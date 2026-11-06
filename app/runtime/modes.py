"""任务模式（10 P0）：mode 是 TaskRecord 的属性，不是启动参数。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MODE_NAMES = ("chat", "act", "plan", "goal")
DEFAULT_MODE = "act"
GOAL_MODE = "goal"
RUN_MODES = frozenset({"chat", "act"})


@dataclass(frozen=True)
class ModeProfile:
    mode: str
    tools: tuple[str, ...] | str
    system: str
    isolation: str
    approval: str
    completion: dict[str, bool] = field(default_factory=dict)
    capability: tuple[str, ...] = ()
    delegation: dict[str, Any] = field(default_factory=dict)

    @property
    def tool_names(self) -> tuple[str, ...] | str:
        return self.tools


READ_ONLY_TOOLS = (
    "read_file", "list_files", "grep", "glob", "git_status", "git_diff", "git_log",
)

MODE_PROFILES: dict[str, ModeProfile] = {
    "chat": ModeProfile(
        mode="chat", tools=(), system="chat_only", isolation="shared_readonly",
        approval="ask", completion={"verified": False},
        capability=("task.list",),
    ),
    "act": ModeProfile(
        mode="act", tools="*", system="default", isolation="shared_readonly",
        approval="ask", completion={"done_verified": True},
        capability=("task.*", "approval.decide"),
    ),
    "goal": ModeProfile(
        mode="goal", tools="*", system="default", isolation="worktree_write",
        approval="ask", completion={"done_verified": True, "criteria_required": False},
        capability=("task.*", "approval.decide"),
    ),
    "plan": ModeProfile(
        mode="plan", tools=READ_ONLY_TOOLS, system="plan_only", isolation="shared_readonly",
        approval="ask", completion={"criteria_required": True},
        capability=("task.*", "plan.review"),
        delegation={"model_initiated": False},
    ),
}


def resolve_profile(mode: str) -> ModeProfile:
    try:
        return MODE_PROFILES[mode]
    except KeyError as exc:
        raise ModeError(f"unknown mode profile: {mode!r}") from exc


def validate_profile_tools(profile: ModeProfile, registry_names: tuple[str, ...] | list[str]) -> None:
    if profile.tools == "*":
        return
    available = set(registry_names)
    missing = sorted(set(profile.tools) - available)
    if missing:
        raise ModeError(f"mode {profile.mode} profile references missing tools: {missing}")


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
