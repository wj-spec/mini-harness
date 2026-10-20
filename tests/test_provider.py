import asyncio
import json

import httpx

from core.models import Message, ToolCall
from app.events import TextDelta, ToolCallsReady
from app.providers.openai_compat import OpenAICompatProvider, _to_openai


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
