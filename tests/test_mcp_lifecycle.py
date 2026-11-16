import asyncio
import pytest
from app.tools.mcp import McpManager, parse_servers, McpError
from app.tools.registry import ToolRegistry
from core.contracts import ToolCall
from test_mcp import make_server, allow_start
from app.safety.policy import ApprovalGate, ApprovalDecision

def test_reload_keeps_old_connection_on_failure_then_replaces_tools(tmp_path):
    async def scenario():
        registry=ToolRegistry(tmp_path)
        raw={"fake":make_server(tmp_path)}
        manager=McpManager.from_config(raw,tmp_path)
        await manager.register_into(registry,allow_start())
        original=manager.clients["fake"]
        try:
            with pytest.raises(McpError):
                await manager.reconfigure(parse_servers({"bad":{"command":"/missing-mcp-binary"}}),registry,allow_start())
            assert manager.clients["fake"] is original
            assert (await registry.execute_async(ToolCall("mcp_fake_echo",{"text":"still live"})))[0]
            await manager.reconfigure(parse_servers({"new":make_server(tmp_path,"new")}),registry,allow_start())
            assert "mcp_fake_echo" not in registry.names()
            assert "mcp_new_echo" in registry.names()
            assert (await registry.execute_async(ToolCall("mcp_new_echo",{"text":"new"})))[0]
            await manager.disconnect(registry,allow_start())
            assert not manager.clients and "mcp_new_echo" not in registry.names()
            assert "read_file" in registry.names()
        finally:
            await manager.close()
    asyncio.run(scenario())

def test_denied_reload_and_disconnect_leave_registry_unchanged(tmp_path):
    async def deny(call,preview):return ApprovalDecision(False,"deny")
    async def scenario():
        registry=ToolRegistry(tmp_path)
        raw={"fake":make_server(tmp_path)}
        manager=McpManager.from_config(raw,tmp_path)
        await manager.register_into(registry,allow_start())
        original=manager.clients["fake"]
        try:
            with pytest.raises(McpError):
                await manager.reconfigure(parse_servers(raw),registry,ApprovalGate(deny))
            with pytest.raises(McpError):
                await manager.disconnect(registry,ApprovalGate(deny))
            with pytest.raises(McpError):
                await manager.reconfigure({},registry,ApprovalGate(deny))
            assert manager.clients["fake"] is original
            assert "mcp_fake_echo" in registry.names()
        finally:await manager.close()
    asyncio.run(scenario())

def test_cleanup_failure_does_not_skip_other_connections(tmp_path):
    class Client:
        def __init__(self, fail=False):self.fail,self.closed=fail,False
        async def close(self):
            self.closed=True
            if self.fail:raise RuntimeError("failure")
    async def scenario():
        manager=McpManager({},tmp_path)
        retired=McpManager({},tmp_path)
        bad,good=Client(True),Client()
        retired.clients={"bad":bad}
        manager.retired=[retired]
        manager.clients={"good":good}
        with pytest.raises(McpError):await manager.close()
        assert good.closed and not manager.clients
        assert manager.retired == [retired]
        bad.fail=False
        await manager.close()
        assert not manager.retired
    asyncio.run(scenario())

def test_cancelled_reload_keeps_original_tools(tmp_path, monkeypatch):
    from app.tools import mcp
    async def scenario():
        registry=ToolRegistry(tmp_path)
        raw={"fake":make_server(tmp_path)}
        manager=McpManager.from_config(raw,tmp_path)
        await manager.register_into(registry,allow_start())
        original=manager.clients["fake"]
        async def cancelled(self):raise asyncio.CancelledError()
        monkeypatch.setattr(mcp._Connection,"open",cancelled)
        try:
            with pytest.raises(asyncio.CancelledError):
                await manager.reconfigure(parse_servers(raw),registry,allow_start())
            assert manager.clients["fake"] is original
            assert (await registry.execute_async(ToolCall("mcp_fake_echo",{"text":"alive"})))[0]
        finally:await manager.close()
    asyncio.run(scenario())


def test_scoped_reload_disconnect_keep_other_service_and_its_grants(tmp_path):
    async def scenario():
        registry = ToolRegistry(tmp_path)
        raw = {"alpha": make_server(tmp_path, "alpha"), "beta": make_server(tmp_path, "beta")}
        manager = McpManager.from_config(raw, tmp_path)
        gate = allow_start()
        await manager.register_into(registry, gate)
        alpha, beta = manager.clients["alpha"], manager.clients["beta"]
        gate.require_once("mcp_alpha_echo", lambda *args: None)
        gate.require_once("mcp_beta_echo", lambda *args: None)
        gate.session_allowed.add((str(tmp_path), "mcp_alpha_echo", "old"))
        gate.session_allowed.add((str(tmp_path), "mcp_beta_echo", "keep"))
        try:
            with pytest.raises(McpError):
                await manager.reconfigure(parse_servers({"alpha":{"command":"/missing-server"}}), registry, gate, only="alpha")
            assert manager.clients == {"alpha": alpha, "beta": beta}
            assert "mcp_alpha_echo" in gate.once_only
            await manager.reconfigure(parse_servers(raw), registry, gate, only="alpha")
            assert manager.clients["alpha"] is not alpha
            assert manager.clients["beta"] is beta
            assert "mcp_alpha_echo" not in gate.once_only
            assert "mcp_beta_echo" in gate.once_only
            assert all(key[1] != "mcp_alpha_echo" for key in gate.session_allowed)
            assert any(key[1] == "mcp_beta_echo" for key in gate.session_allowed)
            await manager.disconnect(registry, gate, only="alpha")
            assert "alpha" not in manager.clients and manager.clients["beta"] is beta
            assert "mcp_alpha_echo" not in registry.names()
            assert (await registry.execute_async(ToolCall("mcp_beta_echo", {"text":"still live"})))[0]
        finally:
            await manager.close()
    asyncio.run(scenario())


def test_scoped_denial_and_unknown_target_cannot_disconnect_all(tmp_path):
    async def deny(call, preview):
        return ApprovalDecision(False, "deny")
    async def scenario():
        registry = ToolRegistry(tmp_path)
        raw = {"alpha":make_server(tmp_path)}
        manager = McpManager.from_config(raw, tmp_path)
        await manager.register_into(registry, allow_start())
        original = manager.clients["alpha"]
        try:
            with pytest.raises(McpError):
                await manager.disconnect(registry, ApprovalGate(deny), only="alpha")
            with pytest.raises(McpError):
                await manager.reconfigure({}, registry, allow_start(), only="alpha")
            with pytest.raises(McpError):
                await manager.disconnect(registry, allow_start(), only="missing")
            assert manager.clients["alpha"] is original
            assert "mcp_alpha_echo" in registry.names()
        finally:
            await manager.close()
    asyncio.run(scenario())


def test_cancel_scoped_cleanup_preserves_other_connection_and_retries(tmp_path):
    class Client:
        def __init__(self, cancel=False):
            self.cancel, self.closed = cancel, False
            self.started = asyncio.Event()
        async def close(self):
            if self.cancel:
                self.started.set()
                await asyncio.Event().wait()
            self.closed = True
    async def scenario():
        manager = McpManager(parse_servers({"alpha":{"command":"x"},"beta":{"command":"x"}}), tmp_path)
        alpha, beta = Client(True), Client()
        manager.clients = {"alpha":alpha,"beta":beta}
        registry = ToolRegistry(tmp_path)
        operation = asyncio.create_task(manager.disconnect(registry, allow_start(), only="alpha"))
        await alpha.started.wait()
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert manager.clients["beta"] is beta and not beta.closed
        assert len(manager.retired) == 1
        alpha.cancel = False
        await manager.close()
        assert alpha.closed and beta.closed and not manager.retired
    asyncio.run(scenario())
