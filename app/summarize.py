from __future__ import annotations

import httpx

from app.providers.openai_compat import chat_completions_url
from core.models import Message
from core.summarize import summarize_messages


SUMMARY_SYSTEM_PROMPT = (
    "You compact an agent conversation so the agent can continue. "
    "Preserve: the original goal, decisions, file paths, errors, test results, "
    "and open questions. Do not invent facts. Output <= 300 words, bullet points."
)


def _transcript(messages: list[Message], max_chars: int = 60_000) -> str:
    parts: list[str] = []
    for m in messages:
        text = " ".join(m.content.split())
        if m.role == "tool":
            head = f"tool {m.name or '?'}"
        elif m.role == "assistant":
            head = "assistant"
        elif m.role == "user":
            head = "user"
        else:
            continue
        calls = "".join(f" [call {c.name}({c.args})]" for c in m.tool_calls)
        parts.append(f"{head}: {text[:500]}{calls}")
    text = "\n".join(parts)
    if len(text) <= max_chars:
        return text
    return text[: max_chars // 2] + "\n...[truncated]...\n" + text[-(max_chars // 2) :]


class LLMSummarizer:
    """HTTP 摘要适配器；失败时退回确定性摘要。"""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        max_chars: int = 4000,
        timeout: float = 60.0,
        trust_env: bool = False,
        transport: httpx.BaseTransport | None = None,
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_chars = max_chars
        self.timeout = timeout
        self.trust_env = trust_env
        self.transport = transport
        self.last_error: str | None = None

    def __call__(self, messages: list[Message]) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": _transcript(messages)},
            ],
            "temperature": 0,
            "max_tokens": 800,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                transport=self.transport,
                trust_env=self.trust_env,
            ) as client:
                response = client.post(
                    chat_completions_url(self.base_url), headers=headers, json=payload,
                )
                response.raise_for_status()
                data = response.json()
                text = data["choices"][0]["message"]["content"]
                self.last_error = None
                return text.strip()[: self.max_chars] or summarize_messages(
                    messages, self.max_chars
                )
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return summarize_messages(messages, self.max_chars)
