from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict, cast

Role = Literal["system", "user", "assistant", "tool"]


class ExecutionOutcome(TypedDict):
    status: Literal["succeeded", "failed", "timed_out", "cancelled", "unknown"]
    exit_code: int | None


def parse_execution_outcome(value: Any) -> ExecutionOutcome | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"status", "exit_code"}:
        raise ValueError("invalid execution outcome")
    status, code = value["status"], value["exit_code"]
    if not isinstance(status, str) or status not in {"succeeded", "failed", "timed_out", "cancelled", "unknown"}:
        raise ValueError("invalid execution status")
    if code is not None and (type(code) is not int or abs(code) > 2**53 - 1):
        raise ValueError("invalid execution exit code")
    if (status == "succeeded" and code != 0) or (status == "failed" and (code is None or code == 0)):
        raise ValueError("execution status contradicts exit code")
    if status == "unknown" and code is not None:
        raise ValueError("unknown execution cannot have an exit code")
    return cast(ExecutionOutcome, dict(value))


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
    execution: ExecutionOutcome | None = None  # 运行事实，不发送为模型协议字段


@dataclass
class Response:
    """模型一次调用的返回：要么是最终文本，要么是工具调用。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict | None = None
    finish_reason: str | None = None
