import asyncio
import json

import pytest

from app.config import Config
from app.runtime.limit_settings import LimitSettings, LimitSettingsError
from app.runtime.model_config import load_environment
from app.safety.policy import ApprovalDecision


def settings(tmp_path, allowed=True, environ=None):
    from test_stdio import agent_for
    agent = agent_for(tmp_path, ["first", "second", "third"])
    async def approve(call, preview):
        return ApprovalDecision(allowed, "once" if allowed else "deny")
    agent.approval.handler = approve
    cfg = Config(workspace=tmp_path, max_steps=6, max_run_seconds=100, max_run_tokens=8000)
    return LimitSettings(cfg, agent, path=tmp_path / ".env", environ=environ or {})


def test_save_only_limits_preserves_model_proxy_and_private_mode(tmp_path):
    s = settings(tmp_path)
    s.path.write_text("VIBE_API_KEY=secret\nVIBE_TRUST_ENV=1\nVIBE_MAX_STEPS=9\n")
    values = {**s.values(), "max_steps": 12}
    asyncio.run(s.save({"limits": values, "revision": s.revision()}))
    saved = load_environment(s.path, {})
    assert saved["VIBE_API_KEY"] == "secret" and saved["VIBE_TRUST_ENV"] == "1"
    assert saved["VIBE_MAX_STEPS"] == "12"
    assert s.values()["max_steps"] == 6
    assert s.path.stat().st_mode & 0o777 == 0o600
    assert "secret" not in json.dumps(s.overview())


@pytest.mark.parametrize("key,value", [
    ("max_steps", True), ("max_steps", 1.5), ("max_run_tokens", -1),
    ("max_run_seconds", 0), ("max_run_cost_usd", float("inf")), ("max_run_tokens", "100"),
])
def test_invalid_limits_rejected_without_changes(tmp_path, key, value):
    s = settings(tmp_path)
    with pytest.raises(LimitSettingsError):
        s.apply({"limits": {**s.values(), key: value}})
    assert s.config.max_steps == 6


def test_denial_and_changes_during_approval_preserve_file(tmp_path):
    s = settings(tmp_path, False)
    with pytest.raises(ValueError):
        asyncio.run(s.save({"limits": s.values(), "revision": s.revision()}))
    assert not s.path.exists()
    s = settings(tmp_path)
    async def approve(call, preview):
        s.path.write_text("VIBE_MAX_STEPS=33\n")
        return ApprovalDecision(True, "once")
    s.agent.approval.handler = approve
    with pytest.raises(LimitSettingsError, match="已变化"):
        asyncio.run(s.save({"limits": s.values(), "revision": s.revision()}))
    assert s.path.read_text() == "VIBE_MAX_STEPS=33\n"


def test_sources_overrides_and_run_scope_restored_on_exception(tmp_path):
    s = settings(tmp_path, environ={"VIBE_MAX_STEPS": "6"})
    s.config.limit_cli_fields = ("max_run_seconds",)
    s.apply({"limits": {**s.values(), "max_steps": 20, "max_run_seconds": 300, "max_run_tokens": 9000}})
    assert s.values()["max_steps"] == 6 and s.values()["max_run_seconds"] == 100
    assert s.values()["max_run_tokens"] == 9000
    before = s.agent.max_run_tokens
    with pytest.raises(RuntimeError):
        with s.for_run({**s.values(), "max_run_tokens": 300}):
            assert s.agent.max_run_tokens == 300
            raise RuntimeError()
    assert s.agent.max_run_tokens == before


def test_snapshot_selection_is_task_specific_and_first_snapshot_wins(tmp_path):
    s = settings(tmp_path)
    path = tmp_path / "trace.jsonl"
    old = s.values()
    new = {**old, "max_steps": 99}
    path.write_text("\n".join(json.dumps(x) for x in [
        {"type": "run_start", "task_id": "a", "run_limits": old},
        {"type": "run_end", "task_id": "b", "run_limits": new},
        {"type": "run_end", "task_id": "a", "run_limits": new}]))
    assert s.task_snapshot(path, "a") == old
    assert s.task_snapshot(path, "b") == new

def test_new_defaults_do_not_change_resume_snapshot(tmp_path, monkeypatch):
    from test_stdio import Harness, agent_for
    from app.session.store import SessionStore
    agent = agent_for(tmp_path, ["one", "two", "three"])
    agent.max_steps = 6
    seen = []
    original = agent.run
    async def observe(*args, **kwargs):
        seen.append(agent.max_steps)
        return await original(*args, **kwargs)
    monkeypatch.setattr(agent, "run", observe)
    session = SessionStore.new(tmp_path, "fake")
    async def scenario():
        h = Harness(agent, Config(workspace=tmp_path, max_steps=6), session)
        await h.until("ready")
        h.send("run", "r1", text="first")
        await h.until("done", "r1")
        h.send("command", "s", command="settings", args={"action": "list"})
        view = await h.until("settings_overview", "s")
        h.send("command", "a", command="settings",
               args={"action": "apply", "limits": {**view["limits"], "max_steps": 10}})
        await h.until("command_result", "a")
        h.send("command", "r2", command="resume")
        await h.until("done", "r2")
        h.send("run", "r3", text="new")
        await h.until("done", "r3")
        await h.close()
    asyncio.run(scenario())
    assert seen == [6, 6, 10]
    assert agent.max_steps == 6
    assert session.latest_run()["run_limits"]["max_steps"] == 10


def test_settings_read_during_run_but_apply_is_busy(tmp_path, monkeypatch):
    from test_stdio import Harness, agent_for
    from app.runtime.agent import AgentResult
    agent = agent_for(tmp_path, [])
    async def scenario():
        release = asyncio.Event()
        started = asyncio.Event()
        async def run(*args, **kwargs):
            started.set()
            await release.wait()
            return AgentResult("ok", 1, "done")
        monkeypatch.setattr(agent, "run", run)
        h = Harness(agent, Config(workspace=tmp_path))
        await h.until("ready")
        h.send("run", "r", text="wait")
        await started.wait()
        h.send("command", "s", command="settings", args={"action": "list"})
        view = await h.until("settings_overview", "s")
        h.send("command", "a", command="settings", args={"action": "apply", "limits": view["limits"]})
        error = await h.until("error", "a")
        assert error["code"] == "busy"
        release.set()
        await h.until("done", "r")
        await h.close()
    asyncio.run(scenario())


def test_corrupt_resume_limits_stops_cleanly_and_next_run_works(tmp_path):
    from test_stdio import Harness, agent_for
    from app.session.store import SessionStore
    agent = agent_for(tmp_path, ["first", "next"])
    session = SessionStore.new(tmp_path, "fake")
    async def scenario():
        h = Harness(agent, Config(workspace=tmp_path), session)
        await h.until("ready")
        h.send("run", "r1", text="first")
        await h.until("done", "r1")
        latest = session.latest_run()
        latest["run_limits"] = {"max_steps": "corrupt"}
        session.append_trace(latest)
        h.send("command", "r2", command="resume")
        done = await h.until("done", "r2")
        assert done["result"]["status"] == "stopped"
        h.send("run", "r3", text="new")
        assert (await h.until("done", "r3"))["result"]["status"] == "done"
        await h.close()
    asyncio.run(scenario())


def test_goal_continuation_uses_original_limits_after_other_task(tmp_path, monkeypatch):
    from test_stdio import Harness, agent_for
    from app.session.store import SessionStore
    from app.runtime.agent import AgentResult
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    agent = agent_for(tmp_path, [])
    agent.max_steps = 6
    observed = []
    async def stopped(*args, **kwargs):
        observed.append(agent.max_steps)
        return AgentResult("continue", 1, "stopped", stop_reason="max_steps")
    monkeypatch.setattr(agent, "run", stopped)
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    async def scenario():
        h = Harness(agent, Config(workspace=tmp_path, max_steps=6), session)
        await h.until("ready")
        h.send("run", "r1", text="goal", task={"kind": "goal", "objective": "goal"})
        task_id = (await h.until("task_state", "r1"))["task"]["task_id"]
        await h.until("done", "r1")
        h.send("command", "s", command="settings", args={"action": "list"})
        view = await h.until("settings_overview", "s")
        h.send("command", "a", command="settings",
               args={"action": "apply", "limits": {**view["limits"], "max_steps": 10}})
        await h.until("command_result", "a")
        h.send("run", "r2", text="other")
        await h.until("done", "r2")
        h.send("run", "r3", text="continue", task={"kind": "goal", "objective": "goal", "task_id": task_id})
        done = await h.until("done", "r3")
        assert done["result"]["stop_reason"] == "max_steps"
        await h.close()
    asyncio.run(scenario())
    assert observed == [6, 10, 6]
