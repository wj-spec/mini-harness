import asyncio
import json
import stat
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.runtime.mcp_settings import McpSettings, McpSettingsError
from app.runtime.model_settings import ModelSettingsError
from app.runtime.model_config import load_environment
from app.safety.policy import ApprovalGate, ApprovalDecision


def setup(tmp_path, *, file=False, env=None, approve=None):
    calls = []
    async def decide(call, preview):
        calls.append((call.args, preview))
        if approve:
            return await approve(call, preview)
        return ApprovalDecision(True, "once", "test")
    path = tmp_path / ("mcp.json" if file else ".env")
    config = SimpleNamespace(workspace=tmp_path, mcp_config=str(path) if file else "", mcp_servers="")
    agent = SimpleNamespace(approval=ApprovalGate(decide, audit_path=tmp_path / "audit.jsonl"))
    settings = McpSettings(config, agent, path=tmp_path / ".env", environ=env or {})
    return settings, path, calls


def args(settings, **overrides):
    return dict(action="save", name="demo", command="python", args='["server.py"]',
                env='{"TOKEN":"secret-value"}', revision=settings.overview()["revision"], **overrides)


def test_first_save_private_env_preserves_other_settings_and_masks_secrets(tmp_path):
    settings, path, calls = setup(tmp_path)
    path.write_text("# keep\nVIBE_MODEL=example\n")
    asyncio.run(settings.save(args(settings)))
    saved = load_environment(path, {})
    assert saved["VIBE_MODEL"] == "example"
    assert json.loads(saved["VIBE_MCP_SERVERS"])["demo"]["env"]["TOKEN"] == "secret-value"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert settings.config.mcp_servers == saved["VIBE_MCP_SERVERS"]
    view = settings.overview()
    assert view["servers"][0]["env_keys"] == ["TOKEN"]
    assert view["servers"][0]["args_count"] == 1
    for output in (json.dumps(view), json.dumps(calls), (tmp_path / "audit.jsonl").read_text()):
        assert "secret-value" not in output
        assert "server.py" not in output


def test_json_preserves_wrapper_advanced_fields_and_secret_on_blank(tmp_path):
    settings, path, _ = setup(tmp_path, file=True)
    path.write_text(json.dumps({"note": "keep", "mcpServers": {"demo": {
        "command": "python", "args": ["server.py"], "env": {"TOKEN": "secret-value"},
        "risk": "read", "allowed_tools": ["lookup"], "timeout": 15}}}))
    request = args(settings)
    request.update(args="", env="")
    asyncio.run(settings.save(request))
    saved = json.loads(path.read_text())
    assert saved["note"] == "keep"
    assert saved["mcpServers"]["demo"]["env"] == {"TOKEN": "secret-value"}
    assert saved["mcpServers"]["demo"]["allowed_tools"] == ["lookup"]
    assert saved["mcpServers"]["demo"]["risk"] == "read"
    request.update(command="other", revision=settings.overview()["revision"])
    with pytest.raises(McpSettingsError, match="重新填写"):
        asyncio.run(settings.save(request))
    request["env"] = "{}"
    asyncio.run(settings.save(request))
    assert json.loads(path.read_text())["mcpServers"]["demo"]["env"] == {}


def test_conflict_denial_and_changed_during_approval_do_not_overwrite(tmp_path):
    settings, path, _ = setup(tmp_path)
    request = args(settings)
    path.write_text("VIBE_MODEL=new\n")
    with pytest.raises(McpSettingsError, match="变化"):
        asyncio.run(settings.save(request))
    async def deny(call, preview):
        return ApprovalDecision(False, "once", "no")
    settings, path, _ = setup(tmp_path, approve=deny)
    with pytest.raises(ModelSettingsError, match="允许"):
        asyncio.run(settings.save(args(settings)))
    assert path.read_text() == "VIBE_MODEL=new\n"
    async def concurrent(call, preview):
        path.write_text("VIBE_MODEL=newer\n")
        return ApprovalDecision(True, "once", "ok")
    settings, path, _ = setup(tmp_path, approve=concurrent)
    with pytest.raises(McpSettingsError, match="变化"):
        asyncio.run(settings.save(args(settings)))
    assert path.read_text() == "VIBE_MODEL=newer\n"


def test_invalid_json_override_symlink_and_delete(tmp_path):
    settings, path, _ = setup(tmp_path)
    request = args(settings)
    request["env"] = '{"TOKEN": "secret-value", invalid}'
    with pytest.raises(McpSettingsError) as error:
        asyncio.run(settings.save(request))
    assert "secret-value" not in str(error.value)
    assert not path.exists()
    asyncio.run(settings.save(args(settings)))
    asyncio.run(settings.save({"action": "delete", "name": "demo", "revision": settings.overview()["revision"]}))
    assert settings.overview()["servers"] == []
    overridden, _, _ = setup(tmp_path, env={"VIBE_MCP_SERVERS": "{}"})
    assert not overridden.overview()["editable"]
    with pytest.raises(McpSettingsError, match="环境"):
        asyncio.run(overridden.save(args(overridden)))
    target = tmp_path / "target"
    target.write_text("VIBE_MODEL=example\n")
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(McpSettingsError, match="符号链接"):
        asyncio.run(settings.save(args(settings)))
    assert target.read_text() == "VIBE_MODEL=example\n"


def test_saved_config_reloads_real_local_service_and_delete_waits_for_reload(tmp_path, monkeypatch):
    from app.config import Config
    from app.session.store import SessionStore
    from app.stdio import _Connection
    from app.tools.mcp import McpManager
    from core.contracts import ToolCall
    from test_stdio import agent_for
    from test_mcp import make_server, allow_start
    monkeypatch.setenv("VIBE_CONFIG_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("VIBE_SETTINGS_ENV_KEYS", "[]")
    async def scenario():
        agent = agent_for(tmp_path, [])
        agent.approval = allow_start()
        config = Config(workspace=tmp_path)
        manager = McpManager({}, tmp_path)
        events = []
        connection = _Connection(agent, config, SessionStore.new(tmp_path, "fake"), events.append, manager)
        raw = make_server(tmp_path)
        request = {"action": "save", "name": "demo", "command": raw["command"],
                   "args": json.dumps(raw["args"]), "env": '{"TOKEN":"integration-secret"}',
                   "revision": connection.mcp_settings.overview()["revision"]}
        try:
            await connection.command("save", "mcp_config", request)
            assert any(e["type"] == "mcp_config" for e in events)
            assert not manager.clients and "mcp_demo_echo" not in agent.tools.names()
            await connection.command("reload", "mcp_config", {"action": "reload"})
            assert "mcp_demo_echo" in agent.tools.names()
            assert (await agent.tools.execute_async(ToolCall("mcp_demo_echo", {"text": "ok"})))[0]
            await connection.command("delete", "mcp_config", {"action": "delete", "name": "demo",
                "revision": connection.mcp_settings.overview()["revision"]})
            assert "mcp_demo_echo" in agent.tools.names()
            await connection.command("reload-empty", "mcp_config", {"action": "reload"})
            assert not manager.clients and "mcp_demo_echo" not in agent.tools.names()
            assert "integration-secret" not in json.dumps(events)
        finally:
            await manager.close()
    asyncio.run(scenario())


def test_stdio_list_during_run_and_save_busy(tmp_path, monkeypatch):
    from app.config import Config
    from app.runtime.agent import AgentResult
    from test_stdio import Harness, agent_for
    monkeypatch.setenv("VIBE_CONFIG_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("VIBE_SETTINGS_ENV_KEYS", "[]")
    agent = agent_for(tmp_path, [])
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def run(*args, **kwargs):
            started.set()
            await release.wait()
            return AgentResult("ok", 1, "done")
        monkeypatch.setattr(agent, "run", run)
        h = Harness(agent, Config(workspace=tmp_path))
        ready = await h.until("ready")
        assert "mcp-editor" in ready["capabilities"]
        h.send("run", "r", text="wait")
        await started.wait()
        h.send("command", "list", command="mcp_config", args={"action": "list"})
        view = await h.until("mcp_config", "list")
        h.send("command", "save", command="mcp_config", args={"action":"save","name":"demo",
            "command":"python","args":"","env":"","revision":view["revision"]})
        assert (await h.until("error", "save"))["code"] == "busy"
        release.set()
        await h.until("done", "r")
        await h.close()
        assert not (tmp_path / ".env").exists()
    asyncio.run(scenario())


def test_atomic_replace_failure_preserves_original_and_cleans_temporary(tmp_path, monkeypatch):
    from app.runtime import mcp_settings
    settings, path, _ = setup(tmp_path, file=True)
    original = '{"servers":{}}'
    path.write_text(original)
    def fail(*args):
        raise OSError("secret-from-os")
    monkeypatch.setattr(mcp_settings.os, "replace", fail)
    with pytest.raises(McpSettingsError, match="原文件") as error:
        asyncio.run(settings.save(args(settings)))
    assert "secret-from-os" not in str(error.value)
    assert path.read_text() == original
    assert not list(tmp_path.glob(".mcp.*"))
    assert settings.config.mcp_servers == ""


def test_launch_metadata_keeps_dotenv_inline_editable_and_file_priority(tmp_path):
    settings, _, _ = setup(tmp_path, env={"VIBE_MCP_SERVERS":"{}", "VIBE_SETTINGS_ENV_KEYS":"[]"})
    assert settings.overview()["editable"]
    settings, path, _ = setup(tmp_path, file=True, env={"VIBE_MCP_SERVERS":"{}"})
    path.write_text('{"servers":{}}')
    assert settings.overview()["editable"]
    assert settings.overview()["source"] == "JSON 文件"


@pytest.mark.parametrize("name", ["servers", "mcpServers"])
def test_service_name_matching_wrapper_stays_reloadable(tmp_path, name):
    from app.tools.mcp import load_servers
    settings, _, _ = setup(tmp_path)
    request = args(settings)
    request["name"] = name
    asyncio.run(settings.save(request))
    assert settings.overview()["servers"][0]["name"] == name
    assert load_servers(None, settings.config.mcp_servers)[name]["command"] == "python"


def test_advanced_fields_saved_validated_and_cwd_change_requires_environment(tmp_path):
    settings, path, _ = setup(tmp_path, file=True)
    asyncio.run(settings.save(args(settings)))
    advanced = {"cwd":"/tmp", "timeout":12, "risk":"read", "allowed_tools":["echo"],
                "tool_risks":{"echo":"read"}, "allowed_origins":[], "browser_actions":False}
    request = args(settings)
    request.update(args="", env="", advanced=advanced)
    with pytest.raises(McpSettingsError, match="重新填写"):
        asyncio.run(settings.save(request))
    request["env"] = "{}"
    asyncio.run(settings.save(request))
    item = settings.overview()["servers"][0]
    assert item["advanced"] == advanced
    assert json.loads(path.read_text())["demo"]["allowed_tools"] == ["echo"]
    original = path.read_text()
    request.update(revision=settings.revision(), advanced={**advanced,"timeout":0})
    with pytest.raises(McpSettingsError):
        asyncio.run(settings.save(request))
    request["advanced"] = {**advanced,"tool_risks":{"other":"read"}}
    with pytest.raises(McpSettingsError):
        asyncio.run(settings.save(request))
    request["advanced"] = {**advanced,"browser_actions":True}
    with pytest.raises(McpSettingsError):
        asyncio.run(settings.save(request))
    assert path.read_text() == original


def test_overview_shows_changed_and_removed_but_live_service(tmp_path):
    from app.tools.mcp import parse_servers
    settings, path, _ = setup(tmp_path, file=True)
    path.write_text('{"demo":{"command":"python","args":["old"]}}')
    active = parse_servers(json.loads(path.read_text()))["demo"]
    manager = SimpleNamespace(clients={"demo":SimpleNamespace(server=active, broken=None, client=True)})
    assert settings.overview(manager)["servers"][0]["changed"] is False
    path.write_text('{"demo":{"command":"python","args":["new"]}}')
    assert settings.overview(manager)["servers"][0]["changed"] is True
    path.write_text('{}')
    view = settings.overview(manager)["servers"][0]
    assert view["connected"] and not view["configured"]
    assert view["name"] == "demo"
