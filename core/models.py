from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    """模型想调用一个工具：名字 + 参数 + 协议 id。"""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    id: str | None = None


@dataclass
class Message:
    """统一的消息形状，替代裸字符串。"""

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict | None = None
    finish_reason: str | None = None
    name: str | None = None  # role == "tool" 时，记录工具名
    tool_call_id: str | None = None  # role == "tool" 时，对应哪次调用


@dataclass
class Response:
    """模型一次调用的返回：要么是最终文本，要么是工具调用。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict | None = None
    finish_reason: str | None = None
