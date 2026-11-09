"""会话级 plan mode 状态（P1）：active 已生效，wanted 为运行中待生效选择。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PLAN_MODE_FILE = "plan-mode.json"
PLAN_MODE_SCHEMA_VERSION = 1


class PlanModeError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlanModeState:
    active: bool = False
    wanted: bool | None = None
    updated_at: str = ""
    schema_version: int = PLAN_MODE_SCHEMA_VERSION

    @property
    def pending(self) -> bool:
        return self.wanted is not None and self.wanted != self.active

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "PlanModeState":
        if not isinstance(value, dict):
            raise PlanModeError("plan mode state must be an object")
        if value.get("schema_version", PLAN_MODE_SCHEMA_VERSION) != PLAN_MODE_SCHEMA_VERSION:
            raise PlanModeError("unsupported plan mode schema_version")
        active = value.get("active", False)
        wanted = value.get("wanted")
        if not isinstance(active, bool):
            raise PlanModeError("active must be boolean")
        if wanted is not None and not isinstance(wanted, bool):
            raise PlanModeError("wanted must be boolean or null")
        return cls(
            active=active,
            wanted=wanted,
            updated_at=str(value.get("updated_at", "")),
            schema_version=PLAN_MODE_SCHEMA_VERSION,
        )


class PlanModeStore:
    """单文件会话状态；损坏时按 inactive 处理，不阻塞会话启动。"""

    def __init__(self, session_root: str | Path):
        self.session_root = Path(session_root)
        self.path = self.session_root / PLAN_MODE_FILE

    def load(self) -> PlanModeState:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return PlanModeState()
        except OSError:
            return PlanModeState()
        try:
            return PlanModeState.from_dict(json.loads(raw))
        except (json.JSONDecodeError, PlanModeError):
            return PlanModeState()

    def save(self, state: PlanModeState) -> Path:
        state.schema_version = PLAN_MODE_SCHEMA_VERSION
        state.updated_at = _now()
        self.session_root.mkdir(parents=True, exist_ok=True)
        target = self.path
        temporary = target.with_name(target.name + ".tmp")
        try:
            temporary.write_text(
                json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return target

    def status(self) -> dict[str, Any]:
        state = self.load()
        return {"active": state.active, "pending": state.pending, "wanted": state.wanted}

    def set(self, active: bool, *, running: bool = False) -> str:
        """选择 plan mode；返回 committed/queued/cancelled/noop。"""
        state = self.load()
        if running:
            selected = state.wanted if state.wanted is not None else state.active
            if selected == active:
                if state.wanted is not None and state.wanted == state.active:
                    state.wanted = None
                    self.save(state)
                    return "cancelled"
                return "noop"
            if state.active == active:
                state.wanted = None
                self.save(state)
                return "cancelled"
            state.wanted = active
            self.save(state)
            return "queued"
        if state.active == active:
            if state.wanted is not None:
                state.wanted = None
                self.save(state)
                return "cancelled"
            return "noop"
        state.active = active
        state.wanted = None
        self.save(state)
        return "committed"

    def commit_pending(self) -> PlanModeState:
        state = self.load()
        if state.wanted is not None:
            state.active = state.wanted
            state.wanted = None
            self.save(state)
            state.updated_at = _now()
        return state

    def effective(self) -> bool:
        return self.load().active
