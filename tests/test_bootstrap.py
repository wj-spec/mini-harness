from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.runtime.bootstrap import Bootstrap, MAX_FRAME_BYTES, serve
from app.config import Config
from app.runtime.model_config import load_environment, save_model_config


class Harness:
    def __init__(self, config, *, environ=None, exec_=None):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.events = []
        self.executed = []
        if environ is None:
            environ = {"VIBE_CONFIG_FILE": str(Path(config.workspace) / ".env")}

        async def requests():
            while (frame := await self.incoming.get()) is not None:
                yield frame

        def emit(event):
            self.events.append(event)
            self.outgoing.put_nowait(event)

        def fake_exec(program, argv, env):
            self.executed.append((program, argv, dict(env)))

        self.task = asyncio.create_task(serve(
            config, requests=requests(), emit=emit, environ=environ,
            exec_=exec_ or fake_exec,
        ))

    def send(self, kind, request_id, **data):
        self.incoming.put_nowait(json.dumps({"v": 1, "type": kind, "id": request_id, **data}))

    async def until(self, kind, request_id=None):
        async with asyncio.timeout(3):
            while True:
                event = await self.outgoing.get()
                if event["type"] == kind and (request_id is None or event["id"] == request_id):
                    return event

    async def close(self):
        self.incoming.put_nowait(None)
        await asyncio.wait_for(self.task, 3)


def config_for(tmp_path, **overrides):
    values = {"workspace": tmp_path, "api_key": "", "model": "deepseek-chat",
              "base_url": "https://api.deepseek.com/v1"}
    values.update(overrides)
    return Config(**values)


def draft(tmp_path, **changes):
    import hashlib
    path = tmp_path / ".env"
    revision = hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()
    values = {
        "action": "save", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-bootstrap-test", "trust_env": False, "revision": revision,
    }
    values.update(changes)
    return values


def test_bootstrap_ready_has_no_run_capability_and_model_overview(tmp_path):
    async def scenario():
        h = Harness(config_for(tmp_path), environ={"VIBE_CONFIG_FILE": str(tmp_path / ".env")})
        ready = await h.until("ready")
        assert ready["capabilities"] == ["config-bootstrap", "model-settings", "commands"]
        assert "run" not in ready["capabilities"]
        assert ready["workspace"] == str(tmp_path.resolve())
        assert ready["bootstrap"]["configured"] is False
        assert ready["bootstrap"]["reason"] == "missing_key"
        assert ready["bootstrap"]["config_path"] == str(tmp_path / ".env")
        assert ready["bootstrap"]["defaults"] == {"base_url": "https://api.deepseek.com/v1",
                                                  "model": "deepseek-chat"}
        await h.until("status")
        h.send("command", "list", command="model", args={"action": "list"})
        overview = await h.until("model_settings", "list")
        assert overview["session_id"] == ""
        assert overview["key_present"] is False
        assert "api_key" not in overview
        assert not (tmp_path / ".vibe" / "sessions").exists()
        await h.close()
        assert h.events[-1]["type"] == "closed"

    asyncio.run(scenario())


@pytest.mark.parametrize("kind,extra", [
    ("run", {"text": "hello"}),
    ("steer", {"target": "run-1", "text": "hint"}),
    ("command", {"command": "resume"}),
])
def test_bootstrap_rejects_run_and_unavailable_commands(tmp_path, kind, extra):
    async def scenario():
        h = Harness(config_for(tmp_path))
        await h.until("ready")
        h.send(kind, "rejected", **extra)
        failure = await h.until("error", "rejected")
        assert failure["code"] == "bootstrap_unavailable"
        assert "项目" in failure["message"]
        assert not [event for event in h.events if event["type"] == "approval_request"]
        assert not (tmp_path / ".vibe" / "sessions").exists()
        await h.close()

    asyncio.run(scenario())


def test_save_goes_through_approval_and_writes_private_env(tmp_path):
    async def scenario():
        env = {"VIBE_CONFIG_FILE": str(tmp_path / ".env")}
        h = Harness(config_for(tmp_path), environ=env)
        await h.until("ready")
        h.send("command", "save", command="model", args=draft(tmp_path))
        approval = await h.until("approval_request", "bootstrap")
        assert approval["decisions"] == ["once", "deny"]
        assert "sk-bootstrap-test" not in json.dumps(approval, ensure_ascii=False)
        assert "write_file" == approval["call"]["name"]
        h.send("approval", "ok", approval_id=approval["approval_id"], decision="once")
        saved = await h.until("model_settings", "save")
        assert "已保存" in saved["message"]
        assert saved["key_present"] is True
        assert saved["sources"]["api_key"] == ".env"
        assert load_environment(tmp_path / ".env", {})["VIBE_API_KEY"] == "sk-bootstrap-test"
        assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600
        assert "sk-bootstrap-test" not in json.dumps(h.events, ensure_ascii=False)
        await h.close()

    asyncio.run(scenario())


def test_denied_save_keeps_file_and_reports_denial(tmp_path):
    async def scenario():
        h = Harness(config_for(tmp_path))
        await h.until("ready")
        h.send("command", "save", command="model", args=draft(tmp_path))
        approval = await h.until("approval_request", "bootstrap")
        h.send("approval", "no", approval_id=approval["approval_id"], decision="deny")
        failure = await h.until("error", "save")
        assert failure["code"] == "model_settings_error"
        assert "未获允许" in failure["message"]
        assert not (tmp_path / ".env").exists()
        await h.close()

    asyncio.run(scenario())


def test_activation_forwards_saved_environment_to_real_backend(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    save_model_config(path, {"VIBE_PROVIDER": "openai", "VIBE_MODEL": "saved-model",
                             "VIBE_BASE_URL": "https://example.com/v1", "VIBE_API_KEY": "sk-saved"})

    async def scenario():
        monkeypatch.setattr("sys.orig_argv", ["/usr/bin/python3", "-m", "app", "--stdio", "--workspace", str(tmp_path)])
        h = Harness(config_for(tmp_path), environ={"VIBE_CONFIG_FILE": str(path)})
        await h.until("ready")
        h.send("command", "activate", command="bootstrap", args={"action": "activate"})
        result = await h.until("bootstrap_activation", "activate")
        assert "正在初始化" in result["text"]
        program, argv, env = h.executed[0]
        assert program == "/usr/bin/python3"
        assert argv == ["/usr/bin/python3", "-m", "app", "--stdio", "--workspace", str(tmp_path)]
        assert env["VIBE_API_KEY"] == "sk-saved"
        assert env["VIBE_MODEL"] == "saved-model"
        assert env["VIBE_CONFIG_FILE"] == str(path)
        await h.close()

    asyncio.run(scenario())


def test_activation_ignores_placeholder_shell_key_and_requires_saved_key(tmp_path):
    path = tmp_path / ".env"

    async def scenario():
        h = Harness(config_for(tmp_path, api_key="sk-your-key-here"),
                    environ={"VIBE_CONFIG_FILE": str(path), "VIBE_API_KEY": "sk-your-key-here"})
        ready = await h.until("ready")
        assert ready["bootstrap"]["reason"] == "placeholder_key"
        h.send("command", "activate", command="bootstrap", args={"action": "activate"})
        failure = await h.until("error", "activate")
        assert failure["code"] == "model_settings_error"
        assert "API Key" in failure["message"]
        await h.close()

    asyncio.run(scenario())


def test_test_action_needs_approval_and_reports_result(tmp_path, monkeypatch):
    import httpx

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            return httpx.Response(200, json={"choices": [{"message": {"content": "H"}}]})

    monkeypatch.setattr("app.runtime.model_settings.httpx.AsyncClient", lambda **kwargs: FakeClient())

    async def scenario():
        h = Harness(config_for(tmp_path))
        await h.until("ready")
        h.send("command", "probe", command="model", args=draft(tmp_path, action="test"))
        approval = await h.until("approval_request", "bootstrap")
        assert "连接检测" in approval["preview"]
        h.send("approval", "ok", approval_id=approval["approval_id"], decision="once")
        result = await h.until("model_settings", "probe")
        assert "检测通过" in result["message"]
        await h.close()

    asyncio.run(scenario())


def test_bootstrap_rejects_apply_and_duplicate_ids(tmp_path):
    async def scenario():
        h = Harness(config_for(tmp_path))
        await h.until("ready")
        h.send("command", "apply", command="model", args={**draft(tmp_path), "action": "apply"})
        failure = await h.until("error", "apply")
        assert "进入项目后生效" in failure["message"]
        h.incoming.put_nowait(json.dumps({"v": 1, "type": "command", "id": "list", "command": "model",
                                          "args": {"action": "list"}}))
        h.incoming.put_nowait(json.dumps({"v": 1, "type": "command", "id": "list", "command": "model",
                                          "args": {"action": "list"}}))
        await h.until("model_settings", "list")
        failure = await h.until("error", "list")
        assert failure["code"] == "duplicate_id"
        await h.close()

    asyncio.run(scenario())


def test_placeholder_shell_key_yields_to_saved_file_key_in_sources_and_activation(tmp_path):
    path = tmp_path / ".env"
    save_model_config(path, {"VIBE_MODEL": "saved-model", "VIBE_BASE_URL": "https://example.com/v1",
                             "VIBE_API_KEY": "sk-saved"})
    base_env = {"VIBE_CONFIG_FILE": str(path), "VIBE_API_KEY": "sk-your-key-here",
                "VIBE_MODEL_ENV_KEYS": '["VIBE_API_KEY"]'}

    async def scenario():
        h = Harness(Config(workspace=tmp_path, api_key="sk-your-key-here"), environ=base_env)
        await h.until("ready")
        h.send("command", "list", command="model", args={"action": "list"})
        overview = await h.until("model_settings", "list")
        assert overview["key_present"] is False
        h.send("command", "activate", command="bootstrap", args={"action": "activate"})
        await h.until("bootstrap_activation", "activate")
        _, _, env = h.executed[0]
        assert env["VIBE_API_KEY"] == "sk-saved"
        await h.close()

    asyncio.run(scenario())


def test_default_config_path_and_frame_limit_match_stdio(tmp_path):
    from app.stdio import MAX_FRAME_BYTES as stdio_limit
    state = Bootstrap(Config(workspace=tmp_path), lambda event: None, environ={})
    from app.runtime.user_config import config_path
    assert state.config_path() == config_path({})
    assert MAX_FRAME_BYTES == stdio_limit


def test_cancel_without_matching_operation_is_reported(tmp_path):
    async def scenario():
        h = Harness(config_for(tmp_path))
        await h.until("ready")
        h.send("cancel", "c0", target="missing")
        failure = await h.until("error", "c0")
        assert failure["code"] == "cancel_mismatch"
        await h.close()

    asyncio.run(scenario())


def test_cancel_aborts_detection_and_keeps_saved_config(tmp_path, monkeypatch):
    import httpx

    class SlowClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            await asyncio.sleep(5)
            return httpx.Response(200, json={"choices": [{"message": {"content": "H"}}]})

    monkeypatch.setattr("app.runtime.model_settings.httpx.AsyncClient", lambda **kwargs: SlowClient())

    async def scenario():
        h = Harness(config_for(tmp_path))
        await h.until("ready")
        h.send("command", "probe", command="model", args=draft(tmp_path, action="test"))
        approval = await h.until("approval_request", "bootstrap")
        h.send("approval", "ok", approval_id=approval["approval_id"], decision="once")
        await asyncio.sleep(0.05)
        h.send("cancel", "c1", target="probe")
        failure = await h.until("error", "probe")
        assert failure["code"] == "cancelled"
        assert "已保存的配置保留" in failure["message"]
        h.send("command", "list", command="model", args={"action": "list"})
        assert (await h.until("model_settings", "list"))["key_present"] is False
        await h.close()

    asyncio.run(scenario())


def test_invalid_save_reports_the_field_without_requesting_approval(tmp_path):
    async def scenario():
        h = Harness(config_for(tmp_path))
        await h.until("ready")
        h.send("command", "save", command="model", args=draft(tmp_path, api_key="sk-your-key-here"))
        failure = await h.until("error", "save")
        assert failure["code"] == "model_settings_error"
        assert failure["field"] == "api_key"
        assert not [event for event in h.events if event["type"] == "approval_request"]
        await h.close()

    asyncio.run(scenario())


def test_config_problem_page_blocks_save_and_activation(tmp_path):
    async def scenario():
        env = {"VIBE_CONFIG_FILE": str(tmp_path / ".env"),
               "VIBE_CONFIG_PROBLEM": "配置文件无法解析：.env 第 1 行引号不完整。 原文件未修改；请修正后重新启动。",
               "VIBE_BOOTSTRAP": "1"}
        h = Harness(config_for(tmp_path), environ=env)
        ready = await h.until("ready")
        assert ready["bootstrap"]["problem"].startswith("配置文件无法解析")
        h.send("command", "save", command="model", args=draft(tmp_path))
        failure = await h.until("error", "save")
        assert "不会覆盖原文件" in failure["message"]
        assert not [event for event in h.events if event["type"] == "approval_request"]
        h.send("command", "enter", command="bootstrap", args={"action": "activate"})
        failure = await h.until("error", "enter")
        assert "不会覆盖原文件" in failure["message"]
        assert not (tmp_path / ".env").exists()
        await h.close()

    asyncio.run(scenario())
