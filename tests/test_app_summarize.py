import json

import httpx
import pytest

from app.summarize import LLMSummarizer
from core.models import Message
from core.summarize import summarize_messages


@pytest.mark.parametrize(
    "base_url",
    ["http://example/v1", "http://example/v1/", "http://example/v1/chat/completions"],
)
def test_llm_summarizer_uses_configured_model_and_provider(base_url):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "- goal: fix\n- open: test"}}]}
        )

    summarizer = LLMSummarizer(
        "summary-model", "key", base_url,
        transport=httpx.MockTransport(handler),
    )
    output = summarizer(
        [Message("user", "fix bug"), Message("tool", "ERROR bad", name="bash")]
    )

    assert output == "- goal: fix\n- open: test"
    assert captured["url"] == "http://example/v1/chat/completions"
    assert captured["authorization"] == "Bearer key"
    assert captured["body"]["model"] == "summary-model"
    assert captured["body"]["stream"] is False
    assert "bash" in captured["body"]["messages"][1]["content"]
    assert summarizer.last_error is None


def test_llm_summarizer_falls_back_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    summarizer = LLMSummarizer(
        "summary-model", "key", "http://example/v1",
        transport=httpx.MockTransport(handler),
    )
    messages = [Message("user", "fix bug"), Message("tool", "ERROR bad", name="bash")]

    output = summarizer(messages)

    assert output == summarize_messages(messages)
    assert summarizer.last_error == "ConnectError: down"


def test_llm_summarizer_empty_response_falls_back():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": " "}}]})

    summarizer = LLMSummarizer(
        "summary-model", "key", "http://example/v1",
        transport=httpx.MockTransport(handler),
    )
    messages = [Message("user", "fix bug")]

    assert summarizer(messages) == summarize_messages(messages)
