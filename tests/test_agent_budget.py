import asyncio
import pytest

from app.agent import Agent
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.session import SessionStore
from app.tools import ToolRegistry, ToolSpec
from app.events import StreamError, TextDelta, StreamDone
from core.models import Message, Response, ToolCall
from core.state import TaskState
from core.context import ContextBuilder
from core.budget import BudgetPolicy


def run(coro):
    return asyncio.run(coro)


def test_budget_stop_preserves_last_text_and_reason(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    provider = FakeProvider([Response("已定位接入位置，尚未修改。", usage={"total_tokens": 15000})])
    agent = Agent(provider, ToolRegistry(tmp_path), max_run_tokens=10000)

    result = run(agent.run("接入 MCP", on_message=session.append_message, trace=session.append_trace))

    assert result.status == "stopped"
    assert result.stop_reason == "token_budget"
    assert any("已定位接入位置" in m.content for m in session.load_messages())
    assert "已定位接入位置" in result.text
    assert session.latest_run()["stop_reason"] == "token_budget"


def test_budget_stop_pairs_unexecuted_tools_without_approval_or_effects(tmp_path):
    approvals = []

    async def allow(call, preview):
        approvals.append(call.name)
        return ApprovalDecision(True, "once")

    calls = [
        ToolCall("write_file", {"path": "a.txt", "content": "hello"}, id="write"),
        ToolCall("bash", {"command": "echo should-not-run"}, id="command"),
    ]
    provider = FakeProvider([Response(tool_calls=calls, usage={"total_tokens": 15000})])
    session = SessionStore.new(tmp_path, "fake")
    agent = Agent(provider, ToolRegistry(tmp_path), max_run_tokens=10000, approval=ApprovalGate(allow))

    result = run(agent.run("task", on_message=session.append_message))

    assert result.status == "stopped"
    assert approvals == []
    assert not (tmp_path / "a.txt").exists()
    messages = session.load_messages()
    requested = next(m for m in messages if m.tool_calls)
    results = [m for m in messages if m.role == "tool"]
    assert [m.tool_call_id for m in results] == [call.id for call in requested.tool_calls]
    assert all("not_executed" in m.content and "token_budget" in m.content for m in results)
    assert session.repair_incomplete_tool_calls() == 0


def test_unaffordable_request_uses_one_short_tool_free_checkpoint(tmp_path):
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("list_files", {}, id="list")],
                 usage={"prompt_tokens": 7500, "completion_tokens": 500, "total_tokens": 8000}),
        Response("已检查项目，接下来需实现客户端。", usage={"total_tokens": 400}),
    ])
    history = [Message("user", "x" * 20000), Message("assistant", "old answer")]
    agent = Agent(provider, ToolRegistry(tmp_path), max_run_tokens=10000, history=history)
    events = []

    result = run(agent.run("接入 MCP", trace=events.append))

    assert result.status == "stopped" and result.stop_reason == "token_budget"
    assert len(provider.seen) == 2
    assert provider.seen_tools[0] and provider.seen_tools[1] == []
    assert sum(len(m.content) for m in provider.seen[1]) < 8000
    assert "接入 MCP" in " ".join(m.content for m in provider.seen[1])
    assert "已检查项目" in result.text
    budget = [e for e in events if e["type"] == "budget_update"]
    assert budget[0]["used_tokens"] == 0
    assert any(e["phase"] == "finalizing" for e in budget)
    assert budget[-1]["phase"] == "stopped"
    assert budget[-1]["used_tokens"] == 8400
    assert budget[-1]["remaining_tokens"] == 1600


def test_budget_checkpoint_cannot_execute_model_tools(tmp_path):
    executed = []
    registry = ToolRegistry(tmp_path)
    registry.register(ToolSpec("probe", "probe", {"type": "object"}, lambda: executed.append(1)))
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("list_files", {}, id="list")], usage={"total_tokens": 9500}),
        Response(tool_calls=[ToolCall("probe", {}, id="probe")], usage={"total_tokens": 100}),
        "unexpected continuation",
    ])
    agent = Agent(provider, registry, max_run_tokens=10000)

    result = run(agent.run("task"))

    assert result.status == "stopped"
    assert executed == []
    assert any(m.tool_call_id == "probe" and "not_executed" in m.content for m in agent.history)


def test_budget_checkpoint_skips_request_when_remaining_is_too_small(tmp_path):
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("list_files", {}, id="list")], usage={"total_tokens": 9999}),
        "should not be called",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), max_run_tokens=10000)

    result = run(agent.run("task"))

    assert result.status == "stopped" and result.stop_reason == "token_budget"
    assert len(provider.seen) == 1
    assert "9999" in result.text


def test_missing_usage_is_estimated_and_budget_remains_effective(tmp_path):
    provider = FakeProvider(["x" * 60000])
    agent = Agent(provider, ToolRegistry(tmp_path), max_run_tokens=10000)
    events = []

    result = run(agent.run("task", trace=events.append))

    assert result.status == "stopped" and result.usage["total_tokens"] > 10000
    budget = [e for e in events if e["type"] == "budget_update"]
    assert budget[-1]["estimated"] is True


def test_checkpoint_reports_progress_as_recorded_not_verified(tmp_path):
    state = TaskState(goal="fix bug", files_changed=["a.py"], tests_run=["pytest -q"], open=["check result"])
    provider = FakeProvider([Response("仍需验证。", usage={"total_tokens": 15000})])
    agent = Agent(provider, ToolRegistry(tmp_path), max_run_tokens=10000, state=state)

    result = run(agent.run("fix bug"))

    assert "a.py" in result.text and "pytest -q" in result.text
    assert "check result" in result.text
    assert "待核对" in result.text


def test_budget_preflights_first_request_with_existing_history(tmp_path):
    provider = FakeProvider([Response("先缩小任务。", usage={"total_tokens": 200})])
    agent = Agent(
        provider, ToolRegistry(tmp_path), max_run_tokens=2000,
        history=[Message("user", "x" * 20000)],
    )

    result = run(agent.run("继续任务"))

    assert result.status == "stopped" and result.stop_reason == "token_budget"
    assert provider.seen_tools == [[]]
    assert sum(len(m.content) for m in provider.seen[0]) < 8000


def test_checkpoint_stream_error_preserves_partial_text_without_retry(tmp_path):
    class Provider:
        calls = 0

        async def stream(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                from app.events import ToolCallsReady
                yield ToolCallsReady([ToolCall("list_files", {}, id="list")])
                yield StreamDone(usage={"total_tokens": 9500})
            else:
                yield TextDelta("已检查工具注册。")
                yield StreamError("connection lost", retryable=True)

    provider = Provider()
    agent = Agent(provider, ToolRegistry(tmp_path), max_run_tokens=10000, llm_retry_backoff=0)

    result = run(agent.run("task"))

    assert result.status == "stopped" and result.stop_reason == "token_budget"
    assert provider.calls == 2
    assert "已检查工具注册" in result.text
    assert "connection lost" in result.text


def test_recorded_mcp_usage_can_continue_beyond_eighty_percent_of_task_budget(tmp_path):
    totals = [1490, 3320, 11206, 20022, 25320, 30397, 35637, 39485]
    script = [
        Response(tool_calls=[ToolCall("probe", {"step": i}, id=f"read{i}")],
                 usage={"total_tokens": total})
        for i, total in enumerate(totals)
    ]
    script.append(Response("仅完成调研，MCP 尚未实现。", usage={"total_tokens": 400}))
    provider = FakeProvider(script)
    registry = ToolRegistry(tmp_path)
    registry.register(ToolSpec("probe", "read only", {"type": "object"}, lambda step: "read"))
    agent = Agent(provider, registry, max_run_tokens=200000, max_steps=20)
    events = []

    result = run(agent.run("接入 MCP", trace=events.append))

    assert result.status == "done" and result.stop_reason == ""
    assert result.usage["total_tokens"] == 167277
    assert len(provider.seen) == 9 and provider.seen_tools[-1]
    assert len([m for m in agent.history if m.role == "tool"]) == 8
    assert events[-1]["type"] == "run_end" and events[-1]["tool_calls"] == 8


def test_exact_limit_preserves_call_ids_and_blocks_execution(tmp_path):
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("write_file", {"path": "a", "content": "x"})],
                 usage={"total_tokens": 10000}),
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), max_run_tokens=10000)

    result = run(agent.run("task"))

    assert result.status == "stopped" and not (tmp_path / "a").exists()
    requested = next(m for m in agent.history if m.tool_calls).tool_calls[0]
    response = next(m for m in agent.history if m.role == "tool")
    assert requested.id and response.tool_call_id == requested.id


@pytest.mark.parametrize("response", [
    Response("", usage={"total_tokens": 100}),
    Response("未完成的摘要", usage={"total_tokens": 100}, finish_reason="length"),
    Response('<tool_call>{"name":"write_file","arguments":{"path":"a","content":"x"}}</tool_call>',
             usage={"total_tokens": 100}),
])
def test_checkpoint_never_continues_or_executes_text_tools(tmp_path, response):
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("list_files", {}, id="list")], usage={"total_tokens": 9500}),
        response,
        "unexpected continuation",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), max_run_tokens=10000)

    result = run(agent.run("task"))

    assert result.status == "stopped" and result.stop_reason == "token_budget"
    assert len(provider.seen) == 2 and not (tmp_path / "a").exists()


def test_retry_cannot_consume_another_budget_sized_request(tmp_path):
    class Provider:
        calls = 0

        async def stream(self, messages, tools):
            self.calls += 1
            yield StreamError("busy", retryable=True)
            yield StreamDone(usage={"prompt_tokens": 900, "total_tokens": 900})

    provider = Provider()
    agent = Agent(provider, ToolRegistry(tmp_path), tools_enabled=False,
                  max_run_tokens=1000, llm_retry_backoff=0)

    result = run(agent.run("task"))

    assert result.status == "stopped" and result.stop_reason == "token_budget"
    assert provider.calls == 1


def test_budget_preflight_uses_compacted_request(tmp_path):
    provider = FakeProvider([Response("done", usage={"total_tokens": 200})])
    context = ContextBuilder(
        max_tokens=3200, reserve_output=200, keep_recent_groups=1,
        pin_first_user=False, offload_dir=tmp_path / ".vibe" / "offload",
    )
    agent = Agent(
        provider, ToolRegistry(tmp_path), context=context, max_run_tokens=10000,
        history=[Message("user", "x" * 80000), Message("assistant", "old")],
    )

    result = run(agent.run("small change"))

    assert result.status == "done"
    assert provider.seen_tools[0]
    assert sum(len(m.content) for m in provider.seen[0]) < 8000


@pytest.mark.parametrize("usage", [{"total_tokens": 15000}, None])
def test_budget_exhaustion_preserves_interrupted_response(tmp_path, usage):
    class Provider:
        async def stream(self, messages, tools):
            yield TextDelta("partial " + "x" * 60000)
            yield StreamError("connection lost")
            if usage:
                yield StreamDone(usage=usage)

    agent = Agent(Provider(), ToolRegistry(tmp_path), max_run_tokens=10000)

    result = run(agent.run("task"))

    assert result.status == "stopped" and result.stop_reason == "token_budget"
    assert any("partial" in m.content for m in agent.history)
    assert "connection lost" in result.text


def test_interval_compacts_working_history_and_keeps_it_for_next_user_turn(tmp_path):
    history = [Message("user", "接入 MCP，保留已有审批规则。")]
    for i in range(24):
        history.extend([
            Message("assistant", tool_calls=[ToolCall("read_file", {"path": f"{i}.py"}, id=f"old{i}")]),
            Message("tool", f"source-{i} " * 2000, name="read_file", tool_call_id=f"old{i}"),
        ])
    session = SessionStore.new(tmp_path, "fake")
    for message in history:
        session.append_message(message)
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("list_files", {}, id="latest")], usage={"total_tokens": 160000}),
        Response("只完成调研，下一步实现客户端。", usage={"total_tokens": 400}),
        Response("继续实现。", usage={"total_tokens": 100}),
    ])
    hidden_calls = []

    def hidden_summary(messages):
        hidden_calls.append(messages)
        raise AssertionError("收尾不能增加未计量的模型请求")

    context = ContextBuilder(
        policy=BudgetPolicy(), keep_recent_groups=8,
        summarizer=hidden_summary, offload_dir=tmp_path / ".vibe" / "offload",
    )
    agent = Agent(provider, ToolRegistry(tmp_path, context=context), context=context,
                  max_run_tokens=200000, history=history)
    contexts, events = [], []

    result = run(agent.run("开始", on_message=session.append_message,
                           on_history=session.save_context,
                           on_context=contexts.append, trace=events.append))

    assert result.status == "done" and len(provider.seen) == 2
    assert provider.seen_tools[1] and hidden_calls == []
    compacted = next(c for c in contexts if "history_before" in c.sections)
    assert compacted.sections["history_after"] < compacted.sections["history_before"]
    assert compacted.sections["history_after"] <= 8000
    assert any(m.name == "history_summary_l2" for m in provider.seen[1])
    assert any(m.tool_call_id == "latest" for m in provider.seen[1])
    assert len(session.load_context()) == len(agent.history) < len(session.load_messages())
    assert any(e["type"] == "history_compacted" for e in events)
    fresh = ContextBuilder(offload_dir=tmp_path / ".vibe" / "offload")
    assert all(fresh.resolve(ref) for ref in compacted.offloaded)

    run(agent.run("继续"))
    assert provider.seen_tools[-1]
    assert not any(m.content == history[2].content for m in provider.seen[-1])


def test_compaction_preserves_oversized_goal_and_skips_unaffordable_checkpoint(tmp_path):
    goal = "保留这个完整任务。" * 1000
    context = ContextBuilder(policy=BudgetPolicy(), offload_dir=tmp_path / ".vibe" / "offload")
    provider = FakeProvider([Response("should not be called", usage={"total_tokens": 1})])
    agent = Agent(provider, ToolRegistry(tmp_path, context=context), context=context,
                  max_run_tokens=1000, history=[Message("user", goal), Message("assistant", "old")])
    snapshots = []

    result = run(agent.run("继续", on_history=snapshots.append))

    assert result.status == "stopped" and provider.seen == []
    assert snapshots and any(m.content == goal for m in snapshots[-1])
    assert any(m.content == goal for m in agent.history)


def test_budget_overshoot_still_compacts_without_another_model_request(tmp_path):
    context = ContextBuilder(policy=BudgetPolicy(), keep_recent_groups=1,
                             offload_dir=tmp_path / ".vibe" / "offload")
    provider = FakeProvider([Response("partial", usage={"total_tokens": 15000})])
    agent = Agent(provider, ToolRegistry(tmp_path, context=context), context=context,
                  max_run_tokens=10000, history=[Message("user", "task"),
                                               Message("assistant", "x" * 10000)])
    snapshots = []

    result = run(agent.run("继续", on_history=snapshots.append))

    assert result.status == "stopped" and len(provider.seen) == 1
    assert snapshots and any(m.name == "history_summary_l2" for m in snapshots[-1])
    assert any("partial" in m.content for m in agent.history)


def test_automatic_compaction_respects_single_request_context_window(tmp_path):
    context = ContextBuilder(
        policy=BudgetPolicy(context_window=1000, output_cap=100, scratch_cap=8),
        keep_recent_groups=8, offload_dir=tmp_path / ".vibe" / "offload",
    )
    provider = FakeProvider([
        Response("x" * 20000, usage={"total_tokens": 160000}, finish_reason="length"),
        Response("进度已保存。", usage={"total_tokens": 100}),
    ])
    agent = Agent(provider, ToolRegistry(tmp_path, context=context), context=context,
                  max_run_tokens=200000, tools_enabled=False)

    result = run(agent.run("task"))

    from core.context import total_tokens
    assert result.status == "done"
    assert all(total_tokens(request) <= context.policy.prompt_cap for request in provider.seen)


def test_oversized_pinned_task_never_sends_out_of_window_request(tmp_path):
    context = ContextBuilder(policy=BudgetPolicy.for_context_window(1000),
                             offload_dir=tmp_path / ".vibe" / "offload")
    provider = FakeProvider(["should not be called"])
    agent = Agent(provider, ToolRegistry(tmp_path, context=context), context=context,
                  tools_enabled=False)

    result = run(agent.run("x" * 20000))

    assert provider.seen == []
    assert result.status == "stopped" and result.stop_reason == "context_window"
    assert any(m.name == "context_stop" for m in agent.history)
