"""旧技能正文的模型视图；原始会话记录保持可回看。"""
from __future__ import annotations

from dataclasses import replace

from core.models import Message


def skill_history_view(messages: list[Message]) -> list[Message]:
    result = []
    for message in messages:
        if (message.role == "tool" and message.name == "load_skill"
                and message.content.startswith("# skill: ")):
            name = message.content.splitlines()[0][len("# skill: "):].strip()
            message = replace(message, content=(
                f"Historical skill load: {name}. Body omitted from model history. "
                "Current activation is determined only by [ACTIVE SKILLS]. "
                "Call load_skill to activate the current version if needed."
            ))
        result.append(message)
    return result
