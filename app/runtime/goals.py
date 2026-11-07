"""跨段目标状态 GoalRecord（spec: docs/specs/goal-record.md）。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime.tasks import TaskRecord

GOAL_SCHEMA_VERSION = 2
GOAL_STATES = ("created", "running", "paused", "completed", "failed", "cancelled")
GOAL_FILE = "goal-record.json"
LEGACY_GOAL_FILE = "goal.json"

BUDGET_DEFAULTS = {"max_goal_tokens": 0, "max_goal_cost_usd": 0, "max_goal_seconds": 0}


class GoalError(Exception):
    pass


class GoalCorrupt(GoalError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _require_text(value: Any, name: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    result = value.strip()
    if max_length is not None and len(result) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return result


def _goal_id_for_task(task: TaskRecord) -> str:
    if task.goal_id:
        return task.goal_id
    suffix = task.task_id[2:] if task.task_id.startswith("t-") else task.task_id
    return f"g-{suffix}"


def _state_for_task(task: TaskRecord) -> str:
    if task.status in {"created", "queued"}:
        return "created"
    if task.status == "running":
        return "running"
    if task.status in {"paused", "interrupted"}:
        return "paused"
    if task.status in {"completed", "failed", "cancelled"}:
        return task.status
    return "paused"


def _segments_for_task(task: TaskRecord) -> list[dict[str, Any]]:
    return [
        {
            "segment": segment.segment,
            "run_id": segment.run_id,
            "status": segment.status,
            "stop_reason": segment.stop_reason,
            "usage": {},
            "verified": None,
            "round_ref": segment.round_ref,
        }
        for segment in task.segments
    ]


def _validate_items(value: Any, name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError(f"{name} items must be objects")
        result.append(dict(item))
    return result


def _validate_budget(value: Any) -> dict[str, int | float]:
    if value is None:
        return dict(BUDGET_DEFAULTS)
    if not isinstance(value, dict):
        raise ValueError("budget must be an object")
    result: dict[str, int | float] = dict(BUDGET_DEFAULTS)
    for key in BUDGET_DEFAULTS:
        if key in value:
            raw = value[key]
            if not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw < 0:
                raise ValueError(f"budget.{key} must be a non-negative number")
            result[key] = raw
    return result


@dataclass
class GoalRecord:
    schema_version: int
    goal_id: str
    task_id: str
    objective: str
    state: str
    acceptance_criteria: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    segments: list[dict[str, Any]] = field(default_factory=list)
    open_items: list[dict[str, Any]] = field(default_factory=list)
    blocking: list[dict[str, Any]] = field(default_factory=list)
    verification_plan: list[dict[str, Any]] = field(default_factory=list)
    budget: dict[str, int | float] = field(default_factory=lambda: dict(BUDGET_DEFAULTS))
    plan_id: str | None = None
    source: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def acceptance(self):
        """从已确认 required criteria + verification_plan 生成三态验收对象。"""
        from app.runtime.acceptance import AcceptanceError, PlanAcceptance

        confirmed = [
            item for item in self.acceptance_criteria
            if item.get("confirmed") is True and item.get("required", True) is not False
        ]
        if not confirmed:
            return None
        criteria = []
        for index, item in enumerate(self.acceptance_criteria, 1):
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                continue
            criteria.append({
                "id": str(item.get("id") or f"ac{index}"),
                "text": str(item.get("text")),
                "required": item.get("required", True) is not False,
                "confirmed": item.get("confirmed") is True,
                "manual": bool(item.get("manual", False)),
                "source": str(item.get("source", "user")),
            })
        confirmed_ids = [item["id"] for item in criteria if item["confirmed"] and item["required"]]
        commands = []
        for index, item in enumerate(self.verification_plan, 1):
            if not isinstance(item, dict):
                continue
            command = str(item.get("command") or "").strip()
            if not command:
                continue
            covers = [str(entry) for entry in item.get("covers") or [] if str(entry).strip()]
            commands.append({
                "id": str(item.get("id") or f"v{index}"),
                "command": command,
                "covers": covers or confirmed_ids,
                "source": str(item.get("source", "project_profile")),
            })
        try:
            return PlanAcceptance.from_dict({
                "schema_version": 1,
                "task_id": self.task_id,
                "plan_id": f"goal-{self.goal_id}",
                "plan_version": 1,
                "acceptance_criteria": criteria,
                "verification_plan": commands,
                "trusted_source": "project_profile",
            })
        except AcceptanceError:
            return None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "objective": self.objective,
            "state": self.state,
            "acceptance_criteria": [dict(item) for item in self.acceptance_criteria],
            "constraints": [dict(item) for item in self.constraints],
            "segments": [dict(item) for item in self.segments],
            "open_items": [dict(item) for item in self.open_items],
            "blocking": [dict(item) for item in self.blocking],
            "verification_plan": [dict(item) for item in self.verification_plan],
            "budget": dict(self.budget),
            "plan_id": self.plan_id,
            "source": dict(self.source),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        for key, value in self.extra.items():
            if key not in payload:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, value: Any) -> "GoalRecord":
        if not isinstance(value, dict):
            raise ValueError("goal record must be an object")
        if value.get("schema_version") != GOAL_SCHEMA_VERSION:
            raise ValueError("unsupported goal schema_version")
        goal_id = _require_text(value.get("goal_id"), "goal_id", max_length=128)
        task_id = _require_text(value.get("task_id"), "task_id", max_length=128)
        objective = _require_text(value.get("objective"), "objective", max_length=4000)
        state = value.get("state")
        if state not in GOAL_STATES:
            raise ValueError("invalid goal state")
        criteria = _validate_items(value.get("acceptance_criteria"), "acceptance_criteria")
        for item in criteria:
            if not isinstance(item.get("confirmed"), bool):
                item["confirmed"] = False
        source = value.get("source")
        if source is None:
            source = {}
        if not isinstance(source, dict):
            raise ValueError("source must be an object")
        plan_id = value.get("plan_id")
        if plan_id is not None:
            plan_id = _require_text(plan_id, "plan_id", max_length=128)
        known = {
            "schema_version", "goal_id", "task_id", "objective", "state",
            "acceptance_criteria", "constraints", "segments", "open_items", "blocking",
            "verification_plan", "budget", "plan_id", "source", "created_at", "updated_at",
        }
        return cls(
            schema_version=GOAL_SCHEMA_VERSION, goal_id=goal_id, task_id=task_id,
            objective=objective, state=state, acceptance_criteria=criteria,
            constraints=_validate_items(value.get("constraints"), "constraints"),
            segments=_validate_items(value.get("segments"), "segments"),
            open_items=_validate_items(value.get("open_items"), "open_items"),
            blocking=_validate_items(value.get("blocking"), "blocking"),
            verification_plan=_validate_items(value.get("verification_plan"), "verification_plan"),
            budget=_validate_budget(value.get("budget")), plan_id=plan_id,
            source=source, created_at=_require_text(value.get("created_at"), "created_at", max_length=64),
            updated_at=_require_text(value.get("updated_at"), "updated_at", max_length=64),
            extra={key: item for key, item in value.items() if key not in known},
        )


class GoalStore:
    def __init__(self, session_root: str | Path):
        self.root = Path(session_root)
        self.path = self.root / GOAL_FILE
        self.legacy_path = self.root / LEGACY_GOAL_FILE

    def load(self, goal_id: str | None = None) -> GoalRecord | None:
        if self.path.is_symlink():
            raise GoalCorrupt("goal-record.json is a symlink")
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise GoalCorrupt(f"goal-record.json unreadable: {exc}") from exc
        try:
            record = GoalRecord.from_dict(json.loads(raw))
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise GoalCorrupt(f"invalid goal-record.json: {exc}") from exc
        if goal_id is not None and record.goal_id != goal_id:
            return None
        return record

    def save(self, record: GoalRecord) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(
                json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def ensure_for_task(self, task: TaskRecord) -> GoalRecord:
        if task.kind != "goal":
            raise GoalError("GoalRecord is only for kind=goal tasks")
        existing = self.load()
        if existing is not None and existing.task_id == task.task_id:
            return self.sync_from_task(task)
        if existing is not None and existing.state not in {"completed", "failed", "cancelled"}:
            if existing.task_id != task.task_id:
                raise GoalError("session already has an active goal record for another task")
        legacy = self._legacy_record(task)
        if legacy is not None:
            self.save(legacy)
            return legacy
        now = _now()
        record = GoalRecord(
            schema_version=GOAL_SCHEMA_VERSION, goal_id=_goal_id_for_task(task),
            task_id=task.task_id, objective=task.objective, state=_state_for_task(task),
            segments=_segments_for_task(task),
            source={"session_id": task.session_id, "created_by": "harness"},
            created_at=now, updated_at=now,
        )
        self.save(record)
        return record

    def sync_from_task(self, task: TaskRecord) -> GoalRecord:
        record = self.load()
        if record is None or record.task_id != task.task_id:
            return self.ensure_for_task(task)
        updated = replace(
            record,
            objective=task.objective,
            state=_state_for_task(task),
            segments=_segments_for_task(task),
            updated_at=_now(),
        )
        self.save(updated)
        return updated

    def _legacy_record(self, task: TaskRecord) -> GoalRecord | None:
        if self.path.exists() or not self.legacy_path.is_file():
            return None
        try:
            payload = json.loads(self.legacy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        objective = payload.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            return None
        if payload.get("completed") is True:
            state = "completed"
        elif payload.get("active") is True:
            state = "running"
        else:
            state = "paused"
        now = _now()
        return GoalRecord(
            schema_version=GOAL_SCHEMA_VERSION, goal_id=_goal_id_for_task(task),
            task_id=task.task_id, objective=objective.strip(), state=state,
            segments=[],
            source={"session_id": task.session_id, "created_by": "tui_legacy"},
            created_at=now, updated_at=now,
        )

    def migrate_legacy(self) -> GoalRecord | None:
        record = self.load()
        if record is not None:
            return record
        if not self.legacy_path.is_file():
            return None
        try:
            payload = json.loads(self.legacy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        objective = payload.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            return None
        if payload.get("completed") is True:
            state = "completed"
        elif payload.get("active") is True:
            state = "running"
        else:
            state = "paused"
        now = _now()
        record = GoalRecord(
            schema_version=GOAL_SCHEMA_VERSION, goal_id="g-legacy",
            task_id="", objective=objective.strip(), state=state,
            source={"created_by": "tui_legacy"},
            created_at=now, updated_at=now,
        )
        self.save(record)
        return record


RESOURCE_STOPS = frozenset({"max_run_seconds", "token_budget", "cost_budget", "max_steps"})


def goal_prompt(record: GoalRecord, *, segment: int, stop_reason: str | None = None) -> str:
    """由 GoalRecord 生成段提示；P1 先供 worker 续段使用。"""
    criteria = [
        item for item in record.acceptance_criteria
        if item.get("confirmed") is True and isinstance(item.get("text"), str)
    ]
    lines = []
    if segment <= 1:
        lines.append("[GOAL MODE]")
        lines.append(f"目标：{record.objective}")
        lines.append("这是一个可能跨多个运行段的长任务。先核对工作区，再分步执行；不要停在计划或空泛总结上。")
    else:
        lines.append(f"[GOAL MODE · 第 {segment} 段]")
        lines.append(f"继续目标：{record.objective}")
        lines.append(f"上一段因 {stop_reason or '资源暂停'} 结束。先核对实际工作区与已保存进度，再继续未完成部分；不要重复已完成的工作。")
    if criteria:
        lines.append("已确认验收标准：")
        lines.extend(f"- {item.get('text')}" for item in criteria)
    open_items = [
        item for item in record.open_items
        if item.get("status") in {"open", "blocked"} and isinstance(item.get("text"), str)
    ]
    if open_items:
        lines.append("未完成/阻塞：")
        lines.extend(f"- [{item.get('status')}] {item.get('text')}" for item in open_items)
    if record.constraints:
        texts = [item.get("text") for item in record.constraints if isinstance(item.get("text"), str)]
        if texts:
            lines.append("约束：")
            lines.extend(f"- {text}" for text in texts)
    lines.append("目标真正达成后给出最终答复；暂时无法推进时说明具体阻塞。")
    return "\n".join(lines)


def continue_decision(
    record: GoalRecord,
    task: TaskRecord,
    result_status: str,
    stop_reason: str,
    *,
    max_segments: int = 8,
    max_seconds: float = 4 * 60 * 60,
    max_tokens: int = 0,
    max_cost_usd: float = 0,
    used_tokens: int = 0,
    used_cost_usd: float = 0.0,
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    """返回 (是否继续下一段, 停止原因)。P1 先复刻 TUI goalDecision 的资源停止规则。"""
    if result_status != "stopped" or stop_reason not in RESOURCE_STOPS:
        return False, stop_reason or result_status or "stopped"
    segments = max(len(record.segments), len(task.segments))
    if segments >= max_segments:
        return False, "goal_max_segments"
    current = now or datetime.now(timezone.utc)
    try:
        started = datetime.fromisoformat(record.created_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if (current - started).total_seconds() >= max_seconds:
            return False, "goal_max_seconds"
    except (ValueError, TypeError):
        pass
    if max_tokens > 0:
        used = used_tokens
        if used <= 0:
            for segment in record.segments:
                usage = segment.get("usage") if isinstance(segment, dict) else None
                if isinstance(usage, dict):
                    value = usage.get("total_tokens")
                    if isinstance(value, int):
                        used += value
        if used >= max_tokens:
            return False, "goal_max_tokens"
    if max_cost_usd > 0 and used_cost_usd >= max_cost_usd:
        return False, "goal_max_cost_usd"
    return True, None
