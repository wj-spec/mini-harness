from __future__ import annotations

from dataclasses import dataclass

from core.models import ToolCall


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCallsReady:
    calls: list[ToolCall]


@dataclass
class StreamDone:
    finish_reason: str | None = None
    usage: dict | None = None


@dataclass
class StreamError:
    message: str
    retryable: bool = False
