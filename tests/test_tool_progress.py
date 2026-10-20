import asyncio
import pytest

from app.agent import Agent
from app.config import Config
from app.events import StreamError
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry, ToolSpec
from core.models import Response, ToolCall
from core.budget import BudgetPolicy
from core.context import ContextBuilder
from core.models import Message


def test_sixty_productive_tool_calls_can_complete(tmp_path):
    executed = []
    registry = ToolRegistry(tmp_path)

    def inspect(index):
        executed.append(index)
        return f"new evidence {index}"

    registry.register(ToolSpec("inspect", "Inspect another item", {"type": "object"}, inspect))
    script = [Response(tool_calls=[ToolCall("inspect", {"index": i}, id=f"c{i}")],
                       usage={"total_tokens": 10}) for i in range(60)]
    provider = FakeProvider(script + [Response("done", usage={"total_tokens": 10})])

    result = asyncio.run(Agent(provider, registry, max_steps=70).run("inspect all items"))

    assert result.status == "done", result.text
    assert executed == list(range(60))


def test_default_runtime_has_no_tool_or_model_turn_count_cap():
    config = Config()

    assert not hasattr(config, "max_tool_calls")
    assert config.max_steps == 0


def test_same_request_with_changing_results_is_progress(tmp_path):
    executed = []
    registry = ToolRegistry(tmp_path)

    def poll():
        executed.append(1)
        return f"completed items: {len(executed)}"

    registry.register(ToolSpec("poll", "Read changing progress", {"type": "object"}, poll))
    provider = FakeProvider([
        *[Response(tool_calls=[ToolCall("poll", {}, id=f"poll{i}")]) for i in range(5)],
        "done",
    ])

    result = asyncio.run(Agent(provider, registry).run("wait for progress"))

    assert result.status == "done", result.text
    assert len(executed) == 5


def repeated_calls(count, name="inspect", args=None):
    return [Response(tool_calls=[ToolCall(name, args or {}, id=f"call{i}")],
                     usage={"total_tokens": 10}) for i in range(count)]


def test_unchanged_results_warn_then_use_one_tool_free_checkpoint(tmp_path):
    executed = []
    registry = ToolRegistry(tmp_path)
    registry.register(ToolSpec("inspect", "Inspect", {"type": "object"},
                               lambda: executed.append(1) or "unchanged"))
    provider = FakeProvider(repeated_calls(5) + [Response("受阻，尚未完成。", usage={"total_tokens": 10})])
    agent = Agent(provider, registry)
    events = []

    result = asyncio.run(agent.run("fix", trace=events.append))

    assert result.status == "stopped" and result.stop_reason == "no_progress"
    assert len(executed) == 5 and len(provider.seen) == 6
    assert all(provider.seen_tools[:-1]) and provider.seen_tools[-1] == []
    assert [e["action"] for e in events if e["type"] == "no_progress"] == ["warn", "stop"]
    assert "受阻" in result.text
    assert any(m.name and "progress" in m.name for m in agent.history)


def test_changing_method_after_warning_can_finish(tmp_path):
    registry = ToolRegistry(tmp_path)
    registry.register(ToolSpec("inspect", "Inspect", {"type": "object"}, lambda item=0: f"item {item}"))
    provider = FakeProvider(repeated_calls(3) + [
        Response(tool_calls=[ToolCall("inspect", {"item": 1}, id="new")]), "done",
    ])
    events = []

    result = asyncio.run(Agent(provider, registry).run("fix", trace=events.append))

    assert result.status == "done" and all(provider.seen_tools)
    assert [e["action"] for e in events if e["type"] == "no_progress"] == ["warn"]


def test_different_failed_requests_are_not_new_progress(tmp_path):
    registry = ToolRegistry(tmp_path)

    def inspect(item):
        raise FileNotFoundError(f"missing-{item}")

    registry.register(ToolSpec("inspect", "Inspect", {"type": "object"}, inspect))
    provider = FakeProvider([
        *[Response(tool_calls=[ToolCall("inspect", {"item": i}, id=f"c{i}")]) for i in range(4)],
        "需要提供正确文件位置。",
    ])

    result = asyncio.run(Agent(provider, registry).run("fix"))

    assert result.status == "stopped" and result.stop_reason == "no_progress"
    assert len(provider.seen) == 5 and provider.seen_tools[-1] == []


def test_warning_interrupts_batch_without_executing_unreviewed_tail(tmp_path):
    approvals = []

    async def approve(call, preview):
        approvals.append(call.name)
        return ApprovalDecision(True, "once")

    registry = ToolRegistry(tmp_path)
    registry.register(ToolSpec("inspect", "Inspect", {"type": "object"}, lambda item=0: f"item {item}"))
    calls = [ToolCall("inspect", {}, id=f"read{i}") for i in range(3)]
    calls.append(ToolCall("write_file", {"path": "unexecuted", "content": "x"}, id="tail"))
    provider = FakeProvider([Response(tool_calls=calls),
                             Response(tool_calls=[ToolCall("inspect", {"item": 1}, id="new")]), "done"])
    agent = Agent(provider, registry, approval=ApprovalGate(approve))

    result = asyncio.run(agent.run("fix"))

    assert result.status == "done"
    assert not (tmp_path / "unexecuted").exists() and approvals == []
    assert any(m.tool_call_id == "tail" and "not_executed" in m.content for m in agent.history)
    requested = {call.id for message in agent.history for call in message.tool_calls}
    answered = {message.tool_call_id for message in agent.history if message.role == "tool"}
    assert requested == answered


@pytest.mark.parametrize("checkpoint", [
    Response(""),
    Response("partial", finish_reason="length"),
    Response(tool_calls=[ToolCall("write_file", {"path": "never", "content": "x"}, id="write")]),
    Response('<tool_call>{"name":"write_file","arguments":{"path":"never","content":"x"}}</tool_call>'),
])
def test_degraded_checkpoint_never_resumes_or_executes_tools(tmp_path, checkpoint):
    registry = ToolRegistry(tmp_path)
    registry.register(ToolSpec("inspect", "Inspect", {"type": "object"}, lambda: "same"))
    provider = FakeProvider(repeated_calls(5) + [checkpoint, "must not continue"])
    agent = Agent(provider, registry)

    result = asyncio.run(agent.run("fix"))

    assert result.status == "stopped" and result.stop_reason == "no_progress"
    assert len(provider.seen) == 6 and not (tmp_path / "never").exists()
    requested = {call.id for message in agent.history for call in message.tool_calls}
    answered = {message.tool_call_id for message in agent.history if message.role == "tool"}
    assert requested == answered


def test_denied_operation_is_not_prompted_or_executed_again(tmp_path):
    approvals = []

    async def deny(call, preview):
        approvals.append(call.name)
        return ApprovalDecision(False, "deny")

    provider = FakeProvider(repeated_calls(2, "write_file", {"path": "never", "content": "x"}) + ["操作被拒绝，需要用户处理。"])
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(deny))

    result = asyncio.run(agent.run("fix"))

    assert result.status == "stopped" and result.stop_reason == "no_progress"
    assert approvals == ["write_file"] and not (tmp_path / "never").exists()


def test_progress_checkpoint_error_is_saved_without_retry(tmp_path):
    class Provider(FakeProvider):
        async def stream(self, messages, tools):
            if self.i < 5:
                async for event in super().stream(messages, tools):
                    yield event
            else:
                self.seen.append(list(messages))
                self.seen_tools.append(list(tools))
                yield StreamError("connection lost", retryable=True)

    registry = ToolRegistry(tmp_path)
    registry.register(ToolSpec("inspect", "Inspect", {"type": "object"}, lambda: "same"))
    provider = Provider(repeated_calls(5))

    result = asyncio.run(Agent(provider, registry, llm_retry_backoff=0).run("fix"))

    assert result.status == "stopped" and result.stop_reason == "no_progress"
    assert len(provider.seen) == 6
    assert "connection lost" in result.text


def test_warning_survives_compaction_without_replacing_user_instructions(tmp_path):
    context = ContextBuilder(policy=BudgetPolicy(), keep_recent_groups=1,
                             offload_dir=tmp_path / ".vibe" / "offload")
    registry = ToolRegistry(tmp_path, context=context)
    registry.register(ToolSpec("inspect", "Inspect", {"type": "object"}, lambda item=0: f"item {item}"))
    instruction = "保留公开接口，只改内部实现。"
    provider = FakeProvider(repeated_calls(3) + [
        Response(tool_calls=[ToolCall("inspect", {"item": 1}, id="new")], usage={"total_tokens": 10}),
        "done",
    ])
    agent = Agent(provider, registry, context=context, compact_interval_tokens=30,
                  history=[Message("user", "修复 bug"), Message("assistant", "old detail " * 3000),
                           Message("user", instruction)])
    events = []

    result = asyncio.run(agent.run(None, trace=events.append))

    assert result.status == "done"
    assert any(e["type"] == "history_compacted" for e in events)
    assert any(m.role == "user" and m.content == instruction for m in provider.seen[3])
    assert any("Change the method" in m.content for m in provider.seen[3])
    assert provider.seen_tools[3]


def test_cancel_during_degraded_compaction_never_starts_checkpoint_request(tmp_path):
    async def scenario():
        cancel = asyncio.Event()
        loop = asyncio.get_running_loop()

        class CancellingContext(ContextBuilder):
            def compact_history(self, *args, **kwargs):
                result = super().compact_history(*args, **kwargs)
                loop.call_soon_threadsafe(cancel.set)
                return result

        context = CancellingContext(policy=BudgetPolicy(), keep_recent_groups=1,
                                    offload_dir=tmp_path / ".vibe" / "offload")
        registry = ToolRegistry(tmp_path, context=context)
        registry.register(ToolSpec("inspect", "Inspect", {"type": "object"}, lambda: "same"))
        provider = FakeProvider(repeated_calls(5) + ["must not start"])

        result = await Agent(provider, registry, context=context).run("fix", cancel=cancel)

        assert result.status == "cancelled"
        assert len(provider.seen) == 5

    asyncio.run(scenario())
