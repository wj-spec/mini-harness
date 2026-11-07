from __future__ import annotations

from app import __main__ as entrypoint
from app.config import Config


def test_build_provider_uses_llm_timeout(monkeypatch):
    monkeypatch.setenv("VIBE_API_KEY", "sk-test")
    config = Config.from_env()
    config.llm_timeout = 7.0
    provider = entrypoint.build_provider(config, False)
    assert provider.timeout == 7.0


def test_build_provider_injects_fake_stream_errors(monkeypatch):
    import asyncio

    from app.events import StreamError, TextDelta
    from core.contracts import Message

    monkeypatch.setenv("VIBE_FAKE_STREAM_ERRORS", "2")
    config = Config.from_env()
    provider = entrypoint.build_provider(config, True)

    async def collect():
        events = []
        async for event in provider.stream([Message("user", "hi")], []):
            events.append(event)
        return events

    faults = asyncio.run(collect())
    assert len(faults) == 1 and isinstance(faults[0], StreamError) and faults[0].retryable is True
    faults = asyncio.run(collect())
    assert len(faults) == 1 and isinstance(faults[0], StreamError)
    final = asyncio.run(collect())
    assert any(isinstance(event, TextDelta) for event in final)
