"""计划执行绑定（14 P0）：harness 拥有的 step ledger，证据推进状态。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime.plan import Plan, PlanError, PlanStep

EXECUTION_SCHEMA_VERSION = 1
STEP_STATUSES = ("pending", "in_progress", "blocked", "done", "deviated", "skipped")


class ExecutionError(ValueError):
    pass


@dataclass
class StepRecord:
    step_id: str
    status: str = "pending"
    evidence: list[dict[str, Any]] = field(default_factory=list)
    attempts: int = 0
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StepLedger:
    task_id: str
    plan_id: str
    plan_version: int
    steps: dict[str, StepRecord]
    schema_version: int = EXECUTION_SCHEMA_VERSION
    updated_at: str = ""

    @classmethod
    def from_plan(cls, plan: Plan) -> "StepLedger":
        return cls(
            task_id=plan.task_id, plan_id=plan.plan_id, plan_version=plan.version,
            steps={step.step_id: StepRecord(step_id=step.step_id) for step in plan.steps},
            updated_at=_now(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "steps": [record.to_dict() for record in self.steps.values()],
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "StepLedger":
        if not isinstance(value, dict):
            raise ExecutionError("ledger must be an object")
        if value.get("schema_version") != EXECUTION_SCHEMA_VERSION:
            raise ExecutionError("unsupported execution schema_version")
        task_id = value.get("task_id")
        plan_id = value.get("plan_id")
        version = value.get("plan_version")
        if not isinstance(task_id, str) or not task_id:
            raise ExecutionError("task_id must be a nonempty string")
        if not isinstance(plan_id, str) or not plan_id:
            raise ExecutionError("plan_id must be a nonempty string")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ExecutionError("plan_version must be a positive integer")
        raw_steps = value.get("steps")
        if not isinstance(raw_steps, list):
            raise ExecutionError("steps must be a list")
        steps: dict[str, StepRecord] = {}
        for item in raw_steps:
            if not isinstance(item, dict):
                raise ExecutionError("step record must be an object")
            step_id = item.get("step_id")
            if not isinstance(step_id, str) or not step_id or step_id in steps:
                raise ExecutionError("invalid/duplicate step_id")
            status = item.get("status", "pending")
            if status not in STEP_STATUSES:
                raise ExecutionError(f"invalid step status: {status!r}")
            evidence = item.get("evidence", [])
            if not isinstance(evidence, list):
                raise ExecutionError("evidence must be a list")
            attempts = item.get("attempts", 0)
            if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
                raise ExecutionError("attempts must be a nonnegative integer")
            steps[step_id] = StepRecord(
                step_id=step_id, status=status,
                evidence=[dict(entry) for entry in evidence if isinstance(entry, dict)],
                attempts=attempts, updated_at=str(item.get("updated_at", "")),
            )
        return cls(
            schema_version=EXECUTION_SCHEMA_VERSION, task_id=task_id, plan_id=plan_id,
            plan_version=version, steps=steps, updated_at=str(value.get("updated_at", "")),
        )

    def record(self, step_id: str) -> StepRecord:
        try:
            return self.steps[step_id]
        except KeyError as exc:
            raise ExecutionError(f"unknown step: {step_id}") from exc

    def start_step(self, step_id: str) -> StepRecord:
        record = self.record(step_id)
        if record.status not in {"pending", "blocked"}:
            raise ExecutionError(f"step {step_id} cannot start from {record.status}")
        record.status = "in_progress"
        record.attempts += 1
        record.updated_at = _now()
        self.updated_at = _now()
        return record

    def add_evidence(self, step_id: str, *, kind: str, ref: str, ok: bool = True) -> StepRecord:
        if kind not in {"file_change", "verification", "command_ok", "manual"}:
            raise ExecutionError(f"invalid evidence kind: {kind!r}")
        if not isinstance(ref, str) or not ref.strip():
            raise ExecutionError("evidence ref must be a nonempty string")
        record = self.record(step_id)
        record.evidence.append({"kind": kind, "ref": ref, "ok": bool(ok), "at": _now()})
        record.updated_at = _now()
        self.updated_at = _now()
        return record

    def complete_step(self, plan: Plan, step_id: str) -> StepRecord:
        step = _plan_step(plan, step_id)
        record = self.record(step_id)
        if record.status == "done":
            return record
        if record.status not in {"in_progress", "blocked"}:
            raise ExecutionError(f"step {step_id} cannot complete from {record.status}")
        if step.verification:
            ok = [item for item in record.evidence
                  if item.get("ok") and item.get("kind") in {"verification", "command_ok"}]
            if not ok:
                raise ExecutionError("step_evidence_missing: verification evidence required")
        elif not record.evidence:
            raise ExecutionError("step_evidence_missing: at least one evidence ref required")
        record.status = "done"
        record.updated_at = _now()
        self.updated_at = _now()
        return record

    def mark_deviated(self, step_id: str, paths: list[str]) -> StepRecord:
        record = self.record(step_id)
        record.status = "deviated"
        record.evidence.append({"kind": "deviation", "paths": list(paths), "at": _now()})
        record.updated_at = _now()
        self.updated_at = _now()
        return record

    def plan_complete(self, plan: Plan) -> bool:
        for step in plan.steps:
            record = self.record(step.step_id)
            if record.status == "skipped":
                continue
            if record.status != "done":
                return False
        return True

    def can_execute_step(self, plan: Plan, step_id: str) -> bool:
        step = _plan_step(plan, step_id)
        for dep in step.depends_on:
            if self.record(dep).status != "done":
                return False
        return self.record(step_id).status in {"pending", "blocked", "in_progress"}


def _plan_step(plan: Plan, step_id: str) -> PlanStep:
    for step in plan.steps:
        if step.step_id == step_id:
            return step
    raise ExecutionError(f"step not in plan: {step_id}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def ledger_path(task_dir: str | Path, plan_version: int) -> Path:
    return Path(task_dir) / "plan" / f"execution.v{plan_version}.json"


def save_ledger(task_dir: str | Path, ledger: StepLedger) -> Path:
    target = ledger_path(task_dir, ledger.plan_version)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(ledger.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return target


def load_ledger(task_dir: str | Path, plan_version: int) -> StepLedger:
    path = ledger_path(task_dir, plan_version)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExecutionError(f"ledger unreadable: {exc}") from exc
    try:
        return StepLedger.from_dict(json.loads(raw))
    except json.JSONDecodeError as exc:
        raise ExecutionError("ledger is not valid JSON") from exc
