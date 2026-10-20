import asyncio
import json
import sys

import pytest

from app import mcp
from app.config import Config
from app.mcp import McpError, McpManager, StdioClient, load_servers, parse_servers
from app.tools import ToolRegistry
from core.models import ToolCall

FAKE_SERVER = r'''
import json, sys

TOOLS = [
    {
        "name": "echo",
        "description": "Echo text back",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "boom",
        "description": "Always fails",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def reply(msg_id, result=None, error=None):
    payload = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None:
        continue
    if method == "initialize":
        reply(msg_id, {"protocolVersion": "2024-11-05", "capabilities": {}})
    elif method == "tools/list":
        reply(msg_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = message.get("params") or {}
        if params.get("name") == "echo":
            text = str((params.get("arguments") or {}).get("text", ""))
            reply(msg_id, {"content": [{"type": "text", "text": text}]})
        else:
            reply(msg_id, {"isError": True, "content": [{"type": "text", "text": "boom"}]})
    else:
        reply(msg_id, error={"code": -32601, "message": "unknown method"})
'''


def make_server(tmp_path, name="fake"):
    script = tmp_path / f"{name}_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return {
        "command": sys.executable,
        "args": [str(script)],
        "timeout": 10,
        "risk": "exec",
    }


@pytest.fixture
def manager(tmp_path):
    mgr = McpManager.from_config({"fake": make_server(tmp_path)}, tmp_path)
    yield mgr
    mgr.close()


def test_parse_servers_rejects_invalid_config():
    with pytest.raises(ValueError):
        parse_servers({"bad": "not-an-object"})
    with pytest.raises(ValueError):
        parse_servers({"bad": {"args": []}})


def test_load_servers_prefers_file_and_unwraps_servers(tmp_path):
    config_file = tmp_path / "mcp.json"
    config_file.write_text(
        json.dumps({"servers": {"a": {"command": "x"}}}), encoding="utf-8"
    )
    assert load_servers(config_file, json.dumps({"b": {"command": "y"}})) == {
        "a": {"command": "x"}
    }
    assert load_servers(None, json.dumps({"b": {"command": "y"}})) == {
        "b": {"command": "y"}
    }
    assert load_servers(None, None) == {}
    with pytest.raises(ValueError):
        load_servers(None, "[]")


def test_stdio_client_initialize_list_and_call(tmp_path):
    server = parse_servers({"fake": make_server(tmp_path)})["fake"]
    client = StdioClient(server, tmp_path)
    try:
        client.start()
        client.initialize()
        tools = client.list_tools()
        assert [t["name"] for t in tools] == ["echo", "boom"]
        ok, text = client.call_tool("echo", {"text": "你好"}, timeout=5)
        assert ok and text == "你好"
        ok, text = client.call_tool("boom", {}, timeout=5)
        assert not ok and text == "boom"
    finally:
        client.close()


def test_manager_registers_prefixed_tools(manager, tmp_path):
    registry = ToolRegistry(tmp_path)
    assert manager.register_into(registry) == ["mcp_fake_boom", "mcp_fake_echo"]
    assert manager.tools["mcp_fake_echo"] == ("fake", "echo")
    assert registry.risk("mcp_fake_echo") == "exec"
    ok, output = asyncio.run(
        registry.execute_async(ToolCall("mcp_fake_echo", {"text": "hi"}, "1"))
    )
    assert ok and output == "hi"


def test_manager_reports_tool_errors_via_exception(manager, tmp_path):
    registry = ToolRegistry(tmp_path)
    manager.register_into(registry)
    ok, output = asyncio.run(
        registry.execute_async(ToolCall("mcp_fake_boom", {}, "1"))
    )
    assert not ok and "McpToolError" in output and "boom" in output


def test_manager_skips_dead_server_and_keeps_others(tmp_path):
    servers = parse_servers(
        {
            "dead": {"command": str(tmp_path / "no-such-binary"), "timeout": 5},
            "fake": make_server(tmp_path),
        }
    )
    mgr = McpManager(servers, tmp_path)
    registry = ToolRegistry(tmp_path)
    try:
        loaded = mgr.register_into(registry)
    finally:
        mgr.close()
    assert loaded == ["mcp_fake_boom", "mcp_fake_echo"]
    assert any(err.startswith("dead:") for err in mgr.errors)
    assert "dead" not in mgr.clients


def test_render_result_truncates_and_handles_structured_content():
    text = mcp._render_result({"content": [{"type": "text", "text": "x" * 30_000}]})
    assert text.endswith("...[truncated]")
    assert len(text) <= mcp.MAX_RESULT_CHARS + len("\n...[truncated]")
    assert mcp._render_result({"structuredContent": {"a": 1}}) == '{"a": 1}'
    assert mcp._render_result({}) == "(empty)"


def test_schema_falls_back_to_object():
    assert mcp._schema({"type": "object", "properties": {}}) == {
        "type": "object",
        "properties": {},
    }
    assert mcp._schema(None) == {"type": "object", "properties": {}}


def test_config_reads_mcp_env(monkeypatch):
    monkeypatch.setenv("VIBE_MCP_CONFIG", "/tmp/mcp.json")
    monkeypatch.setenv("VIBE_MCP_SERVERS", '{"a": {"command": "x"}}')
    config = Config.from_env()
    assert config.mcp_config == "/tmp/mcp.json"
    assert config.mcp_servers == '{"a": {"command": "x"}}'


def test_request_surfaces_server_exit(tmp_path):
    script = tmp_path / "exit_server.py"
    script.write_text("import sys\nsys.stdin.read()\n", encoding="utf-8")
    server = parse_servers({"exit": {"command": sys.executable, "args": [str(script)]}})["exit"]
    client = StdioClient(server, tmp_path)
    try:
        client.start()
        with pytest.raises(McpError):
            client.initialize(timeout=5)
    finally:
        client.close()
