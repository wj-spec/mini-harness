from __future__ import annotations

from dataclasses import dataclass

from core.models import ToolCall


@dataclass
class TextDelta:
    text: str


@dataclass
class ReasoningDelta:
    text: str


@dataclass
class ToolCallsReady:
    calls: list[ToolCall]


@dataclass
class StreamDone:
    finish_reason: str | None = None
    usage: dict | None = None
    reasoning_content: str | None = None


@dataclass
class StreamError:
    message: str
    retryable: bool = False
