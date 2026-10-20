import asyncio
import json

from app.agent import Agent
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.session import SessionStore
from app.tools import ToolRegistry, ToolSpec
from core.budget import BudgetPolicy
from core.context import ContextBuilder, total_tokens
from core.models import Message, Response, ToolCall


def run(coro):
    return asyncio.run(coro)


def old_history():
    history = [Message("user", "接入 MCP，保留审批机制。")]
    for index in range(8):
        history.extend([
            Message("assistant", tool_calls=[
                ToolCall("read_file", {"path": f"old-{index}.py"}, id=f"old-{index}"),
            ]),
            Message("tool", f"source {index} " * 2000,
                    name="read_file", tool_call_id=f"old-{index}"),
        ])
    return history


def context_for(tmp_path, **kwargs):
    return ContextBuilder(
        policy=BudgetPolicy(), keep_recent_groups=1,
        offload_dir=tmp_path / ".vibe" / "offload", **kwargs,
    )


def register_probe(tmp_path, context, output="inspected"):
    registry = ToolRegistry(tmp_path, context=context)
    registry.register(ToolSpec(
        "probe", "Inspect a source file", {"type": "object"}, lambda step: output,
    ))
    return registry


def test_interval_threshold_compacts_and_continues_with_tools_in_same_run(tmp_path):
    hidden_calls = []

    def hidden_summary(messages):
        hidden_calls.append(messages)
        raise AssertionError("自动压缩不能增加未计量的模型调用")

    history = old_history()
    context = context_for(tmp_path, summarizer=hidden_summary)
    registry = register_probe(tmp_path, context)
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("probe", {"step": 1}, id="first")],
                 usage={"total_tokens": 160_000}),
        Response(tool_calls=[ToolCall("probe", {"step": 2}, id="second")],
                 usage={"total_tokens": 10_000}),
        Response("MCP 接入完成。", usage={"total_tokens": 100}),
    ])
    session = SessionStore.new(tmp_path, "fake")
    for message in history:
        session.append_message(message)
    agent = Agent(
        provider, registry, context=context, history=history,
        max_run_tokens=1_000_000, compact_interval_tokens=200_000,
    )
    events, contexts = [], []

    result = run(agent.run(
        "继续实现", on_message=session.append_message, on_history=session.save_context,
        on_context=contexts.append, trace=events.append,
    ))

    assert result.status == "done" and result.usage["total_tokens"] == 170_100
    assert len(provider.seen) == 3 and all(provider.seen_tools)
    assert hidden_calls == []
    assert sum(e["type"] == "run_start" for e in events) == 1
    assert sum(e["type"] == "run_end" for e in events) == 1
    compacted = [c for c in contexts if "history_before" in c.sections]
    assert compacted and compacted[0].sections["history_after"] < compacted[0].sections["history_before"]
    assert any(m.name == "history_summary_l2" for m in provider.seen[1])
    assert any(m.tool_call_id == "first" for m in provider.seen[1])
    assert session.load_context() == agent.history
    assert len(session.load_messages()) > len(session.load_context())
    assert not any(m.name == "budget_stop" for m in agent.history)


def test_compaction_interval_rearms_without_resetting_total_usage(tmp_path):
    context = context_for(tmp_path)
    registry = register_probe(tmp_path, context, output="file body " * 4000)
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("probe", {"step": 1}, id="first")],
                 usage={"total_tokens": 160_000}),
        Response(tool_calls=[ToolCall("probe", {"step": 2}, id="second")],
                 usage={"total_tokens": 160_000}),
        Response("done", usage={"total_tokens": 100}),
    ])
    agent = Agent(
        provider, registry, context=context, history=old_history(),
        max_run_tokens=1_000_000, compact_interval_tokens=200_000,
    )
    events = []

    result = run(agent.run("继续", trace=events.append))

    assert result.status == "done" and result.usage["total_tokens"] == 320_100
    assert len([e for e in events if e["type"] == "history_compacted"]) == 2
    used = [e["used_tokens"] for e in events if e["type"] == "budget_update"]
    assert used == sorted(used) and used[-1] == 320_100
    assert all(provider.seen_tools)


def test_task_total_still_stops_unexecuted_tool_after_multiple_compactions(tmp_path):
    approvals = []

    async def allow(call, preview):
        approvals.append(call.name)
        return ApprovalDecision(True, "once")

    context = context_for(tmp_path)
    registry = register_probe(tmp_path, context, output="source " * 4000)
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("probe", {"step": 1}, id="first")],
                 usage={"total_tokens": 80_000}),
        Response(tool_calls=[ToolCall("probe", {"step": 2}, id="second")],
                 usage={"total_tokens": 80_000}),
        Response(tool_calls=[ToolCall("write_file", {"path": "never.txt", "content": "x"}, id="write")],
                 usage={"total_tokens": 40_000}),
    ])
    agent = Agent(
        provider, registry, context=context, history=old_history(),
        max_run_tokens=200_000, compact_interval_tokens=100_000,
        approval=ApprovalGate(allow),
    )
    events = []

    result = run(agent.run("继续", trace=events.append))

    assert result.status == "stopped" and result.stop_reason == "token_budget"
    assert result.usage["total_tokens"] == 200_000 and len(provider.seen) == 3
    assert not (tmp_path / "never.txt").exists() and approvals == []
    assert any(m.role == "tool" and m.tool_call_id == "write" and "not_executed" in m.content
               for m in agent.history)
    assert sum(e["type"] == "run_start" for e in events) == 1
    assert sum(e["type"] == "run_end" for e in events) == 1


def test_unaffordable_request_is_compacted_before_checkpoint_fallback(tmp_path):
    context = context_for(tmp_path)
    history = [Message("user", "小范围修复"), Message("assistant", "x" * 120_000)]
    provider = FakeProvider([Response("done", usage={"total_tokens": 200})])
    agent = Agent(
        provider, ToolRegistry(tmp_path, context=context), context=context, history=history,
        max_run_tokens=10_000, compact_interval_tokens=200_000,
    )
    snapshots = []

    result = run(agent.run("继续", on_history=snapshots.append))

    assert result.status == "done" and result.stop_reason == ""
    assert len(provider.seen) == 1 and provider.seen_tools[0]
    assert snapshots and total_tokens(snapshots[0]) < total_tokens(history)
    assert not any("Write a short progress checkpoint" in m.content for m in provider.seen[0])


def test_history_high_water_compacts_without_waiting_for_spending_interval(tmp_path):
    context = ContextBuilder(
        max_tokens=20_000, reserve_output=2_000, keep_recent_groups=1,
        offload_dir=tmp_path / ".vibe" / "offload",
    )
    history = [Message("user", "小范围修复"), Message("assistant", "x" * 60_000)]
    provider = FakeProvider([Response("done", usage={"total_tokens": 200})])
    agent = Agent(
        provider, ToolRegistry(tmp_path, context=context), context=context, history=history,
        max_run_tokens=1_000_000, compact_interval_tokens=0,
    )
    events = []

    result = run(agent.run("继续", trace=events.append))

    assert result.status == "done" and provider.seen_tools[0]
    assert any(e["type"] == "history_compacted" for e in events)
    assert total_tokens(provider.seen[0]) < total_tokens(history)


def test_ineffective_compaction_does_not_restart_or_loop(tmp_path):
    context = context_for(tmp_path)
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("probe", {"step": 1}, id="first")],
                 usage={"total_tokens": 160_000}),
        Response("done", usage={"total_tokens": 100}),
    ])
    agent = Agent(
        provider, register_probe(tmp_path, context), context=context,
        max_run_tokens=1_000_000, compact_interval_tokens=200_000,
    )
    events = []

    result = run(agent.run("tiny task", trace=events.append))

    assert result.status == "done" and len(provider.seen) == 2
    assert len([e for e in events if e["type"] == "history_compacted"]) == 1
    assert sum(e["type"] == "run_start" for e in events) == 1


def test_workspace_permission_survives_automatic_compaction(tmp_path):
    asked = []

    async def allow_workspace(call, preview):
        asked.append(call.name)
        return ApprovalDecision(True, "workspace")

    context = context_for(tmp_path)
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("write_file", {"path": "a", "content": "first"}, id="a")],
                 usage={"total_tokens": 160000}),
        Response(tool_calls=[ToolCall("write_file", {"path": "b", "content": "second"}, id="b")],
                 usage={"total_tokens": 10000}),
        Response("done", usage={"total_tokens": 100}),
    ])
    audit = tmp_path / ".vibe" / "audit.jsonl"
    agent = Agent(provider, ToolRegistry(tmp_path, context=context), context=context,
                  history=old_history(), approval=ApprovalGate(allow_workspace, audit),
                  max_run_tokens=1000000, compact_interval_tokens=200000)
    events = []

    result = run(agent.run("继续实现", trace=events.append))

    assert result.status == "done" and result.usage["total_tokens"] == 170100
    assert asked == ["write_file"]
    assert (tmp_path / "a").read_text() == "first"
    assert (tmp_path / "b").read_text() == "second"
    assert any(e["type"] == "history_compacted" for e in events)
    assert [json.loads(line)["decision"] for line in audit.read_text().splitlines()] == ["workspace", "workspace_allow"]
