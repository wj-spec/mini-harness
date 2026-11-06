"""任务模式（10 P0）：mode 是 TaskRecord 的属性，不是启动参数。"""
from __future__ import annotations

MODE_NAMES = ("chat", "act", "plan", "goal")
DEFAULT_MODE = "act"
GOAL_MODE = "goal"
RUN_MODES = frozenset({"chat", "act"})


class ModeError(ValueError):
    pass


def parse_mode(value: object, *, default: str = DEFAULT_MODE) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or value not in MODE_NAMES:
        raise ModeError(f"invalid mode: {value!r}")
    return value


def kind_for_mode(mode: str) -> str:
    return "goal" if mode == "goal" else "run"


def mode_for_kind(kind: str, value: object = None) -> str:
    if kind == "goal":
        return parse_mode(value, default=GOAL_MODE)
    if kind == "run":
        return parse_mode(value, default=DEFAULT_MODE)
    raise ModeError(f"mode is only supported for run/goal tasks, got {kind}")
