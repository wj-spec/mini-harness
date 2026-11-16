"""stdio/TUI 与 TaskStore 的薄接线（spec: docs/specs/task-runtime-integration.md）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.runtime.budget import BudgetError, BudgetLedger
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

_GOAL_META_KEYS = frozenset({"kind", "objective", "task_id", "goal_id", "mode", "plan_id", "plan_version"})


class TaskRuntimeError(Exception):
    """任务运行时接入失败；stdio 映射为结构化 error。"""


class TaskRuntimeDisabled(TaskRuntimeError):
    pass


class TaskBudgetExceeded(TaskRuntimeError):
    def __init__(self, check: dict[str, Any]):
        self.check = check
        super().__init__(f"budget exceeded: {check.get('reason')}")


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
    if kind not in {"goal", "run"}:
        raise TaskRuntimeError("task.kind must be goal or run")
    try:
        from app.runtime.modes import mode_for_kind
        mode = mode_for_kind(kind, meta.get("mode"))
    except ValueError as exc:
        raise TaskRuntimeError(str(exc)) from exc
    result: dict = {"kind": kind, "mode": mode}
    for key in ("objective", "task_id", "goal_id", "plan_id"):
        if key not in meta:
            continue
        value = meta[key]
        if not isinstance(value, str) or not value.strip():
            raise TaskRuntimeError(f"task.{key} must be a nonempty string")
        if key == "objective" and len(value.strip()) > 4000:
            raise TaskRuntimeError("task.objective exceeds 4000 characters")
        result[key] = value.strip()
    if "plan_version" in meta:
        version = meta["plan_version"]
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise TaskRuntimeError("task.plan_version must be a positive integer")
        result["plan_version"] = version
    return result


class TaskRuntime:
    def __init__(self, store: TaskStore, *, workspace: str | Path, enabled: bool = True):
        self.store = store
        self.workspace = str(Path(workspace).expanduser().resolve())
        self.enabled = enabled
        self.goals = GoalStore(self.store.root)
        self.budget = BudgetLedger(self.store.root)
        from app.runtime.goal_limits import goal_limits_from_env
        self.goal_initial = goal_limits_from_env()
        self.goal_defaults = lambda: dict(self.goal_initial)

    @staticmethod
    def _budget_hard_enabled() -> bool:
        return os.getenv("VIBE_TASK_BUDGET_HARD", "").strip().lower() in {"1", "true", "yes"}

    def prepare_run(self, request_id: str, meta: dict[str, Any] | None) -> RunTask | None:
        if meta is None:
            return None
        if not self.enabled:
            raise TaskRuntimeDisabled("task runtime is disabled")
        parsed = _validate_meta(meta)
        record = self.store.get(parsed['task_id']) if parsed.get('task_id') else None
        mode = record.mode if record is not None else parsed['mode']
        plan_id = record.plan_id if record is not None else parsed.get('plan_id')
        version = record.plan_version if record is not None else parsed.get('plan_version')
        if mode != 'plan' and plan_id:
            from app.runtime.plan import PlanError, content_hash, find_plan, plan_lock
            from app.runtime.plan_revision import PlanRevision

            found = find_plan(self.store.root, plan_id, version)
            if found is not None:
                directory, plan = found
                try:
                    with plan_lock(directory, require_process_lock=True):
                        ref = {'session_id': self.store.root.name, 'plan_task_id': plan.task_id,
                               'plan_id': plan.plan_id, 'plan_version': plan.version, 'plan_hash': content_hash(plan)}
                        PlanRevision(self.store.root, session_id=self.store.root.name,
                                     task_runtime=self).guard_review(ref)
                        return self._prepare_run(request_id, parsed)
                except PlanError as exc:
                    raise TaskRuntimeError(f'{exc.code}: {exc}') from exc
        return self._prepare_run(request_id, parsed)

    def _prepare_run(self, request_id, parsed):
        task_id = parsed.get("task_id")
        if task_id:
            record = self._attach(task_id, parsed)
        else:
            record = self._create(parsed)
        compatibility = None
        if record.kind == "goal":
            from app.runtime.goal_limits import goal_limit_reason, legacy_goal_limits
            try:
                if "goal_limits" not in record.extra:
                    record = self.store.freeze_goal_limits(record.task_id,
                        legacy_goal_limits(self.store.root, record, self.goal_initial))
                    compatibility = "旧目标预算已固定：优先匹配历史预算，缺失项沿用本次启动值；新默认不覆盖续做。"
                check = goal_limit_reason(record, self.budget.totals(task_id=record.task_id))
                if check:
                    raise TaskBudgetExceeded({"allowed": False, "reason": check})
            except TaskBudgetExceeded:
                self.store.release_lock(record.task_id)
                raise
            except (ValueError, OSError, TypeError, KeyError) as exc:
                self.store.release_lock(record.task_id)
                raise TaskRuntimeError("无法核对目标预算，请检查任务记录；不会重置上限。") from exc
        if self._budget_hard_enabled():
            check = self.budget.check_start(
                record.task_id,
                max_tokens=int(record.budget.get("max_goal_tokens", 0) or 0),
                max_wall_seconds=float(record.budget.get("max_wall_seconds", 0) or 0),
                started_at=record.created_at,
            )
            if not check["allowed"]:
                try:
                    self.store.release_lock(record.task_id)
                except Exception:
                    pass
                raise TaskBudgetExceeded(check)
        try:
            record = self.store.transition(record.task_id, "running", reason="run_start")
            segment = self.store.start_segment(record.task_id, run_id=request_id)
        except Exception:
            self.store.release_lock(record.task_id)
            raise
        record = self.store.get(record.task_id)
        goal_error = compatibility
        if record.kind == "goal":
            try:
                self.goals.ensure_for_task(record)
            except GoalError as exc:
                goal_error = f"{type(exc).__name__}: {exc}"
        return RunTask(
            task_id=record.task_id, run_id=request_id, segment=segment.segment,
            objective=record.objective, record=record, goal_error=goal_error,
        )

    def record_trace(self, run_task: RunTask | None, event: dict[str, Any]) -> None:
        if run_task is None:
            return
        record = run_task.record
        try:
            self.budget.on_trace(
                task_id=run_task.task_id, goal_id=record.goal_id,
                parent_id=record.parent_id, node_kind=record.kind,
                segment=run_task.segment, run_id=run_task.run_id, event=event,
            )
        except BudgetError:
            pass

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
                self.budget.close_segment(
                    task_id=run_task.task_id, goal_id=record.goal_id,
                    parent_id=record.parent_id, node_kind=record.kind,
                    segment=run_task.segment, run_id=run_task.run_id,
                    status=segment_status, stop_reason=reason,
                )
                if record.status in {"completed", "failed", "cancelled"}:
                    self.budget.close_task(
                        task_id=run_task.task_id, goal_id=record.goal_id,
                        parent_id=record.parent_id, node_kind=record.kind,
                        status=record.status, reason=reason,
                    )
            except BudgetError:
                pass
            try:
                self.goals.sync_from_task(record)
            except GoalError:
                pass
        return record

    def recover(self) -> list[TaskRecord]:
        if not self.enabled:
            return []
        return self.store.recover_stale()

    def payload(self, record: TaskRecord) -> dict[str, Any]:
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
            "mode": record.mode,
            "status": record.status,
            "segment": segment,
            "objective": record.objective,
            "goal_id": record.goal_id,
            "plan_id": record.plan_id,
            "plan_version": record.plan_version,
            "updated_at": record.updated_at,
            **({"goal_budget": {
                "limits": record.extra["goal_limits"], "started_at": record.created_at,
                "used_tokens": self.budget.totals(task_id=record.task_id)["tokens_total"],
                "used_cost_usd": self.budget.totals(task_id=record.task_id)["cost_usd"],
            }} if record.kind == "goal" and "goal_limits" in record.extra else {}),
        }

    # ---- 内部 ----

    def _create(self, parsed: dict[str, str]) -> TaskRecord:
        objective = parsed.get("objective")
        if not objective:
            raise TaskRuntimeError("task.objective is required when creating a task")
        record = self.store.create(
            kind=parsed["kind"], objective=objective, workspace=self.workspace,
            mode=parsed["mode"], goal_id=parsed.get("goal_id"),
            plan_id=parsed.get("plan_id"), plan_version=parsed.get("plan_version"),
            goal_limits=self.goal_defaults() if parsed["kind"] == "goal" else None,
        )
        self.store.acquire_lock(record.task_id)
        return record

    def _attach(self, task_id: str, parsed: dict[str, str]) -> TaskRecord:
        record = self.store.get(task_id)
        if record.kind not in {"goal", "run"}:
            raise TaskRuntimeError("attached task must be kind=goal/run")
        if record.mode != parsed["mode"]:
            raise TaskRuntimeError("attached task mode mismatch")
        if "plan_id" in parsed and parsed["plan_id"] != record.plan_id:
            raise TaskRuntimeError("attached task plan_id mismatch")
        if "plan_version" in parsed and parsed["plan_version"] != record.plan_version:
            raise TaskRuntimeError("attached task plan_version mismatch")
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
