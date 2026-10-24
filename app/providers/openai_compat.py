from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse

import httpx

from core.models import Message, ToolCall

from app.events import ReasoningDelta, StreamDone, StreamError, TextDelta, ToolCallsReady


def chat_completions_url(base_url: str) -> str:
    """OpenAI 兼容端点的完整 URL；已带 /chat/completions 的地址原样使用。"""
    url = base_url.rstrip("/")
    return url if url.endswith("/chat/completions") else url + "/chat/completions"


def _to_openai(message: Message, *, include_reasoning: bool = False) -> dict:
    data: dict = {"role": message.role, "content": message.content or ""}
    if message.role == "assistant":
        if message.reasoning_content is not None:
            data["reasoning_content"] = message.reasoning_content
        elif include_reasoning:
            # 压缩摘要等本地生成的 assistant 没有模型推理。
            data["reasoning_content"] = ""
    if message.tool_calls:
        data["tool_calls"] = [
            {
                "id": call.id or f"call_{i}",
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.args, ensure_ascii=False),
                },
            }
            for i, call in enumerate(message.tool_calls)
        ]
    if message.role == "tool":
        data["tool_call_id"] = message.tool_call_id or ""
        if message.name:
            data["name"] = message.name
    return data


class OpenAICompatProvider:
    """OpenAI-compatible /chat/completions，流式。

    只做一个 provider；DeepSeek、OpenRouter、Ollama 的 /v1 都走这里。
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://api.deepseek.com/v1",
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
        trust_env: bool = False,
        debug_path: str | Path | None = None,
        stream_usage: bool = True,
    ):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self.trust_env = trust_env
        self.debug_path = Path(debug_path) if debug_path else None
        self.stream_usage = stream_usage
        self._call_seq = 0
        self._reasoning_required = (
            model.lower().startswith("deepseek-")
            or urlparse(self.base_url).hostname == "api.deepseek.com"
        )

    def _debug(self, event: dict) -> None:
        if self.debug_path is None:
            return
        self.debug_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.debug_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    async def stream(self, messages: list[Message], tools: list[dict]) -> AsyncIterator[object]:
        self._reasoning_required = self._reasoning_required or any(
            m.role == "assistant" and m.reasoning_content is not None for m in messages
        )
        payload: dict = {
            "model": self.model,
            "messages": [_to_openai(m, include_reasoning=self._reasoning_required) for m in messages],
            "stream": True,
        }
        if self.stream_usage:
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = tools

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        self._debug({"type": "request", "model": self.model, "tools": len(tools), "messages": len(messages)})
        calls: dict[int, dict] = {}
        finish_reason: str | None = None
        done_seen = False
        usage: dict | None = None
        reasoning_parts: list[str] = []
        reasoning_seen = False
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                transport=self.transport,
                trust_env=self.trust_env,
            ) as client:
                async with client.stream(
                    "POST", chat_completions_url(self.base_url), headers=headers, json=payload,
                ) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", "replace")
                        retryable = response.status_code in {408, 409, 429, 500, 502, 503, 504}
                        yield StreamError(
                            f"HTTP {response.status_code}: {body[:500]}",
                            retryable=retryable,
                        )
                        return

                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            done_seen = True
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        finish_reason = choices[0].get("finish_reason") or finish_reason
                        delta = choices[0].get("delta") or {}

                        if isinstance(delta.get("reasoning_content"), str):
                            reasoning_seen = True
                            self._reasoning_required = True
                            reasoning_parts.append(delta["reasoning_content"])
                            yield ReasoningDelta(delta["reasoning_content"])

                        if delta.get("content"):
                            self._debug({"type": "chunk", "content": delta["content"][:80]})
                            yield TextDelta(delta["content"])

                        for tc in delta.get("tool_calls") or []:
                            index = tc.get("index", 0)
                            slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] += fn["name"]
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
        except httpx.HTTPError as exc:
            yield StreamError(f"{type(exc).__name__}: {exc}", retryable=True)
            return

        # 流终止判定：[DONE] 或明确的 finish_reason 才算完整收尾。
        stream_complete = done_seen or finish_reason is not None
        complete: list[ToolCall] = []
        incomplete: list[ToolCall] = []
        for index in sorted(calls):
            slot = calls[index]
            raw = slot["arguments"]
            name = slot["name"].strip()
            args: dict | None = None
            if raw:
                try:
                    parsed_args = json.loads(raw)
                except json.JSONDecodeError:
                    parsed_args = None
                if isinstance(parsed_args, dict):
                    args = parsed_args
            elif finish_reason == "tool_calls":
                args = {}
            call_id = slot["id"] or f"call_{self._call_seq}_{index}"
            # 不完整接收的工具调用绝不进入可执行列表。
            if name and args is not None and stream_complete:
                complete.append(ToolCall(name=name, args=args, id=call_id))
            else:
                incomplete.append(ToolCall(name=name or "unknown", args={"_raw": raw}, id=call_id))
        if complete or incomplete:
            yield ToolCallsReady(complete, incomplete=incomplete)

        self._debug({"type": "calls", "count": len(complete), "incomplete": len(incomplete),
                     "finish_reason": finish_reason, "stream_complete": stream_complete})
        self._call_seq += 1
        yield StreamDone(
            finish_reason=finish_reason if stream_complete else "truncated", usage=usage,
            reasoning_content="".join(reasoning_parts) if reasoning_seen else None,
        )
