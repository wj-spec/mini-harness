import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import mcp
from app.config import Config
from app.mcp import McpManager, load_servers, parse_servers
from app.policy import ApprovalDecision, ApprovalGate
from app.tools import ToolRegistry
from core.models import ToolCall


FAKE_SERVER = r'''
import json, os, subprocess, sys, time

mode = os.environ.get("FAKE_MCP_MODE", "normal")
if mode == "ignore_term":
    import signal
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
marker = os.environ.get("FAKE_MCP_MARKER")
if marker:
    with open(marker, "w") as f:
        f.write(str(os.getpid()))
if mode == "child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    with open(os.environ["FAKE_MCP_CHILD"], "w") as f:
        f.write(str(child.pid))

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
    {"name": "boom", "description": "Fails", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "structured", "description": "Structured result", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "exit", "description": "Exit now", "inputSchema": {"type": "object", "properties": {}}},
]
if mode == "collision":
    TOOLS = [dict(TOOLS[0], name="a.b"), dict(TOOLS[0], name="a_b")]
if mode in {"sleep", "ignore_term"}:
    TOOLS = [{"name": "sleep", "description": "Wait", "inputSchema": {"type": "object", "properties": {}}}]
if os.environ.get("FAKE_MCP_NAMES"):
    TOOLS = [dict(TOOLS[0], name=name) for name in json.loads(os.environ["FAKE_MCP_NAMES"])]


def send(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(msg_id, result=None, error=None):
    payload = {"jsonrpc": "2.0", "id": msg_id}
    payload["error" if error is not None else "result"] = error if error is not None else result
    send(payload)


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    msg_id = message.get("id")
    if msg_id is None:
        continue
    if method == "initialize":
        reply(msg_id, {
            "protocolVersion": message["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1.0"},
        })
    elif method == "tools/list":
        reply(msg_id, {"tools": TOOLS})
        if mode == "blocked":
            time.sleep(120)
    elif method == "tools/call":
        if os.environ.get("FAKE_MCP_TOOL_MARKER"):
            with open(os.environ["FAKE_MCP_TOOL_MARKER"], "w") as f:
                f.write("called")
        if mode in {"sleep", "ignore_term"}:
            time.sleep(0.5 if mode == "ignore_term" else 120)
            with open(os.environ["FAKE_MCP_COMPLETED"], "w") as f:
                f.write("completed")
        params = message.get("params") or {}
        name = params.get("name")
        if name == "exit":
            sys.exit(7)
        if mode in {"ping_same", "ping_other"}:
            ping_id = msg_id if mode == "ping_same" else "server-ping"
            send({"jsonrpc": "2.0", "id": ping_id, "method": "ping"})
            for ping_line in sys.stdin:
                response = json.loads(ping_line)
                if response.get("id") == ping_id and response.get("result") == {}:
                    break
            else:
                sys.exit(8)
        if name == "boom":
            reply(msg_id, {"isError": True, "content": [{"type": "text", "text": "boom"}]})
        elif name == "structured":
            reply(msg_id, {"content": [], "structuredContent": {"answer": 42}})
        else:
            text = str((params.get("arguments") or {}).get("text", ""))
            reply(msg_id, {"content": [{"type": "text", "text": text}]})
    elif method == "ping":
        reply(msg_id, {})
    else:
        reply(msg_id, error={"code": -32601, "message": "unknown method"})
'''


def make_server(tmp_path, name="fake", mode="normal", timeout=3):
    script = tmp_path / f"{name}_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return {
        "command": sys.executable,
        "args": [str(script)],
        "env": {"FAKE_MCP_MODE": mode},
        "timeout": timeout,
        "risk": "exec",
    }


def allow_start(audit_path=None):
    async def handler(call, preview):
        return ApprovalDecision(True, "once")

    return ApprovalGate(handler=handler, audit_path=audit_path)


def test_parse_servers_rejects_invalid_config():
    with pytest.raises(ValueError):
        parse_servers({"bad": "not-an-object"})
    with pytest.raises(ValueError):
        parse_servers({"bad": {"args": []}})


@pytest.mark.parametrize("override", [
    {"args": "--not-a-list"},
    {"env": "KEY=value"},
    {"env": ["KEY=value"]},
    {"timeout": 0},
    {"timeout": -1},
    {"timeout": float("inf")},
    {"timeout": float("nan")},
    {"risk": "unrestricted"},
    {"risk": []},
    {"risk": {}},
])
def test_parse_servers_rejects_invalid_fields(override):
    with pytest.raises(ValueError):
        parse_servers({"bad": {"command": sys.executable, **override}})


def test_load_servers_prefers_file_and_unwraps_servers(tmp_path):
    config_file = tmp_path / "mcp.json"
    config_file.write_text(json.dumps({"servers": {"a": {"command": "x"}}}), encoding="utf-8")
    assert load_servers(config_file, json.dumps({"b": {"command": "y"}})) == {
        "a": {"command": "x"}
    }
    assert load_servers(None, json.dumps({"b": {"command": "y"}})) == {"b": {"command": "y"}}
    assert load_servers(None, None) == {}
    with pytest.raises(ValueError):
        load_servers(None, "[]")


def test_load_servers_accepts_standard_mcp_servers_wrapper():
    assert load_servers(None, json.dumps({"mcpServers": {"a": {"command": "x"}}})) == {
        "a": {"command": "x"}
    }


def test_start_denied_does_not_launch_server(tmp_path):
    async def scenario():
        marker = tmp_path / "started"
        server = make_server(tmp_path)
        server["env"]["FAKE_MCP_MARKER"] = str(marker)
        manager = McpManager.from_config({"fake": server}, tmp_path)
        gate = ApprovalGate(audit_path=tmp_path / "audit.jsonl")
        try:
            assert await manager.register_into(ToolRegistry(tmp_path), gate) == []
            assert not marker.exists()
            events = [json.loads(line) for line in gate.audit_path.read_text().splitlines()]
            assert events[-1]["decision"] == "deny_no_handler"
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_start_approval_is_scoped_and_audited(tmp_path):
    async def scenario():
        marker = tmp_path / "started"
        server = make_server(tmp_path)
        server["env"].update({"FAKE_MCP_MARKER": str(marker), "SECRET_TOKEN": "not-in-preview"})
        manager = McpManager.from_config({"fake": server}, tmp_path)
        requests = []

        async def handler(call, preview):
            requests.append((call, preview))
            return ApprovalDecision(True, "once")

        gate = ApprovalGate(handler, tmp_path / "audit.jsonl")
        gate.workspace_allowed.add(str(tmp_path.resolve()))
        try:
            loaded = await manager.register_into(ToolRegistry(tmp_path), gate)
            assert "mcp_fake_echo" in loaded and marker.exists()
            assert len(requests) == 1
            call, preview = requests[0]
            assert call.name == "mcp_start"
            assert call.args["command"] == sys.executable
            assert str(tmp_path / "fake_server.py") in call.args["args"]
            assert "not-in-preview" not in preview
            events = [json.loads(line) for line in gate.audit_path.read_text().splitlines()]
            assert events[-1]["tool"] == "mcp_start"
            assert events[-1]["decision"] == "once"
            assert events[-1]["workspace"] == str(tmp_path.resolve())
            assert "not-in-preview" not in gate.audit_path.read_text()
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_cancel_during_second_startup_approval_closes_first_server(tmp_path):
    async def scenario():
        first_pid, second_pid = tmp_path / "first.pid", tmp_path / "second.pid"
        first, second = make_server(tmp_path, "first"), make_server(tmp_path, "second")
        first["env"]["FAKE_MCP_MARKER"] = str(first_pid)
        second["env"]["FAKE_MCP_MARKER"] = str(second_pid)
        manager = McpManager.from_config({"first": first, "second": second}, tmp_path)
        awaiting_second = asyncio.Event()
        never_approve = asyncio.Event()

        async def handler(call, preview):
            if call.args["server"] == "first":
                return ApprovalDecision(True, "once")
            awaiting_second.set()
            await never_approve.wait()
            return ApprovalDecision(True, "once")

        registering = asyncio.create_task(manager.register_into(ToolRegistry(tmp_path), ApprovalGate(handler)))
        try:
            await asyncio.wait_for(awaiting_second.wait(), timeout=2)
            assert first_pid.exists() and not second_pid.exists()
            registering.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(registering, timeout=3)
            assert manager.clients == {}
            async with asyncio.timeout(0.5):
                while _alive(int(first_pid.read_text())):
                    await asyncio.sleep(0.01)
            assert not second_pid.exists()
        finally:
            if not registering.done():
                registering.cancel()
                await asyncio.gather(registering, return_exceptions=True)
            await manager.close()

    asyncio.run(scenario())


def test_agent_requires_separate_approval_for_remote_exec_tool(tmp_path):
    from app.agent import Agent
    from app.providers.fake import FakeProvider
    from core.models import Response

    async def scenario():
        tool_marker = tmp_path / "tool-called"
        server = make_server(tmp_path)
        server["env"]["FAKE_MCP_TOOL_MARKER"] = str(tool_marker)
        manager = McpManager.from_config({"fake": server}, tmp_path)
        requested = []

        async def handler(call, preview):
            requested.append(call.name)
            if call.name == "mcp_start":
                return ApprovalDecision(True, "session")
            return ApprovalDecision(False, "deny")

        gate = ApprovalGate(handler=handler, audit_path=tmp_path / "audit.jsonl")
        registry = ToolRegistry(tmp_path)
        provider = FakeProvider([
            Response(tool_calls=[ToolCall("mcp_fake_echo", {"text": "hi"}, "remote-1")]),
            Response(content="done"),
        ])
        try:
            await manager.register_into(registry, gate)
            result = await Agent(provider, registry, approval=gate).run("call the remote tool")
            assert result.status == "done" and result.text == "done"
            assert requested == ["mcp_start", "mcp_fake_echo"]
            assert not tool_marker.exists()
            tool_message = next(message for message in provider.seen[1] if message.role == "tool")
            assert tool_message.tool_call_id == "remote-1"
            assert "denied_by_user" in tool_message.content
            events = [json.loads(line) for line in gate.audit_path.read_text().splitlines()]
            assert [(event["tool"], event["decision"]) for event in events] == [
                ("mcp_start", "session"), ("mcp_fake_echo", "deny")
            ]
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_agent_cancels_running_mcp_call_with_unknown_execution_outcome(tmp_path):
    from app.agent import Agent
    from app.providers.fake import FakeProvider
    from core.models import Response

    async def scenario():
        started_marker, completed_marker = tmp_path / "tool-started", tmp_path / "tool-completed"
        pid_file = tmp_path / "server.pid"
        server = make_server(tmp_path, mode="sleep", timeout=10)
        server["env"].update({
            "FAKE_MCP_MARKER": str(pid_file),
            "FAKE_MCP_TOOL_MARKER": str(started_marker),
            "FAKE_MCP_COMPLETED": str(completed_marker),
        })
        manager = McpManager.from_config({"fake": server}, tmp_path)
        registry = ToolRegistry(tmp_path)
        gate = allow_start()
        provider = FakeProvider([
            Response(tool_calls=[ToolCall("mcp_fake_sleep", {}, "running-1")]),
            Response(content="must not continue"),
        ])
        agent = Agent(provider, registry, approval=gate)
        cancel = asyncio.Event()
        running = None
        try:
            await manager.register_into(registry, gate)
            running = asyncio.create_task(agent.run("run the remote tool", cancel=cancel))
            async with asyncio.timeout(2):
                while not started_marker.exists():
                    await asyncio.sleep(0.01)
            started = time.monotonic()
            cancel.set()
            result = await asyncio.wait_for(asyncio.shield(running), timeout=1)
            assert result.status == "cancelled"
            assert time.monotonic() - started < 1
            tool_message = next(message for message in agent.history if message.role == "tool")
            assert tool_message.tool_call_id == "running-1"
            assert "cancelled" in tool_message.content
            assert "execution outcome unknown" in tool_message.content
            assert "not executed" not in tool_message.content
            assert provider.i == 1
            async with asyncio.timeout(0.5):
                while _alive(int(pid_file.read_text())):
                    await asyncio.sleep(0.01)
            assert not completed_marker.exists()
        finally:
            if running is not None and not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
            await manager.close()

    asyncio.run(scenario())


def test_timeout_kills_term_ignoring_server_before_returning(tmp_path):
    async def scenario():
        started_marker, completed_marker = tmp_path / "started", tmp_path / "completed"
        pid_file = tmp_path / "server.pid"
        server = make_server(tmp_path, mode="ignore_term", timeout=0.1)
        server["env"].update({
            "FAKE_MCP_MARKER": str(pid_file),
            "FAKE_MCP_TOOL_MARKER": str(started_marker),
            "FAKE_MCP_COMPLETED": str(completed_marker),
        })
        manager = McpManager.from_config({"fake": server}, tmp_path)
        registry = ToolRegistry(tmp_path)
        try:
            await manager.register_into(registry, allow_start())
            ok, output = await registry.execute_async(ToolCall("mcp_fake_sleep", {}, "timeout-1"))
            assert not ok and "timeout" in output.lower()
            assert started_marker.exists()
            assert not _alive(int(pid_file.read_text()))
            await asyncio.sleep(0.7)
            assert not completed_marker.exists()
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_manager_registers_and_calls_async_tools(tmp_path):
    async def scenario():
        manager = McpManager.from_config({"fake": make_server(tmp_path)}, tmp_path)
        registry = ToolRegistry(tmp_path)
        try:
            loaded = await manager.register_into(registry, allow_start())
            assert loaded == ["mcp_fake_boom", "mcp_fake_echo", "mcp_fake_exit", "mcp_fake_structured"]
            assert manager.tools["mcp_fake_echo"] == ("fake", "echo")
            assert registry.risk("mcp_fake_echo") == "exec"
            ok, output = await registry.execute_async(ToolCall("mcp_fake_echo", {"text": "你好"}, "1"))
            assert ok and output == "你好"
            ok, output = await registry.execute_async(ToolCall("mcp_fake_structured", {}, "2"))
            assert ok and json.loads(output) == {"answer": 42}
            ok, output = await registry.execute_async(ToolCall("mcp_fake_boom", {}, "3"))
            assert not ok and "boom" in output
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_remote_arguments_do_not_replace_adapter_state(tmp_path):
    async def scenario():
        manager = McpManager.from_config({"fake": make_server(tmp_path)}, tmp_path)
        registry = ToolRegistry(tmp_path)
        try:
            await manager.register_into(registry, allow_start())
            ok, output = await registry.execute_async(ToolCall("mcp_fake_echo", {
                "text": "hi",
                "_name": "ordinary",
                "_connection": "ordinary",
            }, "1"))
            assert ok and output == "hi"
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_calls_local_fastmcp_package_server(tmp_path):
    async def scenario():
        script = tmp_path / "package_server.py"
        script.write_text(
            "from fastmcp import FastMCP\n"
            "server = FastMCP('package-integration')\n"
            "@server.tool\n"
            "def greet(name: str) -> str:\n"
            "    return 'fastmcp:' + name\n"
            "server.run(transport='stdio', show_banner=False)\n",
            encoding="utf-8",
        )
        package_root = Path(__file__).resolve().parents[1] / ".pylibs"
        manager = McpManager.from_config({"package": {
            "command": sys.executable,
            "args": [str(script)],
            "env": {"PYTHONPATH": str(package_root)},
            "timeout": 10,
        }}, tmp_path)
        registry = ToolRegistry(tmp_path)
        try:
            assert await manager.register_into(registry, allow_start()) == ["mcp_package_greet"]
            ok, output = await registry.execute_async(ToolCall("mcp_package_greet", {"name": "你好"}, "1"))
            assert ok and output == "fastmcp:你好"
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["ping_same", "ping_other"])
def test_server_ping_does_not_complete_tool_early(tmp_path, mode):
    async def scenario():
        manager = McpManager.from_config({"fake": make_server(tmp_path, mode=mode)}, tmp_path)
        registry = ToolRegistry(tmp_path)
        try:
            await manager.register_into(registry, allow_start())
            ok, output = await registry.execute_async(ToolCall("mcp_fake_echo", {"text": "after ping"}, "1"))
            assert ok and output == "after ping"
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_manager_keeps_both_tools_after_name_collision(tmp_path):
    async def scenario():
        manager = McpManager.from_config({"fake": make_server(tmp_path, mode="collision")}, tmp_path)
        registry = ToolRegistry(tmp_path)
        try:
            loaded = await manager.register_into(registry, allow_start())
            assert len(loaded) == 2 and len(set(loaded)) == 2
            assert set(manager.tools.values()) == {("fake", "a.b"), ("fake", "a_b")}
            for index, name in enumerate(loaded):
                ok, output = await registry.execute_async(ToolCall(name, {"text": str(index)}, str(index)))
                assert ok and output == str(index)
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_manager_keeps_both_servers_after_namespace_collision(tmp_path):
    async def scenario():
        manager = McpManager.from_config({
            "a.b": make_server(tmp_path, name="first"),
            "a_b": make_server(tmp_path, name="second"),
        }, tmp_path)
        registry = ToolRegistry(tmp_path)
        try:
            loaded = await manager.register_into(registry, allow_start())
            assert len(loaded) == 8 and len(set(loaded)) == 8
            assert {source for source, name in manager.tools.values()} == {"a.b", "a_b"}
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_server_and_tool_name_boundaries_do_not_overwrite(tmp_path):
    async def scenario():
        first = make_server(tmp_path, name="first")
        first["env"]["FAKE_MCP_NAMES"] = json.dumps(["b_c"])
        second = make_server(tmp_path, name="second")
        second["env"]["FAKE_MCP_NAMES"] = json.dumps(["c"])
        manager = McpManager.from_config({"a": first, "a_b": second}, tmp_path)
        try:
            loaded = await manager.register_into(ToolRegistry(tmp_path), allow_start())
            assert len(loaded) == 2 and len(set(loaded)) == 2
            assert set(manager.tools.values()) == {("a", "b_c"), ("a_b", "c")}
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_exposed_name_is_stable_when_another_server_is_denied(tmp_path):
    async def scenario():
        first, second = make_server(tmp_path, name="first"), make_server(tmp_path, name="second")
        first["env"]["FAKE_MCP_NAMES"] = json.dumps(["b_c"])
        second["env"]["FAKE_MCP_NAMES"] = json.dumps(["c"])
        servers = {"a": first, "a_b": second}
        both = McpManager.from_config(servers, tmp_path)
        try:
            await both.register_into(ToolRegistry(tmp_path), allow_start())
            shared_name = next(name for name, source in both.tools.items() if source == ("a", "b_c"))
        finally:
            await both.close()

        async def allow_first(call, preview):
            return ApprovalDecision(call.args["server"] == "a", "once")

        partial = McpManager.from_config(servers, tmp_path)
        try:
            loaded = await partial.register_into(ToolRegistry(tmp_path), ApprovalGate(allow_first))
            assert loaded == [shared_name]
            assert partial.tools == {shared_name: ("a", "b_c")}
        finally:
            await partial.close()

    asyncio.run(scenario())


def test_manager_skips_dead_server_and_keeps_others(tmp_path):
    async def scenario():
        manager = McpManager.from_config({
            "dead": {"command": str(tmp_path / "no-such-binary"), "timeout": 1},
            "fake": make_server(tmp_path),
        }, tmp_path)
        registry = ToolRegistry(tmp_path)
        try:
            loaded = await manager.register_into(registry, allow_start())
            assert "mcp_fake_echo" in loaded
            assert any(err.startswith("dead:") for err in manager.errors)
            assert "dead" not in manager.clients
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_actual_server_exit_fails_pending_tool_call(tmp_path):
    async def scenario():
        manager = McpManager.from_config({"fake": make_server(tmp_path)}, tmp_path)
        registry = ToolRegistry(tmp_path)
        try:
            await manager.register_into(registry, allow_start())
            started = time.monotonic()
            ok, output = await registry.execute_async(ToolCall("mcp_fake_exit", {}, "1"))
            assert not ok and output
            assert time.monotonic() - started < 2
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_render_result_truncates_and_handles_structured_content():
    text = mcp._render_result({"content": [{"type": "text", "text": "x" * 30_000}]})
    assert text.endswith("...[truncated]")
    assert len(text) <= mcp.MAX_RESULT_CHARS + len("\n...[truncated]")
    assert mcp._render_result({"structuredContent": {"a": 1}}) == '{"a": 1}'
    assert mcp._render_result({}) == "(empty)"


def test_config_reads_mcp_env(monkeypatch):
    monkeypatch.setenv("VIBE_MCP_CONFIG", "/tmp/mcp.json")
    monkeypatch.setenv("VIBE_MCP_SERVERS", '{"a": {"command": "x"}}')
    config = Config.from_env()
    assert config.mcp_config == "/tmp/mcp.json"
    assert config.mcp_servers == '{"a": {"command": "x"}}'


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _kill_recorded_processes(tmp_path):
    for file in [tmp_path / "server.pid", tmp_path / "child.pid"]:
        if file.exists():
            pid = int(file.read_text())
            if _alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def _bounded_scenario(tmp_path, scenario):
    runner = (
        "import runpy, sys; from pathlib import Path; "
        "ns = runpy.run_path(sys.argv[1]); "
        "ns[sys.argv[2]](Path(sys.argv[3]))"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", runner, str(Path(__file__).resolve()), scenario, str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        try:
            output, error = process.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            _kill_recorded_processes(tmp_path)
            process.communicate(timeout=2)
            pytest.fail(f"{scenario} hung during tool timeout or shutdown")
        assert process.returncode == 0, output + error
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        _kill_recorded_processes(tmp_path)


def _blocked_pipe_case(tmp_path):
    async def scenario():
        server = make_server(tmp_path, mode="blocked", timeout=0.2)
        server["env"]["FAKE_MCP_MARKER"] = str(tmp_path / "server.pid")
        manager = McpManager.from_config({"fake": server}, tmp_path)
        registry = ToolRegistry(tmp_path)
        try:
            await manager.register_into(registry, allow_start())
            assert "mcp_fake_echo" in registry.names()
            started = time.monotonic()
            ok, output = await registry.execute_async(ToolCall("mcp_fake_echo", {"text": "x" * 2_000_000}, "1"))
            assert not ok and "timeout" in output.lower()
            assert time.monotonic() - started < 2
        finally:
            await asyncio.wait_for(manager.close(), timeout=5)

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="stdio process groups use POSIX")
def test_blocked_stdin_times_out_and_close_finishes(tmp_path):
    _bounded_scenario(tmp_path, "_blocked_pipe_case")


def _child_cleanup_case(tmp_path):
    async def scenario():
        server = make_server(tmp_path, mode="child")
        server["env"].update({
            "FAKE_MCP_MARKER": str(tmp_path / "server.pid"),
            "FAKE_MCP_CHILD": str(tmp_path / "child.pid"),
        })
        manager = McpManager.from_config({"fake": server}, tmp_path)
        try:
            loaded = await manager.register_into(ToolRegistry(tmp_path), allow_start())
            assert "mcp_fake_echo" in loaded
            assert _alive(int((tmp_path / "child.pid").read_text()))
        finally:
            await asyncio.wait_for(manager.close(), timeout=5)
        for _ in range(50):
            if not _alive(int((tmp_path / "child.pid").read_text())):
                break
            await asyncio.sleep(0.02)
        assert not _alive(int((tmp_path / "child.pid").read_text()))
        assert not _alive(int((tmp_path / "server.pid").read_text()))

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="stdio process groups use POSIX")
def test_close_cleans_up_server_and_descendant(tmp_path):
    _bounded_scenario(tmp_path, "_child_cleanup_case")


def test_parse_servers_supports_tool_allowlist_and_risk_override():
    servers = parse_servers({"browser": {
        "command": "npx", "args": ["@playwright/mcp@x"],
        "allowed_tools": ["browser_snapshot", "browser_navigate", "browser_snapshot"],
        "tool_risks": {"browser_navigate": "write"},
    }})
    server = servers["browser"]
    assert server.allowed_tools == ("browser_snapshot", "browser_navigate")
    assert server.tool_risks == {"browser_navigate": "write"}
    assert server.allowed_origins == ()


def test_parse_servers_rejects_invalid_tool_policy():
    with pytest.raises(ValueError):
        parse_servers({"browser": {"command": "x", "allowed_tools": "browser_snapshot"}})
    with pytest.raises(ValueError):
        parse_servers({"browser": {"command": "x", "allowed_tools": [1]}})
    with pytest.raises(ValueError):
        parse_servers({"browser": {"command": "x", "tool_risks": {"a": "root"}}})
    with pytest.raises(ValueError):
        parse_servers({"browser": {"command": "x", "allowed_tools": ["a"], "tool_risks": {"b": "read"}}})


def test_mcp_registers_only_allowed_tools_and_applies_risk_override(tmp_path):
    async def scenario():
        server = make_server(tmp_path)
        server["risk"] = "read"
        server["env"]["FAKE_MCP_NAMES"] = json.dumps([
            "browser_navigate", "browser_snapshot", "browser_click",
        ])
        server["allowed_tools"] = ["browser_navigate", "browser_snapshot"]
        server["tool_risks"] = {"browser_navigate": "write"}
        manager = McpManager.from_config({"browser": server}, tmp_path)
        try:
            registry = ToolRegistry(tmp_path)
            loaded = await manager.register_into(registry, allow_start())
            exposed = {remote: name for name, (_, remote) in manager.tools.items()}
            assert set(exposed) == {"browser_navigate", "browser_snapshot"}
            assert registry.risk(exposed["browser_navigate"]) == "write"
            assert registry.risk(exposed["browser_snapshot"]) == "read"
            assert all("browser_click" not in name for name in loaded)
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_mcp_missing_allowed_tool_fails_closed(tmp_path):
    async def scenario():
        server = make_server(tmp_path)
        server["allowed_tools"] = ["does_not_exist"]
        manager = McpManager.from_config({"fake": server}, tmp_path)
        try:
            loaded = await manager.register_into(ToolRegistry(tmp_path), allow_start())
            assert loaded == []
            assert any("missing allowed MCP tools" in error for error in manager.errors)
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_parse_servers_validates_allowed_origins():
    servers = parse_servers({"browser": {
        "command": "x", "allowed_origins": ["http://localhost:3000/", "https://example.test"],
    }})
    assert servers["browser"].allowed_origins == ("http://localhost:3000", "https://example.test")
    with pytest.raises(ValueError):
        parse_servers({"browser": {"command": "x", "allowed_origins": "http://localhost"}})
    with pytest.raises(ValueError):
        parse_servers({"browser": {"command": "x", "allowed_origins": ["file:///tmp"]}})
    with pytest.raises(ValueError):
        parse_servers({"browser": {"command": "x", "allowed_origins": ["http://localhost/path"]}})


def test_mcp_origin_guard_blocks_disallowed_navigation_before_server_call(tmp_path):
    async def scenario():
        server = make_server(tmp_path)
        server["risk"] = "read"
        marker = tmp_path / "tool-called"
        server["env"].update({
            "FAKE_MCP_NAMES": json.dumps(["browser_navigate", "browser_snapshot"]),
            "FAKE_MCP_TOOL_MARKER": str(marker),
        })
        server["allowed_tools"] = ["browser_navigate", "browser_snapshot"]
        server["tool_risks"] = {"browser_navigate": "write"}
        server["allowed_origins"] = ["http://localhost:3000"]
        manager = McpManager.from_config({"browser": server}, tmp_path)
        try:
            registry = ToolRegistry(tmp_path)
            await manager.register_into(registry, allow_start())
            exposed = {remote: name for name, (_, remote) in manager.tools.items()}
            ok, output = await registry.execute_async(
                ToolCall(exposed["browser_navigate"], {"url": "https://evil.example/"}),
            )
            assert ok is False
            assert "origin not allowed" in output
            assert not marker.exists(), "guard must reject before calling the MCP server"
            ok, output = await registry.execute_async(
                ToolCall(exposed["browser_navigate"], {"url": "http://localhost:3000/app"}),
            )
            assert ok is True
            assert marker.exists()
        finally:
            await manager.close()

    asyncio.run(scenario())
