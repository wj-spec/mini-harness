import asyncio

from app.events import TextDelta
from app.providers.openai_compat import OpenAICompatProvider
from core.models import Message
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
