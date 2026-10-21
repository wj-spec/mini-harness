import asyncio
import json
import math

import pytest

from app.agent import Agent
from app.config import Config
from app.events import StreamDone, StreamError, TextDelta, ToolCallsReady
from app.policy import ApprovalGate
from app.providers.fake import FakeProvider
from app.providers.openai_compat import OpenAICompatProvider
from app.session import SessionStore
from app.trace import Pricing
from app.tools import ToolRegistry
from core.budget import BudgetPolicy
from core.context import ContextBuilder
from core.models import Message, Response, ToolCall
from core.state import TaskState
from test_stdio import Harness


def metrics_for(harness, request_id):
    return [e for e in harness.events if e["type"] == "metrics" and e["id"] == request_id]


def check_numeric_metrics(events):
    for event in events:
        for key in ("used_tokens", "limit_tokens", "context_window", "done", "planned"):
            assert type(event[key]) is int and event[key] >= 0
        assert type(event["estimated"]) is bool
        assert event["context_tokens"] is None or type(event["context_tokens"]) is int
        assert type(event["cost_usd"]) in (int, float) and math.isfinite(event["cost_usd"])


def test_metrics_use_actual_trace_context_state_and_final_result(tmp_path):
    async def scenario():
        state = TaskState(goal="")
        context = ContextBuilder(max_tokens=8000)
        provider = FakeProvider([
            Response(tool_calls=[ToolCall("update_task", {
                "plan": ["read", "test"], "done": ["read"],
            }, "plan")], usage={"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}),
            Response(content="完成", usage={"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}),
        ])
        agent = Agent(
            provider, ToolRegistry(tmp_path, state=state), state=state, context=context,
            max_run_tokens=10000, pricing=Pricing(input_per_1m=1, output_per_1m=2),
        )
        config = Config(workspace=tmp_path, context_window=12000, max_run_tokens=99999)
        session = SessionStore.new(tmp_path, "fake")
        h = Harness(agent, config, session)
        await h.until("ready")
        h.send("run", "r1", text="规划")
        done = await h.until("done", "r1")
        assert done["result"]["status"] == "done"
        metrics = metrics_for(h, "r1")
        assert metrics, "backend must publish metrics"
        assert metrics[0]["used_tokens"] == 0 and metrics[0]["context_tokens"] is None
        final = metrics[-1]
        assert final["used_tokens"] == done["result"]["usage"]["total_tokens"] == 38
        assert final["limit_tokens"] == 10000
        assert final["estimated"] is False
        assert final["context_window"] == 12000
        assert final["done"] == 1 and final["planned"] == 2
        assert final["cost_usd"] == pytest.approx(done["result"]["cost_usd"]) == pytest.approx(46 / 1_000_000)
        events = [json.loads(line) for line in session.events_path.read_text().splitlines()]
        contexts = [e for e in events if e["type"] == "context"]
        budget = [json.loads(line) for line in session.trace_path.read_text().splitlines()]
        snapshots = [e for e in budget if e["type"] == "context_snapshot"]
        assert final["context_tokens"] == snapshots[-1]["total_tokens"] > 0
        # 旧 flat 回调不含工具定义，新指标以完整请求快照为准。
        assert final["context_tokens"] > contexts[-1]["total_tokens"]
        assert any(e["type"] == "budget_update" and e["used_tokens"] == 38 for e in budget)
        assert session.load_state().done == ["read"]
        assert session.load_context() == agent.history
        assert h.events[h.events.index(done) - 1] == final
        check_numeric_metrics(metrics)
        await h.close()

    asyncio.run(scenario())


def test_estimated_usage_is_marked_and_not_emitted_per_text_delta(tmp_path):
    async def scenario():
        agent = Agent(FakeProvider(["x" * 2048]), ToolRegistry(tmp_path), max_run_tokens=50000)
        h = Harness(agent, Config(workspace=tmp_path))
        h.send("run", "r1", text="流式")
        done = await h.until("done", "r1")
        metrics = metrics_for(h, "r1")
        assert metrics[-1]["estimated"] is True
        assert metrics[-1]["used_tokens"] == done["result"]["usage"]["total_tokens"] > 0
        assert metrics[-1]["context_tokens"] is None
        assert len(metrics) <= 5
        assert len([e for e in h.events if e["type"] == "text_delta"]) > 100
        check_numeric_metrics(metrics)
        await h.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("end_mode", ["cancel", "deny", "error"])
def test_metrics_do_not_break_approval_cancel_error_or_saving(tmp_path, end_mode):
    class Provider:
        async def stream(self, messages, tools):
            if end_mode == "error":
                yield StreamError("model failed", retryable=False)
            elif any(m.role == "tool" for m in messages):
                yield TextDelta("已拒绝")
            else:
                yield ToolCallsReady([ToolCall("write_file", {"path": "out.txt", "content": "no"}, "w1")])
            yield StreamDone(usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})

    async def scenario():
        session = SessionStore.new(tmp_path, "custom")
        agent = Agent(Provider(), ToolRegistry(tmp_path), approval=ApprovalGate(), max_run_tokens=10000)
        h = Harness(agent, Config(workspace=tmp_path), session)
        h.send("run", "r1", text="写入")
        if end_mode != "error":
            request = await h.until("approval_request")
            assert metrics_for(h, "r1")[-1]["used_tokens"] == 12
            assert not (tmp_path / "out.txt").exists()
            if end_mode == "cancel":
                h.send("cancel", "c1", target="r1")
            else:
                h.send("approval", "a1", approval_id=request["approval_id"], decision="deny")
        done = await h.until("done", "r1")
        assert done["result"]["status"] == {"cancel": "cancelled", "deny": "done", "error": "error"}[end_mode]
        final = metrics_for(h, "r1")[-1]
        assert final["used_tokens"] == done["result"]["usage"]["total_tokens"]
        assert not (tmp_path / "out.txt").exists()
        assert session.load_state() is not None and session.load_context() == agent.history
        assert session.latest_run()["status"] == done["result"]["status"]
        check_numeric_metrics(metrics_for(h, "r1"))
        await h.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("provider_kind, expected", [("fake", "fake"), ("openai", "openai")])
def test_ready_identifies_actual_provider_and_session_directory(tmp_path, provider_kind, expected):
    async def scenario():
        provider = FakeProvider(["ok"]) if provider_kind == "fake" else OpenAICompatProvider("model", "secret")
        config = Config(workspace=tmp_path, provider="different-label")
        session = SessionStore.new(tmp_path, "model")
        h = Harness(Agent(provider, ToolRegistry(tmp_path)), config, session)
        ready = await h.until("ready")
        assert ready["provider"] == expected
        assert ready["session_path"] == str(session.root.resolve())
        assert "metrics" in ready["capabilities"]
        assert "secret" not in json.dumps(ready)
        await h.close()

    asyncio.run(scenario())


def test_each_run_metrics_start_at_zero_without_losing_saved_session_usage(tmp_path):
    async def scenario():
        provider = FakeProvider([
            Response(content="first", usage={"total_tokens": 13}),
            Response(content="second", usage={"total_tokens": 7}),
        ])
        session = SessionStore.new(tmp_path, "fake")
        agent = Agent(provider, ToolRegistry(tmp_path), max_run_tokens=10000)
        h = Harness(agent, Config(workspace=tmp_path), session)
        h.send("run", "r1", text="first")
        await h.until("done", "r1")
        h.send("run", "r2", text="second")
        await h.until("done", "r2")
        assert metrics_for(h, "r1")[-1]["used_tokens"] == 13
        assert metrics_for(h, "r2")[0]["used_tokens"] == 0
        assert metrics_for(h, "r2")[-1]["used_tokens"] == 7
        assert session.cost_summary()["total_tokens"] == 20
        await h.close()

    asyncio.run(scenario())


def test_metrics_compaction_keeps_cumulative_budget_and_saves_context(tmp_path):
    async def scenario():
        (tmp_path / "first.txt").write_text("first source " * 4000)
        (tmp_path / "second.txt").write_text("second source " * 4000)
        history = [Message("user", "保留审批"), Message("assistant", "old source " * 8000)]
        context = ContextBuilder(
            policy=BudgetPolicy(), keep_recent_groups=1,
            offload_dir=tmp_path / ".vibe" / "offload",
        )
        provider = FakeProvider([
            Response(tool_calls=[ToolCall("read_file", {"path": "first.txt"}, "first")],
                     usage={"total_tokens": 160000}),
            Response(tool_calls=[ToolCall("read_file", {"path": "second.txt"}, "second")],
                     usage={"total_tokens": 160000}),
            Response(content="done", usage={"total_tokens": 100}),
        ])
        session = SessionStore.new(tmp_path, "fake")
        for message in history:
            session.append_message(message)
        agent = Agent(
            provider, ToolRegistry(tmp_path, context=context), context=context, history=history,
            max_run_tokens=1000000, compact_interval_tokens=200000,
        )
        h = Harness(agent, Config(workspace=tmp_path), session)
        h.send("run", "r1", text="继续")
        done = await h.until("done", "r1")
        assert done["result"]["status"] == "done"
        used = [e["used_tokens"] for e in metrics_for(h, "r1")]
        assert used == sorted(used) and used[-1] == 320100
        trace = [json.loads(line) for line in session.trace_path.read_text().splitlines()]
        assert len([e for e in trace if e["type"] == "history_compacted"]) == 2
        assert session.load_context() == agent.history
        check_numeric_metrics(metrics_for(h, "r1"))
        await h.close()

    asyncio.run(scenario())
