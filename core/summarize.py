from __future__ import annotations

from .models import Message


def summarize_messages(messages: list[Message], max_chars: int = 4000) -> str:
    """确定性摘要：保留角色、工具名、内容前 300 字；不调用模型。"""
    lines: list[str] = []
    for m in messages:
        if m.name in {"history_summary_l1", "history_summary_l2"}:
            lines.extend(m.content.splitlines())
            continue
        content = " ".join(m.content.split())
        if m.role == "tool":
            lines.append(f"- tool {m.name or '?'}: {content[:300]}")
        elif m.role == "assistant":
            if content:
                lines.append(f"- assistant: {content[:300]}")
            for call in m.tool_calls:
                lines.append(f"- call {call.name}({call.args})")
        elif m.role == "user":
            lines.append(f"- user: {content[:300]}")

    text = "\n".join(lines) if lines else "(no summarizable messages)"
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-(max_chars // 2) :]
    return head + "\n...[summary truncated]...\n" + tail
