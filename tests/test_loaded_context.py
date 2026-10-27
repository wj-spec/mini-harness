import asyncio
import json
import signal

import pytest

from app.agent import Agent
from app.config import Config
from app.policy import ApprovalGate
from app.providers.fake import FakeProvider
from app.session import SessionStore
from app.stdio import serve
from app.tools import ToolRegistry, ToolSpec
from core.budget import BudgetPolicy
from core.context import ContextBuilder
from core.models import ToolCall
from test_stdio import Harness
from test_stdio_parity import Connection, StartupManager


def agent_for(tmp_path):
    return Agent(FakeProvider([]), ToolRegistry(tmp_path),
                 context=ContextBuilder(policy=BudgetPolicy()),
                 system="基础指令", project_instructions="项目规则")


def test_entering_session_publishes_loaded_usage_before_any_user_message(tmp_path):
    async def scenario():
        agent = agent_for(tmp_path)
        session = SessionStore.new(tmp_path, "fake")
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until("status")
            snapshots = [event for event in harness.events if event["type"] == "context_snapshot"]
            assert len(snapshots) == 1
            event = snapshots[0]
            snapshot = event["snapshot"]
            assert event["id"] is None and snapshot["phase"] == "loaded"
            assert snapshot["sections"]["system"] > 0 and snapshot["sections"]["tools"] > 0
            assert snapshot["sections"]["history"] == snapshot["sections"]["memory"] == 0
            assert snapshot["total_tokens"] == sum(snapshot["sections"].values())
            assert {block["kind"] for block in snapshot["blocks"]} >= {"base_instructions", "project_instructions"}
            metrics = [event for event in harness.events if event["type"] == "metrics"][-1]
            assert metrics["context_tokens"] == snapshot["total_tokens"]
            assert metrics["used_tokens"] == 0 and metrics["cost_usd"] == 0
            assert agent.provider.seen == [] and agent.history == [] and agent.state is None
            assert session.latest_run() is None and session.load_messages() == []
            trace = [json.loads(line) for line in session.trace_path.read_text().splitlines()]
            assert [event["type"] for event in trace] == ["context_snapshot"]
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_loaded_snapshot_refreshes_registered_tools_without_calling_model(tmp_path):
    async def scenario():
        agent = agent_for(tmp_path)
        before = await agent.loaded_context_snapshot("session-one")
        assert before["model"] == "FakeProvider"
        agent.tools.register(ToolSpec("mcp_probe", "Inspect MCP data", {"type": "object"}, lambda: "ok"))
        after = await agent.loaded_context_snapshot("session-one")
        assert after["phase"] == "loaded" and after["step"] == after["attempt"] == 0
        assert after["run_id"] == before["run_id"]
        assert after["snapshot_id"] != before["snapshot_id"]
        assert after["sections"]["tools"] > before["sections"]["tools"]
        assert any(block["source"] == "mcp_probe" for block in after["blocks"])
        assert after["delta_tokens"] == after["total_tokens"] - before["total_tokens"]
        assert agent.provider.seen == [] and agent.state is None

    asyncio.run(scenario())


def test_loaded_context_does_not_grow_with_inactive_skill_catalog(tmp_path):
    async def scenario():
        agent = agent_for(tmp_path)
        before = await agent.loaded_context_snapshot("same-session")
        entrypoint = next(block for block in before["blocks"] if block["kind"] == "skills")
        assert entrypoint["label"] == "技能入口" and entrypoint["tokens"] <= 100
        assert "list_skills" in agent.system and "load_skill" in agent.system
        for index in range(40):
            path = tmp_path / ".cursor" / "skills" / f"private-procedure-{index}" / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"description: PRIVATE_SUMMARY_{index}\n\nPRIVATE_BODY_{index}", encoding="utf-8")
        catalog = agent.manage_skills("refresh")
        assert "private-procedure-39" in catalog
        after = await agent.loaded_context_snapshot("same-session")
        assert after["total_tokens"] == before["total_tokens"]
        assert after["sections"]["system"] == before["sections"]["system"]
        assert "private-procedure-" not in agent.system
        assert "PRIVATE_SUMMARY_" not in agent.system and "PRIVATE_BODY_" not in agent.system
        assert str(tmp_path / ".cursor" / "skills") not in agent.system
        assert len(agent.tools.skill_entries()) == 40
        assert agent.provider.seen == []

    asyncio.run(scenario())


def test_skill_details_are_loaded_only_while_a_procedure_is_active(tmp_path):
    path = tmp_path / ".vibe" / "skills" / "guide" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("description: A guide\n\nPRIVATE_PROCEDURE", encoding="utf-8")
    agent = agent_for(tmp_path)
    bootstrap = agent.system
    assert "Relative resource paths" not in bootstrap
    agent.manage_skills("activate", "guide")
    assert "PRIVATE_PROCEDURE" in agent.system and str(path) in agent.system
    assert "Relative resource paths" in agent.system
    assert "Reload" in agent.system and "cancelled" in agent.system
    agent.manage_skills("unload", "guide")
    assert agent.system == bootstrap
    assert agent.provider.seen == []


def test_removed_skill_tools_do_not_advertise_a_skill_entrypoint(tmp_path):
    registry = ToolRegistry(tmp_path)
    for name in ("list_skills", "load_skill", "unload_skill"):
        registry._tools.pop(name)
    agent = Agent(FakeProvider([]), registry)
    assert "[SKILL LIFECYCLE]" not in agent.system
    assert not any(part["kind"] == "skills" for part in agent.system_parts)


def test_cancelling_initial_snapshot_cleans_up_stdio_startup(tmp_path):
    async def scenario():
        agent = agent_for(tmp_path)

        async def previous_handler(call, preview):
            raise AssertionError("startup must not request tool approval")

        agent.approval = ApprovalGate(previous_handler)
        entered = asyncio.Event()

        async def blocked_snapshot(session_key):
            entered.set()
            await asyncio.Event().wait()

        agent.loaded_context_snapshot = blocked_snapshot
        manager = StartupManager()
        events = []

        async def requests():
            await asyncio.Event().wait()
            yield ""

        loop = asyncio.get_running_loop()
        signals = {sig: signal.getsignal(sig) for sig in
                   (signal.SIGINT, signal.SIGTERM,
                    *([signal.SIGHUP] if hasattr(signal, "SIGHUP") else []))}
        baseline = asyncio.all_tasks()
        task = asyncio.create_task(serve(
            agent, Config(workspace=tmp_path), requests=requests(),
            emit=events.append, mcp=manager,
        ))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert agent.approval.handler is previous_handler
            assert manager.closed
            assert [event["type"] for event in events] == ["ready", "closed"]
            assert not (asyncio.all_tasks() - baseline)
            assert all(signal.getsignal(sig) is previous for sig, previous in signals.items())
            assert agent.provider.seen == []
        finally:
            for sig, previous in signals.items():
                loop.remove_signal_handler(sig)
                signal.signal(sig, previous)

    asyncio.run(scenario())


@pytest.mark.parametrize("decision", ["once", "deny"])
def test_mcp_startup_refreshes_the_loaded_definitions_after_approval(tmp_path, decision):
    class Manager(StartupManager):
        async def register_into(self, registry, approval):
            await super().register_into(registry, approval)
            if self.started:
                registry.register(ToolSpec("mcp_probe", "MCP probe", {"type": "object"}, lambda: "ok"))
                return ["mcp_probe"]
            return []

    async def scenario():
        agent = agent_for(tmp_path)
        manager = Manager()
        connection = Connection(agent, tmp_path, mcp=manager)
        try:
            request = await connection.until("approval_request", "startup")
            before = next(event["snapshot"] for event in connection.events if event["type"] == "context_snapshot")
            connection.send("approval", "approve", approval_id=request["approval_id"], decision=decision)
            while (await connection.until("status"))["phase"] != "idle":
                pass
            snapshots = [event["snapshot"] for event in connection.events if event["type"] == "context_snapshot"]
            assert len(snapshots) == 2 and all(snapshot["phase"] == "loaded" for snapshot in snapshots)
            after = snapshots[-1]
            assert (after["sections"]["tools"] > before["sections"]["tools"]) is (decision == "once")
            assert agent.provider.seen == [] and agent.history == []
        finally:
            await connection.close()

    asyncio.run(scenario())
