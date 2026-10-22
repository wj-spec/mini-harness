from __future__ import annotations

from collections.abc import AsyncIterator, Iterable

from core.models import Response

from app.events import StreamDone, TextDelta, ToolCallsReady


class FakeProvider:
    """脚本化 provider：测试和 demo 用，不联网。"""

    def __init__(self, script: Iterable[str | Response]):
        self.script = list(script)
        self.i = 0
        self.seen: list[list] = []
        self.seen_tools: list[list] = []

    async def stream(self, messages, tools) -> AsyncIterator[object]:
        self.seen.append(list(messages))
        self.seen_tools.append(list(tools))
        if self.i >= len(self.script):
            yield TextDelta("[fake provider exhausted]")
            yield StreamDone()
            return

        item = self.script[self.i]
        self.i += 1

        if isinstance(item, str):
            for i in range(0, len(item), 8):
                yield TextDelta(item[i : i + 8])
        else:
            if item.content:
                yield TextDelta(item.content)
            if item.tool_calls:
                yield ToolCallsReady(item.tool_calls)

        yield StreamDone(
            usage=None if isinstance(item, str) else item.usage,
            finish_reason=None if isinstance(item, str) else item.finish_reason,
            reasoning_content=None if isinstance(item, str) else item.reasoning_content,
        )
