from __future__ import annotations

from typing import AsyncIterator, Protocol

from core.models import Message

from app.events import StreamDone, StreamError, TextDelta, ToolCallsReady

Event = TextDelta | ToolCallsReady | StreamDone | StreamError


class Provider(Protocol):
    async def stream(self, messages: list[Message], tools: list[dict]) -> AsyncIterator[Event]:
        ...
