"""结构化 Plan 产物（12 P0）：schema 校验、原子存储、终端渲染。"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PLAN_SCHEMA_VERSION = 1
PLAN_STATUSES = ("draft", "proposed", "approved", "executing", "superseded", "legacy")
STEP_KINDS = ("read", "edit", "run", "verify")
STEP_RISKS = ("read", "write", "exec", "network")
EXECUTORS = ("self", "subagent:explore", "subagent:review")


class PlanError(ValueError):
    pass


@dataclass
class PlanStep:
    step_id: str
    title: str
    kind: str = "read"
    depends_on: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    acceptance: str = ""
    verification: dict[str, Any] = field(default_factory=dict)
    risk: str = "read"
    executor: str = "self"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "PlanStep":
        if not isinstance(value, dict):
            raise PlanError("step must be an object")
        step_id = _text(value.get("step_id"), "step.step_id", 128)
        title = _text(value.get("title"), "step.title", 500)
        kind = value.get("kind", "read")
        if kind not in STEP_KINDS:
            raise PlanError(f"step {step_id}: invalid kind {kind!r}")
        risk = value.get("risk", "read")
        if risk not in STEP_RISKS:
            raise PlanError(f"step {step_id}: invalid risk {risk!r}")
        for name in ("depends_on", "files"):
            items = value.get(name, [])
            if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                raise PlanError(f"step {step_id}: {name} must be a string list")
        files = [item for item in value.get("files", [])]
        for path in files:
            if Path(path).is_absolute() or ".." in Path(path).parts or not path.strip():
                raise PlanError(f"step {step_id}: file path must be relative: {path!r}")
        acceptance = value.get("acceptance", "")
        if not isinstance(acceptance, str):
            raise PlanError(f"step {step_id}: acceptance must be a string")
        verification = value.get("verification", {})
        if not isinstance(verification, dict):
            raise PlanError(f"step {step_id}: verification must be an object")
        executor = value.get("executor", "self")
        if executor not in EXECUTORS:
            raise PlanError(f"step {step_id}: invalid executor {executor!r}")
        return cls(
            step_id=step_id, title=title, kind=kind,
            depends_on=list(value.get("depends_on", [])), files=files,
            acceptance=acceptance, verification=dict(verification),
            risk=risk, executor=executor,
        )


@dataclass
class Plan:
    plan_id: str
    task_id: str
    objective: str
    steps: list[PlanStep]
    version: int = 1
    status: str = "draft"
    parent_task_id: str | None = None
    goal_id: str | None = None
    source: dict[str, Any] = field(default_factory=dict)
    acceptance_criteria: list[dict[str, Any]] = field(default_factory=list)
    verification_plan: list[dict[str, Any]] = field(default_factory=list)
    trusted_source: str = "plan_approval"
    budget: dict[str, Any] = field(default_factory=dict)
    approval: dict[str, Any] = field(default_factory=dict)
    constraints: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    created_at: str = ""
    schema_version: int = PLAN_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["steps"] = [step.to_dict() for step in self.steps]
        return payload

    def acceptance(self):
        from app.runtime.acceptance import PlanAcceptance
        if not any(item.get("confirmed") is True and item.get("required", True) is not False
                   for item in self.acceptance_criteria):
            return None
        return PlanAcceptance.from_dict({
            "schema_version": 1,
            "task_id": self.task_id,
            "plan_id": self.plan_id,
            "plan_version": self.version,
            "acceptance_criteria": self.acceptance_criteria,
            "verification_plan": self.verification_plan,
            "trusted_source": self.trusted_source,
        })

    def plan_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_dict(cls, value: Any) -> "Plan":
        if not isinstance(value, dict):
            raise PlanError("plan must be an object")
        if value.get("schema_version", PLAN_SCHEMA_VERSION) != PLAN_SCHEMA_VERSION:
            raise PlanError("unsupported plan schema_version")
        plan_id = _text(value.get("plan_id"), "plan_id", 128)
        task_id = _text(value.get("task_id"), "task_id", 128)
        objective = _text(value.get("objective"), "objective", 4000)
        version = value.get("version", 1)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise PlanError("version must be a positive integer")
        status = value.get("status", "draft")
        if status not in PLAN_STATUSES:
            raise PlanError(f"invalid plan status: {status!r}")
        raw_steps = value.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise PlanError("steps must be a nonempty list")
        steps = [PlanStep.from_dict(item) for item in raw_steps]
        ids = [step.step_id for step in steps]
        if len(ids) != len(set(ids)):
            raise PlanError("step_id values must be unique")
        known = set(ids)
        for step in steps:
            missing = [item for item in step.depends_on if item not in known]
            if missing:
                raise PlanError(f"step {step.step_id}: missing dependencies {missing}")
        _assert_acyclic(steps)
        criteria = value.get("acceptance_criteria", [])
        if not isinstance(criteria, list):
            raise PlanError("acceptance_criteria must be a list")
        seen = set()
        for item in criteria:
            if not isinstance(item, dict):
                raise PlanError("acceptance criterion must be an object")
            cid = _text(item.get("id"), "criterion.id", 128)
            _text(item.get("text"), "criterion.text", 2000)
            if cid in seen:
                raise PlanError(f"duplicate criterion id: {cid}")
            seen.add(cid)
            if "confirmed" in item and not isinstance(item.get("confirmed"), bool):
                raise PlanError(f"criterion {cid}: confirmed must be boolean")
        for name in ("source", "budget", "approval"):
            if name in value and not isinstance(value[name], dict):
                raise PlanError(f"{name} must be an object")
        verification_plan = value.get("verification_plan", [])
        if not isinstance(verification_plan, list) or not all(
            isinstance(item, dict) for item in verification_plan
        ):
            raise PlanError("verification_plan must be an object list")
        trusted_source = value.get("trusted_source", "plan_approval")
        if trusted_source not in {"plan_approval", "project_profile"}:
            raise PlanError("invalid trusted_source")
        for name in ("constraints", "out_of_scope", "open_questions"):
            if name in value and (not isinstance(value[name], list) or
                                  not all(isinstance(item, str) for item in value[name])):
                raise PlanError(f"{name} must be a string list")
        created = value.get("created_at") or datetime.now(timezone.utc).isoformat()
        return cls(
            schema_version=PLAN_SCHEMA_VERSION, plan_id=plan_id, task_id=task_id,
            objective=objective, steps=steps, version=version, status=status,
            parent_task_id=value.get("parent_task_id"), goal_id=value.get("goal_id"),
            source=dict(value.get("source", {})),
            acceptance_criteria=[dict(item) for item in criteria],
            verification_plan=[dict(item) for item in value.get("verification_plan", [])],
            trusted_source=trusted_source,
            budget=dict(value.get("budget", {})), approval=dict(value.get("approval", {})),
            constraints=list(value.get("constraints", [])),
            out_of_scope=list(value.get("out_of_scope", [])),
            open_questions=list(value.get("open_questions", [])),
            created_at=str(created),
        )


def _text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"{name} must be a nonempty string")
    if len(value) > limit:
        raise PlanError(f"{name} exceeds {limit} characters")
    return value.strip()


def _assert_acyclic(steps: list[PlanStep]) -> None:
    graph = {step.step_id: list(step.depends_on) for step in steps}
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node: str) -> None:
        if node in done:
            return
        if node in visiting:
            raise PlanError(f"dependency cycle at step {node}")
        visiting.add(node)
        for dep in graph[node]:
            visit(dep)
        visiting.remove(node)
        done.add(node)

    for step in steps:
        visit(step.step_id)


def plan_dir(task_dir: str | Path) -> Path:
    return Path(task_dir) / "plan"


def plan_path(task_dir: str | Path, version: int) -> Path:
    return plan_dir(task_dir) / f"plan.v{version}.json"


def save_plan(task_dir: str | Path, plan: Plan) -> Path:
    target = plan_path(task_dir, plan.version)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return target


def load_plan(path: str | Path) -> Plan:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanError(f"plan file unreadable: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlanError("plan file is not valid JSON") from exc
    return Plan.from_dict(value)


def load_plan_ref(task_dir: str | Path, plan_id: str, version: int) -> Plan:
    plan = load_plan(plan_path(task_dir, version))
    if plan.plan_id != plan_id:
        raise PlanError("plan_id mismatch")
    return plan


def find_plan(session_root: str | Path, plan_id: str, version: int | None = None) -> tuple[Path, Plan] | None:
    tasks_root = Path(session_root) / "tasks"
    if not tasks_root.is_dir():
        return None
    candidates: list[tuple[int, Path, Plan]] = []
    for path in tasks_root.glob("*/plan/plan.v*.json"):
        try:
            plan = load_plan(path)
        except PlanError:
            continue
        if plan.plan_id != plan_id or (version is not None and plan.version != version):
            continue
        candidates.append((plan.version, path.parent.parent, plan))
    if not candidates:
        return None
    _, task_dir, plan = max(candidates, key=lambda item: item[0])
    return task_dir, plan


def render_plan(plan: Plan) -> str:
    lines = [f"计划：{plan.objective}", f"状态：{plan.status} · version {plan.version}"]
    if plan.acceptance_criteria:
        lines.append("验收标准：")
        for item in plan.acceptance_criteria:
            mark = "已确认" if item.get("confirmed") is True else "候选"
            lines.append(f"- [{mark}] {item.get('text', '')}")
    lines.append("步骤：")
    for index, step in enumerate(plan.steps, 1):
        deps = f" depends={','.join(step.depends_on)}" if step.depends_on else ""
        lines.append(f"{index}. {step.step_id} {step.title} [{step.kind}/{step.risk}]{deps}")
        if step.files:
            lines.append(f"   files: {', '.join(step.files)}")
        if step.acceptance:
            lines.append(f"   acceptance: {step.acceptance}")
        if step.verification:
            lines.append(f"   verification: {json.dumps(step.verification, ensure_ascii=False)}")
    return "\n".join(lines)


def content_hash(plan: Plan) -> str:
    payload = plan.to_dict()
    payload["approval"] = {}
    payload["status"] = "draft"
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _audit(task_dir: str | Path, event: str, **data: Any) -> None:
    directory = Path(task_dir)
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "event": event,
        "ts": datetime.now(timezone.utc).isoformat(),
        **data,
    }
    with (directory / "audit.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def propose_plan(task_dir: str | Path, plan: Plan, *, requester: str = "harness") -> Path:
    if plan.status not in {"draft", "proposed"}:
        raise PlanError(f"cannot propose plan in status {plan.status}")
    plan.status = "proposed"
    path = save_plan(task_dir, plan)
    _audit(task_dir, "plan_proposed", task_id=plan.task_id, plan_id=plan.plan_id,
           version=plan.version, plan_hash=content_hash(plan), requester=requester)
    return path


def approve_plan(
    task_dir: str | Path, plan: Plan, *,
    approved_by: str, capability: str = "plan.review",
    approval_id: str | None = None, decider: str = "user",
) -> Path:
    if capability != "plan.review":
        raise PlanError("plan approval requires plan.review capability")
    if plan.status != "proposed":
        raise PlanError(f"cannot approve plan in status {plan.status}")
    if not approved_by.strip():
        raise PlanError("approved_by must be nonempty")
    now = datetime.now(timezone.utc).isoformat()
    plan_hash = content_hash(plan)
    plan.status = "approved"
    plan.approval = {
        "approved_by": approved_by.strip(),
        "approved_at": now,
        "approval_id": approval_id,
        "plan_hash": plan_hash,
        "capability": capability,
        "requester": "harness",
        "decider": decider,
    }
    path = save_plan(task_dir, plan)
    _audit(task_dir, "plan_approved", task_id=plan.task_id, plan_id=plan.plan_id,
           version=plan.version, plan_hash=plan_hash, approver=approved_by,
           decider=decider, capability=capability, approval_id=approval_id)
    return path


def reject_plan(
    task_dir: str | Path, plan: Plan, *,
    rejected_by: str, reason: str = "", decider: str = "user",
) -> Path:
    if plan.status not in {"proposed", "approved"}:
        raise PlanError(f"cannot reject plan in status {plan.status}")
    plan.status = "draft"
    plan.approval = {
        "approved_by": None, "approved_at": None, "approval_id": None,
        "plan_hash": None, "capability": None,
        "requester": "harness", "decider": decider,
        "rejected_by": rejected_by, "rejected_reason": reason,
    }
    path = save_plan(task_dir, plan)
    _audit(task_dir, "plan_rejected", task_id=plan.task_id, plan_id=plan.plan_id,
           version=plan.version, rejected_by=rejected_by, reason=reason, decider=decider)
    return path


def revise_plan(
    task_dir: str | Path,
    plan: Plan,
    *,
    objective: str | None = None,
    steps: list[dict[str, Any]] | None = None,
    acceptance_criteria: list[dict[str, Any]] | None = None,
    verification_plan: list[dict[str, Any]] | None = None,
    requester: str = "user",
) -> Plan:
    """生成 version+1 的 draft；旧版本保留，审批失效。"""
    payload = plan.to_dict()
    payload["version"] = plan.version + 1
    payload["status"] = "draft"
    payload["approval"] = {}
    if objective is not None:
        payload["objective"] = objective
    if steps is not None:
        payload["steps"] = steps
    if acceptance_criteria is not None:
        payload["acceptance_criteria"] = acceptance_criteria
    if verification_plan is not None:
        payload["verification_plan"] = verification_plan
    revised = Plan.from_dict(payload)
    save_plan(task_dir, revised)
    _audit(task_dir, "plan_revised", task_id=revised.task_id, plan_id=revised.plan_id,
           version=revised.version, requester=requester, previous_version=plan.version)
    return revised


def can_execute(plan: Plan) -> bool:
    return (
        plan.status == "approved"
        and isinstance(plan.approval, dict)
        and plan.approval.get("capability") == "plan.review"
        and not plan.approval.get("stale")
        and plan.approval.get("plan_hash") == content_hash(plan)
        and bool(plan.approval.get("approved_by"))
    )
