import asyncio
from types import SimpleNamespace

from app import agent as agent_module
from app.agent import Agent
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry
from core.models import Response, ToolCall


def test_human_approval_wait_does_not_consume_run_time(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(agent_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def approve(call, preview):
        clock[0] += 1000.0
        return ApprovalDecision(True, "once")

    provider = FakeProvider([
        Response(tool_calls=[ToolCall("write_file", {"path": "a", "content": "ok"}, id="write")]),
        "done",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(approve), max_run_seconds=10)

    result = asyncio.run(agent.run("write"))

    assert (tmp_path / "a").read_text() == "ok"
    assert result.status == "done" and len(provider.seen) == 2


def test_cancel_interrupts_approval_without_late_write_or_unpaired_calls(tmp_path):
    async def scenario():
        entered, release, cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def approve(call, preview):
            entered.set()
            await release.wait()
            return ApprovalDecision(True, "once")

        calls = [ToolCall("write_file", {"path": "a", "content": "ok"}, id="a"),
                 ToolCall("write_file", {"path": "b", "content": "ok"}, id="b")]
        agent = Agent(FakeProvider([Response(tool_calls=calls)]), ToolRegistry(tmp_path),
                      approval=ApprovalGate(approve))
        task = asyncio.create_task(agent.run("write", cancel=cancel))
        await asyncio.wait_for(entered.wait(), 1)
        cancel.set()
        done, _ = await asyncio.wait({task}, timeout=0.2)
        stopped_promptly = task in done
        release.set()
        result = await asyncio.wait_for(task, 1)

        assert stopped_promptly
        assert result.status == "cancelled"
        assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists()
        assert {m.tool_call_id for m in agent.history if m.role == "tool"} == {"a", "b"}

    asyncio.run(scenario())


def test_cancel_after_first_result_pairs_remaining_tools(tmp_path):
    async def scenario():
        cancel = asyncio.Event()
        approved = []

        async def approve(call, preview):
            approved.append(call.id)
            return ApprovalDecision(True, "once")

        calls = [ToolCall("write_file", {"path": "a", "content": "one"}, id="a"),
                 ToolCall("write_file", {"path": "b", "content": "two"}, id="b")]
        agent = Agent(FakeProvider([Response(tool_calls=calls)]), ToolRegistry(tmp_path),
                      approval=ApprovalGate(approve))

        result = await agent.run("write", cancel=cancel, on_result=lambda *args: cancel.set())

        assert result.status == "cancelled"
        assert (tmp_path / "a").read_text() == "one"
        assert not (tmp_path / "b").exists() and approved == ["a"]
        results = {m.tool_call_id: m.content for m in agent.history if m.role == "tool"}
        assert set(results) == {"a", "b"}
        assert "cancelled" in results["b"] and "not executed" in results["b"]

    asyncio.run(scenario())
