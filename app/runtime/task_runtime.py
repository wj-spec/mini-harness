"""stdio/TUI 与 TaskStore 的薄接线（spec: docs/specs/task-runtime-integration.md）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.runtime.goals import GoalError, GoalStore
from app.runtime.tasks import (
    InvalidTaskTransition,
    TaskBusy,
    TaskCorrupt,
    TaskError,
    TaskNotFound,
    TaskRecord,
    TaskStore,
)

_GOAL_META_KEYS = frozenset({"kind", "objective", "task_id", "goal_id"})


class TaskRuntimeError(Exception):
    """任务运行时接入失败；stdio 映射为结构化 error。"""


class TaskRuntimeDisabled(TaskRuntimeError):
    pass


@dataclass(frozen=True)
class RunTask:
    task_id: str
    run_id: str
    segment: int
    objective: str
    record: TaskRecord
    goal_error: str | None = None


def task_runtime_enabled(session_purpose: str) -> bool:
    """env 开关：未设置时 test 会话关闭、用户会话开启；0 显式关闭。"""
    raw = os.getenv("VIBE_TASK_RUNTIME")
    if raw is None:
        return session_purpose != "test"
    value = raw.strip().lower()
    if value in {"0", "false", "off", "no"}:
        return False
    return True


def _validate_meta(meta: Any) -> dict[str, str]:
    if not isinstance(meta, dict):
        raise TaskRuntimeError("task must be an object")
    unknown = set(meta) - _GOAL_META_KEYS
    if unknown:
        raise TaskRuntimeError(f"unknown task fields: {sorted(unknown)}")
    kind = meta.get("kind")
    if kind != "goal":
        raise TaskRuntimeError("P0 only supports task.kind=goal")
    result: dict[str, str] = {"kind": "goal"}
    for key in ("objective", "task_id", "goal_id"):
        if key not in meta:
            continue
        value = meta[key]
        if not isinstance(value, str) or not value.strip():
            raise TaskRuntimeError(f"task.{key} must be a nonempty string")
        if key == "objective" and len(value.strip()) > 4000:
            raise TaskRuntimeError("task.objective exceeds 4000 characters")
        result[key] = value.strip()
    return result


class TaskRuntime:
    def __init__(self, store: TaskStore, *, workspace: str | Path, enabled: bool = True):
        self.store = store
        self.workspace = str(Path(workspace).expanduser().resolve())
        self.enabled = enabled
        self.goals = GoalStore(self.store.root)

    def prepare_run(self, request_id: str, meta: dict[str, Any] | None) -> RunTask | None:
        if meta is None:
            return None
        if not self.enabled:
            raise TaskRuntimeDisabled("task runtime is disabled")
        parsed = _validate_meta(meta)
        task_id = parsed.get("task_id")
        if task_id:
            record = self._attach(task_id, parsed)
        else:
            record = self._create(parsed)
        try:
            record = self.store.transition(record.task_id, "running", reason="run_start")
            segment = self.store.start_segment(record.task_id, run_id=request_id)
        except Exception:
            self.store.release_lock(record.task_id)
            raise
        record = self.store.get(record.task_id)
        goal_error = None
        if record.kind == "goal":
            try:
                self.goals.ensure_for_task(record)
            except GoalError as exc:
                goal_error = f"{type(exc).__name__}: {exc}"
        return RunTask(
            task_id=record.task_id, run_id=request_id, segment=segment.segment,
            objective=record.objective, record=record, goal_error=goal_error,
        )

    def finish_run(self, run_task: RunTask | None, result: Any) -> TaskRecord | None:
        if run_task is None:
            return None
        status = str(getattr(result, "status", "error") or "error")
        verified = getattr(result, "verified", None)
        stop_reason = str(getattr(result, "stop_reason", "") or "")
        if status == "done" and verified is not False:
            segment_status, target, reason = "done", "completed", None
        elif status == "done":
            segment_status, target, reason = "done", "paused", "unverified_completion"
        elif status == "stopped":
            segment_status, target, reason = "stopped", "paused", stop_reason or "stopped"
        elif status == "cancelled":
            segment_status, target, reason = "cancelled", "paused", "cancelled"
        else:
            segment_status, target, reason = "failed", "paused", status or "error"

        record: TaskRecord | None = None
        try:
            self.store.end_segment(
                run_task.task_id, run_id=run_task.run_id,
                status=segment_status, stop_reason=reason,
            )
            record = self.store.transition(
                run_task.task_id, target, reason=reason or segment_status,
                data={"run_status": status, "verified": verified,
                      "stop_reason": stop_reason},
            )
        finally:
            self.store.release_lock(run_task.task_id)
        if record is not None:
            try:
                self.goals.sync_from_task(record)
            except GoalError:
                pass
        return record

    def recover(self) -> list[TaskRecord]:
        if not self.enabled:
            return []
        return self.store.recover_stale()

    @staticmethod
    def payload(record: TaskRecord) -> dict[str, Any]:
        segment = 0
        for item in record.segments:
            if item.status == "running":
                segment = item.segment
                break
        else:
            if record.segments:
                segment = record.segments[-1].segment
        return {
            "task_id": record.task_id,
            "kind": record.kind,
            "status": record.status,
            "segment": segment,
            "objective": record.objective,
            "goal_id": record.goal_id,
            "updated_at": record.updated_at,
        }

    # ---- 内部 ----

    def _create(self, parsed: dict[str, str]) -> TaskRecord:
        objective = parsed.get("objective")
        if not objective:
            raise TaskRuntimeError("task.objective is required when creating a task")
        record = self.store.create(
            kind="goal", objective=objective, workspace=self.workspace, mode="goal",
            goal_id=parsed.get("goal_id"),
        )
        self.store.acquire_lock(record.task_id)
        return record

    def _attach(self, task_id: str, parsed: dict[str, str]) -> TaskRecord:
        record = self.store.get(task_id)
        if record.kind != "goal":
            raise TaskRuntimeError("attached task must be kind=goal")
        if record.session_id != self.store.root.name:
            raise TaskRuntimeError("task belongs to a different session")
        if Path(record.workspace).resolve() != Path(self.workspace):
            raise TaskRuntimeError("task belongs to a different workspace")
        if parsed.get("goal_id") and record.goal_id and parsed["goal_id"] != record.goal_id:
            raise TaskRuntimeError("task goal_id mismatch")
        if record.status in {"completed", "failed", "cancelled"}:
            raise TaskRuntimeError(f"cannot attach terminal task ({record.status})")
        if record.status == "running":
            raise TaskBusy("task is already running")
        self.store.acquire_lock(task_id)
        return record
