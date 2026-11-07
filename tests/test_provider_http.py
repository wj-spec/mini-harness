import asyncio

from app.events import TextDelta
from app.providers.openai_compat import OpenAICompatProvider
from core.contracts import Message
from dev.mock_openai_server import start


def test_provider_over_real_http(tmp_path):
    server, port = start()
    try:
        debug = tmp_path / "provider.jsonl"
        provider = OpenAICompatProvider(
            "mock", "x", f"http://127.0.0.1:{port}/v1", debug_path=debug
        )

        async def collect():
            events = []
            async for event in provider.stream([Message("user", "你好")], []):
                events.append(event)
            return events

        events = asyncio.run(collect())
        text = "".join(e.text for e in events if isinstance(e, TextDelta))
        assert "mock" in text
        assert "流式" in text
        assert '"type": "request"' in debug.read_text()
        assert '"type": "chunk"' in debug.read_text()
    finally:
        server.shutdown()
        server.server_close()


def test_provider_marks_http_5xx_retryable_and_recovers():
    from app.events import StreamError

    server, port = start(fail_statuses=[503])
    try:
        provider = OpenAICompatProvider("mock", "x", f"http://127.0.0.1:{port}/v1")

        async def collect():
            events = []
            async for event in provider.stream([Message("user", "你好")], []):
                events.append(event)
            return events

        first = asyncio.run(collect())
        assert any(isinstance(event, StreamError) and event.retryable for event in first)
        second = asyncio.run(collect())
        text = "".join(e.text for e in second if isinstance(e, TextDelta))
        assert "mock" in text
        assert len(server.request_log) == 2  # type: ignore[attr-defined]
    finally:
        server.shutdown()
        server.server_close()


def test_provider_marks_http_4xx_not_retryable():
    from app.events import StreamError

    server, port = start(fail_statuses=[400])
    try:
        provider = OpenAICompatProvider("mock", "x", f"http://127.0.0.1:{port}/v1")

        async def collect():
            events = []
            async for event in provider.stream([Message("user", "你好")], []):
                events.append(event)
            return events

        events = asyncio.run(collect())
        errors = [event for event in events if isinstance(event, StreamError)]
        assert errors and errors[0].retryable is False
        assert "injected 400" in errors[0].message
    finally:
        server.shutdown()
        server.server_close()


def test_agent_retries_after_real_http_503(tmp_path):
    from app.runtime.agent import Agent
    from app.safety.policy import ApprovalGate
    from app.tools.registry import ToolRegistry

    server, port = start(fail_statuses=[503])
    try:
        provider = OpenAICompatProvider("mock", "x", f"http://127.0.0.1:{port}/v1")
        agent = Agent(
            provider, ToolRegistry(tmp_path), approval=ApprovalGate(),
            max_steps=3, llm_retries=1, llm_retry_backoff=0.01,
        )
        result = asyncio.run(agent.run("你好"))
        assert result.status == "done"
        assert "我是 mock 模型" in result.text
        assert len(server.request_log) == 2  # type: ignore[attr-defined]
    finally:
        server.shutdown()
        server.server_close()
