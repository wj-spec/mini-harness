from __future__ import annotations

from app import __main__ as entrypoint
from app.config import Config


def test_build_provider_uses_llm_timeout(monkeypatch):
    monkeypatch.setenv("VIBE_API_KEY", "sk-test")
    config = Config.from_env()
    config.llm_timeout = 7.0
    provider = entrypoint.build_provider(config, False)
    assert provider.timeout == 7.0
