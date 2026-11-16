import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.config import Config
from app.runtime.model_config import load_environment
from app.runtime.model_settings import ModelSettings, ModelSettingsError
from app.safety.policy import ApprovalGate, ApprovalDecision


def settings(tmp_path, allowed=True, env=None):
    async def approve(call, preview):
        assert "secret-value" not in json.dumps(call.args) + preview
        return ApprovalDecision(allowed, "once" if allowed else "deny")
    config = Config(model="old", api_key="secret-value", workspace=tmp_path)
    agent = SimpleNamespace(approval=ApprovalGate(approve, audit_path=tmp_path / "audit.jsonl"))
    return ModelSettings(config, agent, path=tmp_path / ".env", environ=env or {})


def draft(s, **changes):
    return dict(model="new", base_url=s.config.base_url, api_key="", trust_env=False,
                revision=s.overview()["revision"], **changes)


def test_saved_config_is_atomic_private_redacted_and_not_applied(tmp_path):
    s = settings(tmp_path)
    result = asyncio.run(s.save(draft(s)))
    assert result == "已保存；当前模型未切换。"
    assert s.config.model == "old"
    assert "VIBE_MODEL=new" in (tmp_path / ".env").read_text()
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600
    assert "secret-value" not in json.dumps(s.overview())
    assert "secret-value" not in (tmp_path / "audit.jsonl").read_text()


def test_denied_and_stale_save_leave_file_and_client(tmp_path):
    s = settings(tmp_path, allowed=False)
    with pytest.raises(ModelSettingsError, match="未获允许"):
        asyncio.run(s.save(draft(s)))
    assert not s.path.exists()
    s = settings(tmp_path)
    values = draft(s)
    s.path.write_text("VIBE_MODEL=external\n")
    with pytest.raises(ModelSettingsError, match="已变化"):
        asyncio.run(s.save(values))
    assert s.path.read_text() == "VIBE_MODEL=external\n"
    assert s.config.model == "old"


def test_stale_after_approval_and_symlink_are_rejected(tmp_path):
    s = settings(tmp_path)
    async def mutate(call, preview):
        s.path.write_text("VIBE_MODEL=external\n")
        return ApprovalDecision(True, "once")
    s.agent.approval.handler = mutate
    with pytest.raises(ModelSettingsError, match="已变化"):
        asyncio.run(s.save(draft(s)))
    target = tmp_path / "other"
    target.write_text("untouched")
    s.path.unlink()
    s.path.symlink_to(target)
    with pytest.raises(ModelSettingsError):
        asyncio.run(s.save(draft(s)))
    assert target.read_text() == "untouched"


def test_endpoint_change_requires_new_key_and_environment_wins(tmp_path):
    s = settings(tmp_path, env={"VIBE_MODEL": "old"})
    assert s.overview()["sources"]["model"] == "进程环境"
    values = draft(s)
    assert s.effective(values)["model"] == "old"
    values["base_url"] = "https://elsewhere.example/v1"
    with pytest.raises(ModelSettingsError, match="重新填写"):
        s.candidate(values)
    values["api_key"] = "new-key"
    assert s.candidate(values)["api_key"] == "new-key"
    for url in ["file:///tmp/x", "https://user:password@example.com", "https://example.com?key=secret"]:
        values["base_url"] = url
        with pytest.raises(ModelSettingsError):
            s.candidate(values)


def test_probe_sends_no_conversation_and_redacts_provider_errors(tmp_path):
    s = settings(tmp_path)
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(401, text="secret-value upstream failure")
    result = asyncio.run(s.probe(draft(s), transport=httpx.MockTransport(handler)))
    assert "401" in result and "secret-value" not in result
    assert calls == [{"model": "new", "messages": [{"role": "user", "content": "Hi"}],
                      "max_tokens": 1, "stream": False}]
    assert not s.path.exists()


def test_snapshot_never_contains_key_and_rejects_changed_model(tmp_path):
    s = settings(tmp_path)
    snapshot = s.snapshot()
    assert "secret-value" not in json.dumps(snapshot)
    s.check_snapshot(snapshot)
    s.config.model = "different"
    with pytest.raises(ModelSettingsError, match="模型配置"):
        s.check_snapshot(snapshot)


def test_failed_atomic_save_retains_original(tmp_path, monkeypatch):
    s = settings(tmp_path)
    s.path.write_text("VIBE_MODEL=old\n")
    def fail(*args): raise OSError("secret-value")
    monkeypatch.setattr("app.runtime.model_config.os.replace", fail)
    with pytest.raises(ModelSettingsError) as error:
        asyncio.run(s.save(draft(s)))
    assert "secret-value" not in str(error.value)
    assert s.path.read_text() == "VIBE_MODEL=old\n"


def test_apply_swaps_provider_and_summarizer_only_after_validation(tmp_path):
    from app.providers.openai_compat import OpenAICompatProvider
    from core.context import ContextBuilder
    s = settings(tmp_path)
    s.agent.provider = OpenAICompatProvider("old", "secret-value")
    s.agent.context = ContextBuilder()
    s.agent.llm_auditor = None
    message = s.apply(draft(s))
    assert s.config.model == s.agent.provider.model == "new"
    assert s.agent.context.summarizer.model == "new"
    assert "新任务" in message and not s.path.exists()
    assert s.overview()["sources"]["model"] == "本次选择"


def test_stdio_model_read_during_run_and_busy_mutation(tmp_path):
    from test_stdio import Harness, agent_for
    from app.events import TextDelta, StreamDone
    from core.contracts import Response
    class Slow:
        async def stream(self, messages, tools):
            await asyncio.sleep(.15)
            yield TextDelta("done")
            yield StreamDone(Response("done"))
    async def scenario():
        config = Config(workspace=tmp_path)
        agent = agent_for(tmp_path, [])
        agent.provider = Slow()
        h = Harness(agent, config)
        await h.until("ready")
        h.send("run", "r", text="hello")
        await h.until("status", "r")
        h.send("command", "read", command="model", args={"action": "list"})
        overview = await h.until("model_settings", "read")
        assert "api_key" not in overview
        h.send("command", "apply", command="model", args={"action": "apply", "model": "new",
               "base_url": config.base_url, "api_key": "secret-value", "trust_env": False})
        assert (await h.until("error", "apply"))["code"] == "busy"
        await h.until("done", "r")
        await h.close()
    asyncio.run(scenario())


def test_stdio_resume_rejects_changed_snapshot_before_model_call(tmp_path):
    from test_stdio import Harness, agent_for
    async def scenario():
        config = Config(workspace=tmp_path)
        agent = agent_for(tmp_path, ["first", "must-not-run"])
        h = Harness(agent, config)
        await h.until("ready")
        h.send("run", "first", text="hello")
        await h.until("done", "first")
        config.model = "changed"
        h.send("command", "resume", command="resume")
        failure = await h.until("done", "resume")
        assert failure["result"]["stop_reason"] == "resume_unavailable"
        assert "模型配置" in failure["result"]["text"]
        await h.close()
    asyncio.run(scenario())


def test_launcher_style_save_preserves_proxy_and_sources_are_explicit(tmp_path):
    from app.runtime.model_config import save_model_config, load_environment
    path = tmp_path / ".env"
    path.write_text("VIBE_TRUST_ENV=1\nVIBE_MODEL=old\n")
    save_model_config(path, {"VIBE_MODEL": "new", "VIBE_API_KEY": "key"})
    assert load_environment(path, {})["VIBE_TRUST_ENV"] == "1"
    s = settings(tmp_path, env={"VIBE_CONFIG_FILE": str(path), "VIBE_MODEL_ENV_KEYS": "[]", "VIBE_MODEL": "old"})
    assert s.sources()["model"] == ".env"
    s.config.model_cli_fields = ("model",)
    assert s.sources()["model"] == "CLI"


def test_task_snapshot_guard_selects_the_requested_task(tmp_path):
    from test_stdio import agent_for
    from app.stdio import _Connection
    from app.session.store import SessionStore
    config = Config(workspace=tmp_path)
    agent = agent_for(tmp_path, [])
    session = SessionStore.new(tmp_path, config.model, purpose="test")
    conn = _Connection(agent, config, session, lambda event: None)
    original = conn.model_settings.snapshot()
    session.append_trace({"type": "run_start", "task_id": "task-a", "model_config": original})
    config.model = "new"
    session.append_trace({"type": "run_start", "task_id": "task-b", "model_config": conn.model_settings.snapshot()})
    conn.check_task_model("task-b")
    with pytest.raises(ModelSettingsError):
        conn.check_task_model("task-a")


def test_explicit_probe_success_and_denial_without_network(tmp_path):
    s = settings(tmp_path, allowed=False)
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "H"}}]})
    with pytest.raises(ModelSettingsError):
        asyncio.run(s.probe(draft(s), transport=httpx.MockTransport(handler)))
    assert not calls
    s = settings(tmp_path)
    assert "检测通过" in asyncio.run(s.probe(draft(s), transport=httpx.MockTransport(handler)))
    assert len(calls) == 1
    assert not s.path.exists()


def test_probe_uses_network_tier_and_respects_project_denial(tmp_path):
    from test_stdio import agent_for
    from app.safety.permission_policy import PermissionPolicy, Rule
    agent = agent_for(tmp_path, [])
    from core.contracts import ToolCall
    call = ToolCall("model_connection_test", {})
    assert agent._tool_tier(call) == "network"
    agent.permission_policy = PermissionPolicy((Rule(source="project", tool="model_connection_test", decision="deny"),))
    agent.approval.policy = agent.permission_policy
    s = ModelSettings(Config(workspace=tmp_path, api_key="secret-value"), agent, path=tmp_path / ".env", environ={})
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200)
    with pytest.raises(ModelSettingsError, match="未获允许"):
        asyncio.run(s.probe(draft(s), transport=httpx.MockTransport(handler)))
    assert not calls


def test_save_and_probe_work_without_agent_and_apply_is_refused(tmp_path):
    approvals = []

    async def approve(call, preview):
        approvals.append(call.name)
        return ApprovalDecision(True, "once")

    gate = ApprovalGate(approve, audit_path=tmp_path / "audit.jsonl")
    config = Config(model="old", api_key="", workspace=tmp_path)
    s = ModelSettings(config, path=tmp_path / ".env", environ={}, approval=gate)
    assert s.overview()["key_present"] is False
    values = draft(s)
    values["api_key"] = "new"
    assert "已保存" in asyncio.run(s.save(values))
    assert approvals == ["write_file"]
    assert load_environment(tmp_path / ".env", {})["VIBE_API_KEY"] == "new"
    with pytest.raises(ModelSettingsError, match="进入项目后生效"):
        s.apply(values)


def test_placeholder_key_is_not_configuration_and_is_never_reused(tmp_path):
    from app.runtime.model_config import configured_key, is_placeholder_key, usable_key
    assert is_placeholder_key("sk-your-key-here") and not usable_key("sk-your-key-here")
    assert configured_key({"VIBE_API_KEY": "sk-your-key-here", "OPENAI_API_KEY": "sk-real"}) == "sk-real"
    config = Config(model="old", api_key="sk-your-key-here", workspace=tmp_path)
    s = ModelSettings(config, path=tmp_path / ".env", environ={}, approval=ApprovalGate())
    assert s.overview()["key_present"] is False
    values = draft(s)
    values["api_key"] = "sk-your-key-here"
    with pytest.raises(ModelSettingsError, match="真实 API Key"):
        s.candidate(values)


def test_placeholder_lists_stay_in_sync_with_startup_checks():
    from app.runtime.model_config import API_KEY_PLACEHOLDERS
    from scripts.doctor import API_KEY_PLACEHOLDERS as doctor_placeholders
    assert API_KEY_PLACEHOLDERS == doctor_placeholders


def test_candidate_reports_the_field_for_inline_errors(tmp_path):
    s = settings(tmp_path)
    cases = [("model", " "), ("base_url", "ftp://example.com"), ("api_key", "sk-your-key-here")]
    for field, value in cases:
        values = draft(s)
        values[field] = value
        with pytest.raises(ModelSettingsError) as error:
            s.candidate(values)
        assert error.value.field == field, field


def test_default_model_constants_match_startup_checks():
    from app.runtime.model_config import DEFAULT_BASE_URL, DEFAULT_MODEL
    from scripts.doctor import DEFAULT_BASE_URL as doctor_base, DEFAULT_MODEL as doctor_model
    assert (DEFAULT_BASE_URL, DEFAULT_MODEL) == (doctor_base, doctor_model)


def test_probe_http_errors_explain_only_actionable_causes(tmp_path):
    s = settings(tmp_path)
    for status, expected in [(401, "凭据"), (403, "权限"), (404, "模型"), (429, "稍后重试")]:
        def handler(request, code=status):
            return httpx.Response(code, text="upstream-secret")
        result = asyncio.run(s.probe(draft(s), transport=httpx.MockTransport(handler)))
        assert f"HTTP {status}" in result and expected in result
        assert "upstream-secret" not in result
