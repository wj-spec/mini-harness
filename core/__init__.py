"""可复用的 Agent harness core。这里不 import app。

这个文件的公共 API 保持窄：只 re-export 跨域最常用的类型。
新代码优先从子包导入（`core.contracts` / `core.budget` / `core.context` / `core.task`），
模块落位规则见 core/README.md。
"""

from .context import ContextBuilder, ContextResult
from .contracts import Message, Response, ToolCall

__all__ = [
    "ContextBuilder",
    "ContextResult",
    "Message",
    "Response",
    "ToolCall",
]
