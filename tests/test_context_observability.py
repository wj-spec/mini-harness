import asyncio
import copy
import json
import time

from app.agent import Agent
from app.config import Config
from app.events import StreamDone, StreamError, TextDelta
from app.providers.fake import FakeProvider
from app.session import SessionStore
from app.tools import ToolRegistry, ToolSpec
from core.budget import BudgetPolicy
from core.context import ContextBuilder, estimate_tokens, total_tokens
from core.models import Message, Response, ToolCall
from test_stdio import Harness


def test_no_trace_skips_context_snapshot_construction(tmp_path, monkeypatch):
    def unexpected_snapshot(*args, **kwargs):
        raise AssertionError("snapshot was built without an observer")

    monkeypatch.setattr("app.agent.build_context_snapshot", unexpected_snapshot)
    provider = CapturingProvider(["ok"])
    result = asyncio.run(Agent(provider, ToolRegistry(tmp_path), context=context_for(tmp_path)).run("继续"))

    assert result.status == "done"
    assert len(provider.requests) == 1


def test_snapshot_computation_uses_run_time_without_emitting_or_starting_expired_request(tmp_path, monkeypatch):
    from core.context_snapshot import build_context_snapshot

    def slow_snapshot(*args, **kwargs):
        time.sleep(0.08)
        return build_context_snapshot(*args, **kwargs)

    monkeypatch.setattr("app.agent.build_context_snapshot", slow_snapshot)
    provider = CapturingProvider(["ok"])
    agent = Agent(provider, ToolRegistry(tmp_path), context=context_for(tmp_path),
                  max_run_seconds=0.04, llm_timeout=0.01, llm_retries=0)
    events = []
    result = asyncio.run(agent.run("继续", trace=events.append))

    assert result.status == "stopped"
    assert provider.requests == []
    assert not any(event["type"] in {"context_snapshot", "llm_start", "llm_end"} for event in events)
    assert result.usage["total_tokens"] == 0


def test_snapshot_observer_cancellation_prevents_model_call(tmp_path):
    async def scenario():
        provider = CapturingProvider(["ok"])
        cancel = asyncio.Event()
        events = []

        def observe(event):
            events.append(event)
            if event["type"] == "context_snapshot":
                cancel.set()

        result = await Agent(provider, ToolRegistry(tmp_path), context=context_for(tmp_path)).run(
            "继续", trace=observe, cancel=cancel,
        )

        assert result.status == "cancelled"
        assert provider.requests == []
        assert not any(event["type"] in {"llm_start", "llm_end"} for event in events)
        assert result.usage["total_tokens"] == 0

    asyncio.run(scenario())


def test_snapshot_work_does_not_consume_model_request_timeout(tmp_path, monkeypatch):
    from core.context_snapshot import build_context_snapshot

    def slow_snapshot(*args, **kwargs):
        time.sleep(0.08)
        return build_context_snapshot(*args, **kwargs)

    class YieldingProvider(CapturingProvider):
        async def stream(self, messages, tools):
            await asyncio.sleep(0.001)
            async for event in super().stream(messages, tools):
                yield event

    monkeypatch.setattr("app.agent.build_context_snapshot", slow_snapshot)
    provider = YieldingProvider(["ok"])
    events = []
    agent = Agent(provider, ToolRegistry(tmp_path), context=context_for(tmp_path),
                  max_run_seconds=1, llm_timeout=0.03, llm_retries=0)
    result = asyncio.run(agent.run("继续", trace=events.append))

    assert result.status == "done"
    assert len(provider.requests) == 1
    assert len([event for event in events if event["type"] == "llm_start"]) == 1


class CapturingProvider(FakeProvider):
    def __init__(self, script):
        super().__init__(script)
        self.requests = []

    async def stream(self, messages, tools):
        self.requests.append((copy.deepcopy(messages), copy.deepcopy(tools)))
        async for event in super().stream(messages, tools):
            yield event


def context_for(tmp_path):
    return ContextBuilder(policy=BudgetPolicy(), keep_recent_groups=1,
                          offload_dir=tmp_path / ".vibe" / "offload")


def assert_request_matches(snapshot, messages, tools):
    expected = total_tokens(messages) + estimate_tokens(json.dumps(tools, ensure_ascii=False))
    assert snapshot["total_tokens"] == expected
    assert set(snapshot["sections"]) == {"system", "memory", "tools", "history"}
    assert sum(snapshot["sections"].values()) == expected
    assert sum(block["tokens"] for block in snapshot["blocks"]) == expected
    assert snapshot["estimated"] is True


def test_snapshots_match_final_requests_and_keep_usage_separate(tmp_path):
    (tmp_path / "source.txt").write_text("实际文件内容")
    provider = CapturingProvider([
        Response(tool_calls=[ToolCall("read_file", {"path": "source.txt"}, "read")],
                 usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}),
        Response("完成", usage={"prompt_tokens": 200, "completion_tokens": 30, "total_tokens": 230}),
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), context=context_for(tmp_path),
                  system="基础指令", project_instructions="项目规则")
    events = []
    result = asyncio.run(agent.run("读取文件", trace=events.append))

    snapshots = [event for event in events if event["type"] == "context_snapshot"]
    assert len(snapshots) == len(provider.requests) == 2
    for index, (snapshot, (messages, tools)) in enumerate(zip(snapshots, provider.requests)):
        assert_request_matches(snapshot, messages, tools)
        assert snapshot["phase"] == "request" and snapshot["step"] == index + 1
        assert snapshot["run_id"] == result.run_id
        end = [e for e in events if e["type"] == "llm_end"][index]
        assert end["request_id"] == snapshot["request_id"]
        assert events.index(snapshot) < events.index(end)
    kinds = {block["kind"] for block in snapshots[0]["blocks"]}
    assert {"base_instructions", "project_instructions", "task_state"} <= kinds
    assert any(block["kind"] == "tool_result" for block in snapshots[1]["blocks"])
    assert snapshots[1]["delta_tokens"] == snapshots[1]["total_tokens"] - snapshots[0]["total_tokens"]
    assert result.usage["total_tokens"] == 350
    assert "实际文件内容" not in json.dumps(snapshots, ensure_ascii=False)


def test_stdio_sends_and_persists_the_same_normalized_snapshot(tmp_path):
    async def scenario():
        session = SessionStore.new(tmp_path, "fake")
        agent = Agent(FakeProvider(["ok"]), ToolRegistry(tmp_path), context=context_for(tmp_path))
        harness = Harness(agent, Config(workspace=tmp_path), session)
        harness.send("run", "run-one", text="查看上下文")
        done = await harness.until("done", "run-one")
        assert done["result"]["status"] == "done"
        snapshots = [event for event in harness.events
                     if event["type"] == "context_snapshot" and event["id"] == "run-one"]
        assert len(snapshots) == 1 and snapshots[0]["id"] == "run-one"
        snapshot = snapshots[0]["snapshot"]
        trace = [json.loads(line) for line in session.trace_path.read_text().splitlines()]
        assert [event for event in trace if event["type"] == "context_snapshot"
                and event["phase"] == "request"] == [snapshot]
        metrics = [event for event in harness.events if event["type"] == "metrics"]
        assert metrics[-1]["context_tokens"] == snapshot["total_tokens"]
        assert metrics[-1]["used_tokens"] == done["result"]["usage"]["total_tokens"]
        await harness.close()

    asyncio.run(scenario())


def test_compaction_snapshots_use_the_same_four_partitions_and_show_released_space(tmp_path):
    history = [Message("user", "保留原始任务")]
    for index in range(6):
        history.extend([
            Message("assistant", tool_calls=[ToolCall("inspect", {"index": index}, f"old-{index}")]),
            Message("tool", "旧文件正文 " * 3000, name="inspect", tool_call_id=f"old-{index}"),
        ])
    tools = ToolRegistry(tmp_path)
    tools.register(ToolSpec("inspect", "inspect", {"type": "object"}, lambda index: f"new {index}"))
    provider = CapturingProvider([
        Response(tool_calls=[ToolCall("inspect", {"index": 9}, "new")], usage={"total_tokens": 160000}),
        Response("继续完成", usage={"total_tokens": 10}),
    ])
    events = []
    result = asyncio.run(Agent(provider, tools, context=context_for(tmp_path), history=history).run(
        "继续", trace=events.append,
    ))
    snapshots = [event for event in events if event["type"] == "context_snapshot"]
    before = next(event for event in snapshots if event["phase"] == "before_compaction")
    after = next(event for event in snapshots if event["phase"] == "after_compaction")
    assert result.status == "done" and result.usage["total_tokens"] == 160010
    assert before["reason"] == after["reason"] == "interval"
    assert before["sections"]["tools"] == after["sections"]["tools"] > 0
    assert after["sections"]["history"] < before["sections"]["history"]
    assert after["delta_tokens"] == after["total_tokens"] - before["total_tokens"] < 0
    assert len({event["snapshot_id"] for event in snapshots}) == len(snapshots)
    assert all(set(event["sections"]) == {"system", "memory", "tools", "history"} for event in snapshots)
    assert any(block["kind"] == "history_summary" for block in after["blocks"])


def test_correction_and_tool_free_checkpoint_are_counted_as_actually_sent(tmp_path):
    tools = ToolRegistry(tmp_path)
    tools.register(ToolSpec("inspect", "inspect", {"type": "object"}, lambda: "unchanged"))
    provider = CapturingProvider([
        *[Response(tool_calls=[ToolCall("inspect", {}, f"repeat-{index}")], usage={"total_tokens": 10})
          for index in range(5)],
        Response("需要调整方法", usage={"total_tokens": 10}),
    ])
    events = []
    result = asyncio.run(Agent(provider, tools, context=context_for(tmp_path)).run("完成任务", trace=events.append))
    requests = [event for event in events if event["type"] == "context_snapshot" and event["phase"] == "request"]
    assert result.stop_reason == "no_progress" and len(requests) == len(provider.requests) == 6
    correction_seen = False
    for snapshot, (messages, tool_specs) in zip(requests, provider.requests):
        assert_request_matches(snapshot, messages, tool_specs)
        correction_seen |= any("Harness feedback" in message.content for message in messages)
    assert correction_seen
    assert provider.requests[-1][1] == [] and requests[-1]["reason"] == "finalizing"
    assert not any(block["kind"] == "tool_definition" for block in requests[-1]["blocks"])


def test_retries_have_distinct_ids_and_fresh_snapshots(tmp_path):
    class RetryProvider:
        attempts = 0

        async def stream(self, messages, tools):
            self.attempts += 1
            if self.attempts == 1:
                yield StreamError("temporary", retryable=True)
            else:
                yield TextDelta("ok")
            yield StreamDone(usage={"prompt_tokens": 100, "completion_tokens": 1, "total_tokens": 101})

    provider = RetryProvider()
    agent = Agent(provider, ToolRegistry(tmp_path), context=context_for(tmp_path), llm_retry_backoff=0)
    events = []
    result = asyncio.run(agent.run("继续", trace=events.append))
    snapshots = [event for event in events if event["type"] == "context_snapshot"]
    assert result.status == "done" and provider.attempts == len(snapshots) == 2
    assert [event["attempt"] for event in snapshots] == [0, 1]
    assert snapshots[0]["request_id"] != snapshots[1]["request_id"]
    assert snapshots[0]["total_tokens"] == snapshots[1]["total_tokens"]
    assert snapshots[1]["delta_tokens"] == 0
    assert [event["request_id"] for event in events if event["type"] == "llm_end"] == [
        snapshot["request_id"] for snapshot in snapshots
    ]
