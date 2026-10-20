import asyncio

import pytest

from app.agent import Agent
from app.events import StreamDone, TextDelta, ToolCallsReady
from app.policy import ApprovalDecision, ApprovalGate
from app.tools import ToolRegistry
from core.models import ToolCall


class WaitingProvider:
    def __init__(self, events=()):
        self.events = events
        self.entered = asyncio.Event()
        self.closed = asyncio.Event()
        self.consumer = None
        self.calls = 0

    async def stream(self, messages, tools):
        self.calls += 1
        self.consumer = asyncio.current_task()
        try:
            for event in self.events:
                yield event
            self.entered.set()
            await asyncio.Event().wait()
        finally:
            self.closed.set()


async def cancel_waiting(agent, provider, **kwargs):
    cancel = asyncio.Event()
    existing = asyncio.all_tasks()
    task = asyncio.create_task(agent.run("修复问题", cancel=cancel, **kwargs))
    try:
        await asyncio.wait_for(provider.entered.wait(), 1)
        cancel.set()
        finished, _ = await asyncio.wait({task}, timeout=0.2)
        assert task in finished, "取消应中断没有新事件的模型流"
        result = await task
        assert provider.closed.is_set()
        assert provider.consumer.done()
        assert not [pending for pending in asyncio.all_tasks() - existing if not pending.done()]
        return result
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_cancel_interrupts_silent_stream_and_records_estimated_usage(tmp_path):
    async def scenario():
        provider = WaitingProvider()
        agent = Agent(provider, ToolRegistry(tmp_path), llm_timeout=30)
        events = []

        result = await cancel_waiting(agent, provider, trace=events.append)

        assert result.status == "cancelled" and provider.calls == 1
        assert result.usage["prompt_tokens"] > 0
        assert result.usage["total_tokens"] >= result.usage["prompt_tokens"]
        endings = [event for event in events if event["type"] == "llm_end"]
        assert len(endings) == 1 and endings[0]["usage_estimated"]
        assert events[-1]["type"] == "run_end" and events[-1]["status"] == "cancelled"
        assert not any(m.role == "assistant" and not m.content for m in agent.history)

    asyncio.run(scenario())


def test_cancel_preserves_partial_text_and_received_usage(tmp_path):
    async def scenario():
        provider = WaitingProvider([
            TextDelta("已读取代码，"), TextDelta("尚未修改。"),
            StreamDone(usage={"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128}),
        ])
        agent = Agent(provider, ToolRegistry(tmp_path))
        visible, messages, events = [], [], []

        result = await cancel_waiting(agent, provider, on_text=visible.append,
                                      on_message=messages.append, trace=events.append)

        assert result.status == "cancelled" and result.usage["total_tokens"] == 128
        assert "".join(visible) == "已读取代码，尚未修改。"
        assert agent.history[-1].content == "已读取代码，尚未修改。"
        assert messages[-1] == agent.history[-1]
        assert not next(event for event in events if event["type"] == "llm_end")["usage_estimated"]

    asyncio.run(scenario())


@pytest.mark.parametrize("text_protocol", [False, True])
def test_cancel_preserves_tool_requests_without_approval_or_execution(tmp_path, text_protocol):
    async def scenario():
        approvals = []

        async def approve(call, preview):
            approvals.append(call)
            return ApprovalDecision(True, "once")

        if text_protocol:
            events = [TextDelta('<tool_call>{"name":"write_file",'
                                '"arguments":{"path":"a","content":"x"}}</tool_call>')]
        else:
            events = [ToolCallsReady([
                ToolCall("write_file", {"path": "a", "content": "x"}),
                ToolCall("write_file", {"path": "b", "content": "y"}, id="second"),
            ])]
        provider = WaitingProvider(events)
        agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(approve))

        result = await cancel_waiting(agent, provider)

        assert result.status == "cancelled" and approvals == []
        assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists()
        requested = [call for m in agent.history for call in m.tool_calls]
        results = [m for m in agent.history if m.role == "tool"]
        assert requested and all(call.id for call in requested)
        assert {m.tool_call_id for m in results} == {call.id for call in requested}
        assert all("cancelled" in m.content and "not executed" in m.content for m in results)

    asyncio.run(scenario())


@pytest.mark.parametrize("with_cancel_event", [False, True])
def test_stream_timeout_still_closes_consumer_and_retries(tmp_path, with_cancel_event):
    class SlowThenFast:
        def __init__(self):
            self.calls = 0
            self.closed = []

        async def stream(self, messages, tools):
            self.calls += 1
            attempt = self.calls
            try:
                if attempt == 1:
                    await asyncio.Event().wait()
                yield TextDelta("done")
                yield StreamDone(usage={"total_tokens": 10})
            finally:
                self.closed.append(attempt)

    async def scenario():
        provider = SlowThenFast()
        agent = Agent(provider, ToolRegistry(tmp_path), llm_timeout=0.02,
                      llm_retries=1, llm_retry_backoff=0)
        existing = asyncio.all_tasks()

        result = await agent.run("修复问题", cancel=asyncio.Event() if with_cancel_event else None)

        assert result.status == "done" and result.text == "done"
        assert provider.calls == 2 and provider.closed == [1, 2]
        assert result.usage["total_tokens"] > 10
        assert not [pending for pending in asyncio.all_tasks() - existing if not pending.done()]

    asyncio.run(scenario())
