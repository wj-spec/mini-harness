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
    reasoning_content: str | None = None  # 模型协议元数据，不作为正文


def message_from_dict(d: dict) -> Message:
    """从持久化 JSON 恢复 Message；session 与上下文 offload 共用同一校验。"""
    if not isinstance(d, dict) or d.get("role") not in ("system", "user", "assistant", "tool"):
        raise ValueError("invalid message role")
    if not isinstance(d.get("content", ""), str):
        raise ValueError("invalid message content")
    for field in ("name", "tool_call_id", "finish_reason", "reasoning_content"):
        if d.get(field) is not None and not isinstance(d[field], str):
            raise ValueError(f"invalid message {field}")
    if d.get("usage") is not None and not isinstance(d["usage"], dict):
        raise ValueError("invalid message usage")
    execution = parse_execution_outcome(d.get("execution"))
    if execution is not None and d["role"] != "tool":
        raise ValueError("only tool messages can carry execution outcomes")
    raw_calls = d.get("tool_calls", [])
    if not isinstance(raw_calls, list):
        raise ValueError("invalid tool calls")
    for call in raw_calls:
        if (
            not isinstance(call, dict)
            or not isinstance(call.get("name"), str)
            or not call["name"]
            or not isinstance(call.get("args", {}), dict)
            or (call.get("id") is not None and not isinstance(call["id"], str))
        ):
            raise ValueError("invalid tool call")
    calls = [ToolCall(id=c.get("id"), name=c["name"], args=c.get("args", {})) for c in raw_calls]
    return Message(
        role=d["role"],
        content=d.get("content", ""),
        tool_calls=calls,
        usage=d.get("usage"),
        finish_reason=d.get("finish_reason"),
        name=d.get("name"),
        tool_call_id=d.get("tool_call_id"),
        execution=execution,
        reasoning_content=d.get("reasoning_content"),
    )


@dataclass
class Response:
    """模型一次调用的返回：要么是最终文本，要么是工具调用。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict | None = None
    finish_reason: str | None = None
    reasoning_content: str | None = None
