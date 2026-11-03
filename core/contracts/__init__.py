"""纯数据契约：消息、工具调用、执行结果。只依赖标准库。"""

from .messages import (
    ExecutionOutcome,
    Message,
    Response,
    Role,
    ToolCall,
    message_from_dict,
    parse_execution_outcome,
)

__all__ = [
    "ExecutionOutcome",
    "Message",
    "Response",
    "Role",
    "ToolCall",
    "message_from_dict",
    "parse_execution_outcome",
]
