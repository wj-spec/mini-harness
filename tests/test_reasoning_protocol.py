import asyncio
import json

import httpx
import pytest

from app.agent import Agent
from app.events import StreamError
from app.providers.openai_compat import OpenAICompatProvider
from app.tools import ToolRegistry
from core.budget import BudgetPolicy
from core.context import ContextBuilder, estimate_tokens
from core.models import Message

TOOLS = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]


def sse(*chunks):
    return "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"


def test_deepseek_compacted_assistant_summary_has_reasoning_field():
    """重放真实第11轮：压缩后出现没有工具调用的assistant摘要。"""
    def handler(request):
        body = json.loads(request.content)
        assert body["tools"] == TOOLS
        for message in body["messages"]:
            if message["role"] == "assistant" and "reasoning_content" not in message:
                return httpx.Response(400, json={"error": {"message": "The `reasoning_content` in the thinking mode must be passed back to the API."}})
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n')

    provider = OpenAICompatProvider("deepseek-flash", "fixture", "https://api.deepseek.com/v1", transport=httpx.MockTransport(handler))
    messages = [Message("user", "Fix issue"), Message("assistant", "[history summary L2] inspected files", name="history_summary_l2")]

    async def scenario():
        events = [event async for event in provider.stream(messages, TOOLS)]
        assert not any(isinstance(event, StreamError) for event in events)

    asyncio.run(scenario())


def test_streamed_reasoning_survives_tool_round_trip_without_becoming_user_text(tmp_path):
    (tmp_path / "a.txt").write_text("source")
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            chunks = [
                {"choices": [{"delta": {"reasoning_content": "inspect "}}]},
                {"choices": [{"delta": {"reasoning_content": "the file", "tool_calls": [{"index": 0, "id": "read-1", "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'}}]}, "finish_reason": "tool_calls"}]},
            ]
        else:
            assistant = next(message for message in body["messages"] if message.get("tool_calls"))
            if assistant.get("reasoning_content") != "inspect the file":
                return httpx.Response(400, json={"error": {"message": "missing reasoning_content"}})
            chunks = [{"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}]
        return httpx.Response(200, text="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n")

    provider = OpenAICompatProvider("deepseek-flash", "fixture", "https://api.deepseek.com/v1", transport=httpx.MockTransport(handler))
    displayed = []
    agent = Agent(provider, ToolRegistry(tmp_path))
    result = asyncio.run(agent.run("read a.txt", on_text=displayed.append))
    assert result.status == "done", result.text
    assert "inspect the file" not in "".join(displayed)
    assert len(seen) == 2


def test_reasoning_in_answer_survives_new_user_turn(tmp_path):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, text=sse({"choices": [{"delta": {
            "reasoning_content": f"analysis-{len(seen)}", "content": "answer",
        }, "finish_reason": "stop"}]}))

    provider = OpenAICompatProvider("deepseek-flash", "fixture", transport=httpx.MockTransport(handler))
    agent = Agent(provider, ToolRegistry(tmp_path))

    async def scenario():
        assert (await agent.run("first question")).status == "done"
        assert (await agent.run("follow up")).status == "done"

    asyncio.run(scenario())
    assistant = next(message for message in seen[1]["messages"] if message["role"] == "assistant")
    assert seen[1]["tools"]
    assert assistant["content"] == "answer"
    assert assistant["reasoning_content"] == "analysis-1"


def test_actual_agent_budget_compaction_sends_valid_summary(tmp_path):
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert body["tools"]
        for message in body["messages"]:
            if message["role"] == "assistant":
                assert isinstance(message.get("reasoning_content"), str)
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}))

    context = ContextBuilder(policy=BudgetPolicy(), keep_recent_groups=2, offload_dir=tmp_path / "offload")
    history = [
        Message("user", "fix parser"),
        Message("assistant", "old source " * 12000, reasoning_content="archived analysis"),
        Message("assistant", "inspect parser", reasoning_content="recent analysis"),
    ]
    provider = OpenAICompatProvider("deepseek-flash", "fixture", transport=httpx.MockTransport(handler))
    events = []
    agent = Agent(provider, ToolRegistry(tmp_path, context=context), context=context,
                  history=history, max_run_tokens=10000)
    result = asyncio.run(agent.run("continue", trace=events.append))

    assert result.status == "done", result.text
    assert any(event["type"] == "history_compacted" and event["reason"] == "request_budget" for event in events)
    summaries = [message for message in seen[0]["messages"] if message["content"].startswith("[history summary L2]")]
    assert summaries and summaries[0]["reasoning_content"] == ""
    assert any(message.get("reasoning_content") == "recent analysis" for message in seen[0]["messages"])


@pytest.mark.parametrize("reasoning", [None, ""])
def test_generic_provider_does_not_add_unrequested_reasoning_fields(reasoning):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, text=sse())

    provider = OpenAICompatProvider("other-model", "fixture", "https://example.test/v1", transport=httpx.MockTransport(handler))
    messages = [Message("assistant", "answer", reasoning_content=reasoning)]

    async def scenario():
        return [event async for event in provider.stream(messages, [])]

    asyncio.run(scenario())
    actual = seen[0]["messages"][0]
    if reasoning is None:
        assert "reasoning_content" not in actual
    else:
        assert actual["reasoning_content"] == ""


def test_restored_reasoning_enables_summary_compatibility_for_model_alias():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, text=sse())

    provider = OpenAICompatProvider("my-model-alias", "fixture", "https://proxy.test/v1", transport=httpx.MockTransport(handler))
    messages = [
        Message("assistant", "[history summary L2] inspected", name="history_summary_l2"),
        Message("assistant", "inspect parser", reasoning_content="restored analysis"),
    ]

    async def scenario():
        return [event async for event in provider.stream(messages, TOOLS)]

    asyncio.run(scenario())
    summary, recent = seen[0]["messages"]
    assert summary.get("reasoning_content") == ""
    assert recent["reasoning_content"] == "restored analysis"


@pytest.mark.parametrize("interruption", ["read_error", "timeout", "cancel"])
def test_partial_stream_keeps_reasoning_and_counts_received_tokens(tmp_path, interruption):
    reasoning = "partial reasoning " * 1000
    displayed = []
    cancel = asyncio.Event()

    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            chunk = {"choices": [{"delta": {"reasoning_content": reasoning, "content": "partial answer"}}]}
            yield ("data: " + json.dumps(chunk) + "\n\n").encode()
            if interruption == "read_error":
                raise httpx.ReadError("fixture disconnect")
            if interruption == "cancel":
                cancel.set()
            await asyncio.Event().wait()

    provider = OpenAICompatProvider("deepseek-flash", "fixture", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=InterruptedStream()),
    ))
    agent = Agent(provider, ToolRegistry(tmp_path), llm_timeout=0.02, llm_retries=0)
    result = asyncio.run(agent.run("inspect parser", on_text=displayed.append, cancel=cancel))

    assert result.status == ("cancelled" if interruption == "cancel" else "error")
    assert displayed == ["partial answer"]
    assert result.usage["completion_tokens"] >= estimate_tokens(reasoning)
    if interruption == "cancel":
        assistant = next(message for message in agent.history if message.role == "assistant")
        assert assistant.reasoning_content == reasoning
