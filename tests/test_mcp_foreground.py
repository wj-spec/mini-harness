import asyncio
from types import SimpleNamespace

import pytest

from app.safety.policy import ApprovalDecision
from app.tools.mcp import McpError, parse_servers
from core.contracts import ToolCall
from test_stdio_parity import Connection, make_agent


class Manager:
    def __init__(self):
        self.servers = parse_servers({"alpha": {"command": "true"}, "beta": {"command": "true"}})
        self.clients = {}
        self.errors = []
        self.requests = []
        self.closed = False

    async def register_into(self, registry, approval):
        started = []
        for name in list(self.servers):
            decision = await approval.request(ToolCall("mcp_start", {"server": name}),
                                              f"start {name}", registry.root)
            self.requests.append((name, decision.allowed))
            if not decision.allowed:
                self.errors.append(f"{name}: startup denied")
                continue
            self.clients[name] = SimpleNamespace(server=self.servers[name], client=True)
            started.append(name)
        return started

    async def reconfigure(self, servers, registry, approval, only=None):
        names = [only] if only else ["alpha", "beta"]
        for name in names:
            decision = await approval.request(ToolCall("mcp_start", {"server": name}),
                                              f"start {name}", registry.root)
            self.requests.append((name, decision.allowed))
            if not decision.allowed:
                raise McpError("连接未获允许；所选服务保持未连接，其他服务与后台进程不变。")
        self.clients = {name: SimpleNamespace(server=self.servers[name], client=True) for name in names}
        return f"已连接 {', '.join(names)}"

    async def disconnect(self, registry, approval, only=None):
        return "已断开"

    async def close(self):
        self.closed = True


def test_foreground_start_does_not_connect_mcp(tmp_path):
    async def scenario():
        manager = Manager()
        connection = Connection(make_agent(tmp_path), tmp_path, mcp=manager, auto_mcp=False)
        ready = await connection.until("ready")
        assert ready["mcp"] == {"configured": 2, "connected": 0}
        assert (await connection.until("status"))["phase"] == "idle"
        await asyncio.sleep(0.05)
        assert manager.requests == [] and manager.clients == {}
        assert not [event for event in connection.events if event["type"] == "approval_request"]
        await connection.close()
        assert manager.closed

    asyncio.run(scenario())


def test_background_mode_still_connects_at_startup(tmp_path):
    async def scenario():
        manager = Manager()
        connection = Connection(make_agent(tmp_path), tmp_path, mcp=manager, auto_mcp=True)
        await connection.until("ready")
        request = await connection.until("approval_request", "startup")
        assert "start alpha" in request["preview"]
        connection.send("approval", "a1", approval_id=request["approval_id"], decision="once")
        second = await connection.until("approval_request", "startup")
        connection.send("approval", "a2", approval_id=second["approval_id"], decision="once")
        while (await connection.until("status"))["phase"] != "idle":
            pass
        assert manager.requests == [("alpha", True), ("beta", True)]
        await connection.close()

    asyncio.run(scenario())


def test_explicit_connect_approval_and_denial_report_unconnected_state(tmp_path):
    async def scenario():
        manager = Manager()
        connection = Connection(make_agent(tmp_path), tmp_path, mcp=manager, auto_mcp=False)
        await connection.until("ready")
        await connection.until("status")

        connection.send("command", "connect1", command="mcp_config", args={"action": "reload", "name": "alpha"})
        approval = await connection.until("approval_request", "connect1")
        assert approval["approval_id"]
        connection.send("approval", "deny1", approval_id=approval["approval_id"], decision="deny")
        state = await connection.until("mcp_state")
        assert state == {"v": 1, "type": "mcp_state", "id": None, "configured": 2, "connected": 0}
        result = await connection.until("command_result", "connect1")
        assert "连接未获允许" in result["text"]
        assert manager.clients == {}

        connection.send("command", "connect2", command="mcp_config", args={"action": "reload", "name": "alpha"})
        approval = await connection.until("approval_request", "connect2")
        connection.send("approval", "ok", approval_id=approval["approval_id"], decision="once")
        state = await connection.until("mcp_state")
        assert state["connected"] == 1
        result = await connection.until("command_result", "connect2")
        assert "已连接" in result["text"]
        await connection.close()

    asyncio.run(scenario())
