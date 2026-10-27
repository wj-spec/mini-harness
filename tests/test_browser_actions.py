import asyncio
import json
from types import SimpleNamespace

import pytest

from app.agent import Agent
from app.mcp import McpManager, _Connection, parse_servers
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry
from core.models import Response, ToolCall


SNAPSHOT = {
    "snapshot_id": "s1", "url": "http://localhost:3000/", "title": "Local fixture",
    "elements": [{"ref": "e1", "role": "textbox", "name": "Message"}],
    "snapshot": '- textbox "Message" [ref=e1]',
}
READS = ["browser_snapshot", "browser_console_messages", "browser_network_requests", "browser_close"]
WRITES = ["browser_navigate", "browser_click", "browser_type", "browser_select_option"]


async def setup_browser(tmp_path, monkeypatch, handler):
    calls = []

    async def open_connection(self):
        return [SimpleNamespace(name=name, description=name, input_schema={"type": "object"})
                for name in READS + WRITES]

    async def remote_call(self, name, args):
        calls.append((name, args))
        return json.dumps(SNAPSHOT)

    async def close(self):
        pass

    monkeypatch.setattr(_Connection, "open", open_connection)
    monkeypatch.setattr(_Connection, "call", remote_call)
    monkeypatch.setattr(_Connection, "close", close)
    manager = McpManager.from_config({"browser": {
        "command": "fixture", "browser_actions": True, "risk": "read",
        "allowed_origins": ["http://localhost:3000"], "allowed_tools": READS + WRITES,
        "tool_risks": {name: "read" for name in WRITES},
    }}, tmp_path)
    registry, gate = ToolRegistry(tmp_path), ApprovalGate(handler)
    await manager.register_into(registry, gate)
    assert not manager.errors
    exposed = {remote: name for name, (_, remote) in manager.tools.items()}
    return manager, registry, gate, exposed, calls


def test_browser_actions_remain_write_and_once_only(tmp_path, monkeypatch):
    async def scenario():
        approvals = []

        async def approve(call, preview):
            approvals.append(call.name)
            if call.name == "mcp_start":
                assert call.args["browser_actions"] is True
                assert "http://localhost:3000" in preview
            return ApprovalDecision(True, "once")

        manager, registry, gate, exposed, calls = await setup_browser(tmp_path, monkeypatch, approve)
        try:
            for name in WRITES:
                assert registry.risk(exposed[name]) == "write"
                call = ToolCall(exposed[name], {"url": "http://localhost:3000/"})
                assert gate.decisions_for(call) == ["once", "deny"]
                gate.session_allowed.add(gate._key(call, str(tmp_path.resolve())))
                gate.workspace_allowed.add(str(tmp_path.resolve()))
                assert (await gate.request(call, "fixture", tmp_path)).allowed
                assert (await gate.request(call, "fixture", tmp_path)).allowed
                assert approvals.count(exposed[name]) == 2
            assert calls == []
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("scope", ["session", "workspace"])
def test_browser_rejects_broader_approval_scope(tmp_path, monkeypatch, scope):
    async def scenario():
        async def approve(call, preview):
            return ApprovalDecision(True, "once" if call.name == "mcp_start" else scope)

        manager, _, gate, exposed, calls = await setup_browser(tmp_path, monkeypatch, approve)
        try:
            decision = await gate.request(ToolCall(exposed["browser_click"], {}), "fixture", tmp_path)
            assert not decision.allowed
            assert calls == [] and not gate.session_allowed and not gate.workspace_allowed
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("deny", [False, True])
def test_browser_action_preview_and_agent_approval(tmp_path, monkeypatch, deny):
    async def scenario():
        previews = []

        async def approve(call, preview):
            if call.name != "mcp_start":
                previews.append(preview)
            return ApprovalDecision(not deny or call.name == "mcp_start", "once")

        manager, registry, gate, exposed, calls = await setup_browser(tmp_path, monkeypatch, approve)
        try:
            secret_text = "private fixture text"
            args = {"snapshot_id": "s1", "ref": "e1", "text": secret_text}
            provider = FakeProvider([
                Response(tool_calls=[ToolCall(exposed["browser_snapshot"], {})]),
                Response(tool_calls=[ToolCall(exposed["browser_type"], args)]),
                Response(content="完成"),
            ])
            await Agent(provider, registry, approval=gate).run("填写本地表单，不提交")
            assert [name for name, _ in calls] == (["browser_snapshot"] if deny else ["browser_snapshot", "browser_type"])
            assert len(previews) == 1
            assert all(text in previews[0] for text in ["http://localhost:3000", "Local fixture", "textbox", "Message", "s1", "20"])
            assert secret_text not in previews[0]
            audit_text = (tmp_path / ".vibe/browser/audit.jsonl").read_text()
            audit = json.loads(audit_text.splitlines()[-1])
            assert audit["snapshot_id"] == "s1" and audit["origin"] == "http://localhost:3000"
            assert audit["decision"] == ("deny" if deny else "once")
            assert "args_hash" in audit and secret_text not in audit_text
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("bad_args", [
    {"snapshot_id": "old", "ref": "e1", "text": "x"},
    {"snapshot_id": "s1", "ref": "gone", "text": "x"},
    {"snapshot_id": "s1", "ref": "e1", "text": "x", "submit": True},
    {"snapshot_id": "s1", "ref": "e1", "text": "x", "unexpected": "x"},
])
def test_invalid_browser_action_never_reaches_approval_or_remote(tmp_path, monkeypatch, bad_args):
    async def scenario():
        async def approve(call, preview):
            assert call.name == "mcp_start"
            return ApprovalDecision(True, "once")

        manager, registry, gate, exposed, calls = await setup_browser(tmp_path, monkeypatch, approve)
        try:
            provider = FakeProvider([
                Response(tool_calls=[ToolCall(exposed["browser_snapshot"], {})]),
                Response(tool_calls=[ToolCall(exposed["browser_type"], bad_args)]),
                Response(content="已拒绝"),
            ])
            await Agent(provider, registry, approval=gate).run("fixture")
            assert [name for name, _ in calls] == ["browser_snapshot"]
            assert "ERROR" in next(m.content for m in provider.seen[-1] if m.role == "tool" and m.name == exposed["browser_type"])
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("update", [
    {"allowed_origins": []}, {"allowed_origins": ["https://example.test"]},
    {"allowed_tools": []}, {"allowed_tools": ["browser_evaluate"]},
    {"browser_actions": "yes"},
])
def test_controlled_browser_config_fails_closed(update):
    config = {"command": "fixture", "browser_actions": True,
              "allowed_origins": ["http://localhost:3000"], "allowed_tools": READS + WRITES}
    config.update(update)
    with pytest.raises(ValueError):
        parse_servers({"browser": config})


def test_browser_origins_use_browser_url_normalization():
    server = parse_servers({"browser": {
        "command": "fixture", "browser_actions": True,
        "allowed_origins": ["http://LOCALHOST:80", "https://[::1]:443/"],
        "allowed_tools": READS + WRITES,
    }})["browser"]
    assert server.allowed_origins == ("http://localhost", "https://[::1]")


@pytest.mark.parametrize("failure", ["remote_error", "redirect", "invalid_json"])
def test_browser_failure_invalidates_snapshot_without_retry(tmp_path, monkeypatch, failure):
    async def scenario():
        async def approve(call, preview):
            return ApprovalDecision(True, "once")

        manager, registry, gate, exposed, calls = await setup_browser(tmp_path, monkeypatch, approve)
        try:
            await registry.execute_async(ToolCall(exposed["browser_snapshot"], {}))
            attempts = []

            async def failed_call(self, name, args):
                attempts.append(name)
                if failure == "remote_error":
                    raise RuntimeError("element disappeared")
                if failure == "invalid_json":
                    return "not a snapshot"
                return json.dumps({**SNAPSHOT, "url": "http://evil.example/"})

            monkeypatch.setattr(_Connection, "call", failed_call)
            args = {"snapshot_id": "s1", "ref": "e1", "text": "fixture"}
            provider = FakeProvider([Response(tool_calls=[ToolCall(exposed["browser_type"], args)]), "done"])
            await Agent(provider, registry, approval=gate, tool_retries=5).run("fixture")
            assert attempts == ["browser_type"]
            with pytest.raises(ValueError, match="stale_snapshot"):
                registry.preview(ToolCall(exposed["browser_type"], args))
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_browser_cancelled_approval_never_executes_and_is_audited(tmp_path, monkeypatch):
    async def scenario():
        pending, cancel = asyncio.Event(), asyncio.Event()

        async def approve(call, preview):
            if call.name != "mcp_start":
                pending.set()
                await asyncio.Event().wait()
            return ApprovalDecision(True, "once")

        manager, registry, gate, exposed, calls = await setup_browser(tmp_path, monkeypatch, approve)
        try:
            await registry.execute_async(ToolCall(exposed["browser_snapshot"], {}))
            args = {"snapshot_id": "s1", "ref": "e1", "text": "fixture"}
            provider = FakeProvider([Response(tool_calls=[ToolCall(exposed["browser_type"], args)])])
            running = asyncio.create_task(Agent(provider, registry, approval=gate).run("fixture", cancel=cancel))
            await asyncio.wait_for(pending.wait(), 1)
            cancel.set()
            result = await asyncio.wait_for(running, 1)
            assert result.status == "cancelled"
            assert [name for name, _ in calls] == ["browser_snapshot"]
            audit = json.loads((tmp_path / ".vibe/browser/audit.jsonl").read_text().splitlines()[-1])
            assert audit["decision"] == "cancelled"
        finally:
            await manager.close()

    asyncio.run(scenario())
