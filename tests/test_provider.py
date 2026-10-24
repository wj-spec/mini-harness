import asyncio
import json

import httpx

from core.models import Message, ToolCall
from app.events import TextDelta, ToolCallsReady
from app.providers.openai_compat import OpenAICompatProvider, _to_openai


def sse(chunks, done=True):
    body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
    return body + ("data: [DONE]\n\n" if done else "")


def _collect(provider, messages=None):
    async def go():
        events = []
        async for event in provider.stream(messages or [Message("user", "hi")], []):
            events.append(event)
        return events
    return asyncio.run(go())


def _tool_call_chunk(index, call_id, name, arguments):
    return {"choices": [{"delta": {"tool_calls": [
        {"index": index, "id": call_id, "function": {"name": name, "arguments": arguments}},
    ]}}]}


def _handler(body):
    return lambda request: httpx.Response(200, content=body)


def test_to_openai_keeps_tool_call_id():
    message = Message("tool", "ok", name="read_file", tool_call_id="c1")
    data = _to_openai(message)
    assert data["role"] == "tool"
    assert data["tool_call_id"] == "c1"


def test_stream_parses_text_and_tool_calls():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        chunks = [
            {"choices": [{"delta": {"content": "hi"}}]},
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "c1",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": json.dumps({"path": "a.txt"}),
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        ]
        body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
        body += "data: [DONE]\n\n"
        return httpx.Response(200, content=body)

    provider = OpenAICompatProvider(
        "model-x", "key", "http://example/v1", transport=httpx.MockTransport(handler)
    )

    async def collect():
        events = []
        async for event in provider.stream([Message("user", "hi")], []):
            events.append(event)
        return events

    events = asyncio.run(collect())
    assert any(isinstance(e, TextDelta) and e.text == "hi" for e in events)
    calls = [e for e in events if isinstance(e, ToolCallsReady)]
    assert calls and calls[0].calls[0].name == "read_file"
    assert calls[0].calls[0].args == {"path": "a.txt"}
    assert calls[0].calls[0].id == "c1"
    assert captured["body"]["model"] == "model-x"
    assert captured["body"]["stream"] is True


def test_stream_captures_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            {"choices": [{"delta": {"content": "hi"}}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        ]
        body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
        body += "data: [DONE]\n\n"
        return httpx.Response(200, content=body)

    provider = OpenAICompatProvider(
        "m", "k", "http://example/v1", transport=httpx.MockTransport(handler)
    )

    async def collect():
        return [e async for e in provider.stream([Message("user", "hi")], [])]

    events = asyncio.run(collect())
    from app.events import StreamDone
    done = [e for e in events if isinstance(e, StreamDone)][0]
    assert done.usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def test_incomplete_tool_calls_are_not_executable():
    body = sse([
        _tool_call_chunk(0, "c1", "read_file", '{"path": "a.txt"}'),
        _tool_call_chunk(1, "c2", "write_file", '{"path": "b.txt", "cont'),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ])
    provider = OpenAICompatProvider("m", "k", "http://example/v1", transport=httpx.MockTransport(_handler(body)))
    events = _collect(provider)
    ready = [e for e in events if isinstance(e, ToolCallsReady)][0]
    assert [c.id for c in ready.calls] == ["c1"]
    assert [c.id for c in ready.incomplete] == ["c2"]
    assert ready.incomplete[0].name == "write_file"


def test_stream_without_done_or_finish_reason_is_truncated():
    body = sse([
        {"choices": [{"delta": {"content": "partial"}}]},
        _tool_call_chunk(0, "c1", "read_file", '{"path": "a.txt"}'),
    ], done=False)
    provider = OpenAICompatProvider("m", "k", "http://example/v1", transport=httpx.MockTransport(_handler(body)))
    events = _collect(provider)
    ready = [e for e in events if isinstance(e, ToolCallsReady)][0]
    assert ready.calls == []
    assert [c.id for c in ready.incomplete] == ["c1"]
    done = [e for e in events if hasattr(e, "finish_reason") and not isinstance(e, ToolCallsReady)][-1]
    assert done.finish_reason == "truncated"


def test_empty_arguments_only_completes_with_tool_calls_finish():
    body = sse([
        _tool_call_chunk(0, "c1", "list_files", ""),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ])
    provider = OpenAICompatProvider("m", "k", "http://example/v1", transport=httpx.MockTransport(_handler(body)))
    ready = [e for e in _collect(provider) if isinstance(e, ToolCallsReady)][0]
    assert [c.args for c in ready.calls] == [{}]
    assert ready.incomplete == []

    body2 = sse([_tool_call_chunk(0, "c1", "list_files", "")], done=False)
    provider2 = OpenAICompatProvider("m", "k", "http://example/v1", transport=httpx.MockTransport(_handler(body2)))
    ready2 = [e for e in _collect(provider2) if isinstance(e, ToolCallsReady)][0]
    assert ready2.calls == []
    assert [c.id for c in ready2.incomplete] == ["c1"]
