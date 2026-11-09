"""结构化 Plan 产物（12 P0）：schema 校验、原子存储、终端渲染。"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # Windows 旧管理入口保持可用。
    fcntl = None

PLAN_SCHEMA_VERSION = 1
MAX_PLAN_BYTES = 4 * 1024 * 1024
PLAN_STATUSES = ("draft", "proposed", "approved", "executing", "superseded", "legacy")
STEP_KINDS = ("read", "edit", "run", "verify")
STEP_RISKS = ("read", "write", "exec", "network")
EXECUTORS = ("self", "subagent:explore", "subagent:review")


class PlanError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_plan"):
        super().__init__(message)
        self.code = code


_PLAN_LOCKS = threading.local()


@contextmanager
def plan_lock(task_dir: str | Path, *, require_process_lock: bool = False):
    """同步短锁；同线程嵌套复用，不得跨 await 持有。"""
    if fcntl is None:
        if require_process_lock:
            raise PlanError("plan review requires process locking", code="unsupported_platform")
        yield
        return
    task = Path(task_dir)
    directory = task / "plan"
    if task.is_symlink() or directory.is_symlink():
        raise PlanError("plan directory is a symlink", code="needs_reconciliation")
    directory.mkdir(parents=True, exist_ok=True)
    key = (os.getpid(), str(directory.resolve()))
    active = getattr(_PLAN_LOCKS, "active", None)
    if active is None:
        active = _PLAN_LOCKS.active = {}
    if key in active:
        yield
        return
    path = directory / "review.lock"
    if path.is_symlink():
        raise PlanError("plan review lock is a symlink", code="needs_reconciliation")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PlanError("plan review lock is not a regular file", code="needs_reconciliation")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PlanError("plan review lock busy", code="lock_busy") from exc
        active[key] = fd
        try:
            yield
        finally:
            del active[key]
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def find_dependency_cycle(steps: list[dict[str, Any]]) -> list[str] | None:
    """返回依赖环路径（如 ["s1","s2","s1"]）；无环返回 None。"""
    graph: dict[str, list[str]] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = step.get("step_id")
        if not isinstance(step_id, str):
            continue
        depends = step.get("depends_on") or []
        graph[step_id] = [item for item in depends if isinstance(item, str)] if isinstance(depends, list) else []
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for neighbor in graph.get(node, []):
            if neighbor not in graph:
                continue
            if state.get(neighbor) == 1:
                index = stack.index(neighbor)
                return stack[index:] + [neighbor]
            if state.get(neighbor) is None:
                found = visit(neighbor)
                if found is not None:
                    return found
        state[node] = 2
        stack.pop()
        return None

    for node in graph:
        if state.get(node) is None:
            found = visit(node)
            if found is not None:
                return found
    return None


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
    review_feedback: list[str] = field(default_factory=list)
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
        if len(verification_plan) > 50:
            raise PlanError("verification_plan exceeds 50 entries")
        seen_verification = set()
        criterion_ids = seen
        for item in verification_plan:
            vid = _text(item.get("id"), "verification.id", 128)
            if vid in seen_verification:
                raise PlanError(f"duplicate verification id: {vid}")
            seen_verification.add(vid)
            command = item.get("command")
            if not isinstance(command, str) or not command.strip():
                raise PlanError(f"verification {vid}: command must be a nonempty string")
            if len(command) > 4000:
                raise PlanError(f"verification {vid}: command too long")
            covers = item.get("covers", [])
            if not isinstance(covers, list) or not all(isinstance(cid, str) for cid in covers):
                raise PlanError(f"verification {vid}: covers must be a string list")
            unknown = [cid for cid in covers if cid not in criterion_ids]
            if unknown:
                raise PlanError(f"verification {vid}: unknown criteria {unknown}")
            source = item.get("source", "plan_approval")
            if source not in {"project_profile", "plan_approval"}:
                raise PlanError(f"verification {vid}: invalid source {source!r}")
        trusted_source = value.get("trusted_source", "plan_approval")
        if trusted_source not in {"plan_approval", "project_profile"}:
            raise PlanError("invalid trusted_source")
        for name in ("constraints", "out_of_scope", "open_questions"):
            if name in value and (not isinstance(value[name], list) or
                                  not all(isinstance(item, str) for item in value[name])):
                raise PlanError(f"{name} must be a string list")
        feedback = value.get("review_feedback", [])
        if (not isinstance(feedback, list) or len(feedback) > 20
                or not all(isinstance(item, str) and item.strip() for item in feedback)):
            raise PlanError("review_feedback must be a list of nonempty strings")
        created = value.get("created_at")
        if created is None:
            created = datetime.now(timezone.utc).isoformat()
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
            review_feedback=[item.strip()[:4000] for item in feedback],
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


def validate_plan_snapshot(task_dir: str | Path, plan: Plan, *, missing_ok: bool = False) -> Plan | None:
    """持有 plan_lock 时校验调用者读到的完整状态。"""
    path = plan_path(task_dir, plan.version)
    if path.is_symlink():
        raise PlanError("plan file is a symlink", code="needs_reconciliation")
    if not path.exists():
        if missing_ok:
            return None
        raise PlanError("plan file no longer exists", code="not_found")
    if not path.is_file():
        raise PlanError("plan path is not a regular file", code="needs_reconciliation")
    current = load_plan(path)
    if current.to_dict() != plan.to_dict():
        raise PlanError("plan changed; reload before modifying", code="content_changed")
    return current


def save_plan(task_dir: str | Path, plan: Plan, *, expected: Plan | None = None) -> Path:
    with plan_lock(task_dir):
        target = plan_path(task_dir, plan.version)
        if expected is None:
            if target.exists() or target.is_symlink():
                raise PlanError("plan version already exists", code="content_changed")
        else:
            if expected.version != plan.version or expected.plan_id != plan.plan_id:
                raise PlanError("expected plan identity mismatch", code="target_mismatch")
            current = validate_plan_snapshot(task_dir, expected)
            if current.to_dict() == plan.to_dict():
                return target
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(raw) > MAX_PLAN_BYTES:
            raise PlanError("plan file exceeds 4 MiB")
        fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            if os.name == "posix":
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return target


def load_plan(path: str | Path) -> Plan:
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(MAX_PLAN_BYTES + 1)
    except OSError as exc:
        raise PlanError(f"plan file unreadable: {exc}") from exc
    if len(raw) > MAX_PLAN_BYTES:
        raise PlanError("plan file exceeds 4 MiB")
    try:
        value = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise PlanError("plan file is not valid UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise PlanError("plan file is not valid JSON") from exc
    if isinstance(value, dict) and not value.get("created_at"):
        value["created_at"] = ""
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
    if plan.review_feedback:
        lines.append("审阅反馈：")
        for item in plan.review_feedback[-5:]:
            lines.append(f"- {item}")
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


def render_approval_card(plan: Plan) -> str:
    """渲染计划审批卡：约束/验收/验证命令/步骤/门禁/hash/不可执行原因。"""
    approval = plan.approval if isinstance(plan.approval, dict) else {}
    lines = [f"计划审批卡：{plan.plan_id} · v{plan.version} · {plan.status}"
             + (" · stale" if approval.get("stale") else "")]
    lines.append(f"目标：{plan.objective}")
    if plan.constraints:
        lines.append("约束：" + "；".join(plan.constraints))
    if plan.out_of_scope:
        lines.append("范围外：" + "；".join(plan.out_of_scope))
    if plan.review_feedback:
        lines.append("审阅反馈：")
        for item in plan.review_feedback[-5:]:
            lines.append(f"- {item}")
    if plan.acceptance_criteria:
        lines.append("验收标准：")
        for item in plan.acceptance_criteria:
            mark = "已确认" if item.get("confirmed") is True else "候选"
            lines.append(f"- [{mark}] {item.get('text', '')}")
    if plan.verification_plan:
        lines.append("验证命令：")
        for item in plan.verification_plan:
            covers = ",".join(item.get("covers") or [])
            lines.append(f"- {item.get('id')} covers={covers} source={item.get('source')} cmd={item.get('command')}")
    lines.append("步骤：")
    for index, step in enumerate(plan.steps, 1):
        deps = f" depends={','.join(step.depends_on)}" if step.depends_on else ""
        files = f" files={','.join(step.files)}" if step.files else ""
        lines.append(f"{index}. {step.step_id} {step.title} [{step.kind}/{step.risk}]{deps}{files}")
    executable = can_execute(plan)
    lines.append(f"执行门禁：executable={executable}")
    if not executable:
        reasons = []
        if plan.status != "approved":
            reasons.append(f"status={plan.status}")
        if approval.get("capability") != "plan.review":
            reasons.append("缺少 plan.review 审批")
        if approval.get("stale"):
            reasons.append("审批已 stale")
        if approval.get("plan_hash") != content_hash(plan):
            reasons.append("hash 不匹配")
        if not approval.get("approved_by"):
            reasons.append("无批准人")
        lines.append("不可执行原因：" + "；".join(reasons or ["未知"]))
    lines.append(f"批准：by={approval.get('approved_by') or '-'} at={approval.get('approved_at') or '-'}")
    lines.append(f"hash 匹配：{approval.get('plan_hash') == content_hash(plan)}")
    return "\n".join(lines)


def _audit(task_dir: str | Path, event: str, **data: Any) -> None:
    directory = Path(task_dir)
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "event": event,
        "ts": datetime.now(timezone.utc).isoformat(),
        **data,
    }
    path = directory / "audit.jsonl"
    if path.is_symlink():
        raise PlanError("plan audit is a symlink", code="needs_reconciliation")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK
                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise PlanError("plan audit is not an unlinked regular file", code="needs_reconciliation")
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def audit_event(task_dir: str | Path, event: str, **data: Any) -> None:
    """公开的审计入口：计划审阅等运行时动作复用同一 audit.jsonl。"""
    with plan_lock(task_dir):
        _audit(task_dir, event, **data)


def propose_plan(task_dir: str | Path, plan: Plan, *, requester: str = "harness") -> Path:
    with plan_lock(task_dir):
        current = validate_plan_snapshot(task_dir, plan, missing_ok=True)
        if plan.status not in {"draft", "proposed"}:
            raise PlanError(f"cannot propose plan in status {plan.status}")
        updated = replace(plan, status="proposed")
        path = save_plan(task_dir, updated, expected=current)
        plan.status = updated.status
        _audit(task_dir, "plan_proposed", task_id=plan.task_id, plan_id=plan.plan_id,
               version=plan.version, plan_hash=content_hash(plan), requester=requester)
        return path


def check_plan_review_window(task_dir: str | Path, plan: Plan) -> None:
    """校验既有 proposed 审阅窗口；不刷新时间或写文件。"""
    try:
        review_ttl = float(os.getenv("VIBE_PLAN_REVIEW_TIMEOUT_SECONDS", "0") or 0)
    except ValueError:
        review_ttl = 0.0
    if review_ttl > 0:
        try:
            age = time.time() - plan_path(task_dir, plan.version).stat().st_mtime
        except OSError:
            age = 0.0
        if age > review_ttl:
            raise PlanError("plan review window expired; revise the plan and request approval again",
                            code="review_expired")


def approve_plan(
    task_dir: str | Path, plan: Plan, *,
    approved_by: str, capability: str = "plan.review",
    approval_id: str | None = None, decider: str = "user",
) -> Path:
    with plan_lock(task_dir):
        current = validate_plan_snapshot(task_dir, plan)
        if capability != "plan.review":
            raise PlanError("plan approval requires plan.review capability")
        if plan.status != "proposed":
            raise PlanError(f"cannot approve plan in status {plan.status}")
        if not approved_by.strip():
            raise PlanError("approved_by must be nonempty")
        check_plan_review_window(task_dir, plan)
        now = datetime.now(timezone.utc).isoformat()
        plan_hash = content_hash(plan)
        approval = {
            "approved_by": approved_by.strip(),
            "approved_at": now,
            "approval_id": approval_id,
            "plan_hash": plan_hash,
            "capability": capability,
            "requester": "harness",
            "decider": decider,
        }
        updated = replace(plan, status="approved", approval=approval)
        path = save_plan(task_dir, updated, expected=current)
        plan.status, plan.approval = updated.status, updated.approval
        _audit(task_dir, "plan_approved", task_id=plan.task_id, plan_id=plan.plan_id,
               version=plan.version, plan_hash=plan_hash, approver=approved_by,
               decider=decider, capability=capability, approval_id=approval_id)
        return path


def reject_plan(
    task_dir: str | Path, plan: Plan, *,
    rejected_by: str, reason: str = "", decider: str = "user",
) -> Path:
    with plan_lock(task_dir):
        current = validate_plan_snapshot(task_dir, plan)
        if plan.status not in {"proposed", "approved"}:
            raise PlanError(f"cannot reject plan in status {plan.status}")
        approval = {
            "approved_by": None, "approved_at": None, "approval_id": None,
            "plan_hash": None, "capability": None,
            "requester": "harness", "decider": decider,
            "rejected_by": rejected_by, "rejected_reason": reason,
        }
        updated = replace(plan, status="draft", approval=approval)
        path = save_plan(task_dir, updated, expected=current)
        plan.status, plan.approval = updated.status, updated.approval
        _audit(task_dir, "plan_rejected", task_id=plan.task_id, plan_id=plan.plan_id,
               version=plan.version, rejected_by=rejected_by, reason=reason, decider=decider)
        return path


def mark_plan_stale(task_dir: str | Path, plan: Plan) -> Path:
    """使当前批准失效；不覆盖其它调用者保存的状态。"""
    with plan_lock(task_dir):
        current = validate_plan_snapshot(task_dir, plan)
        if plan.status != "approved":
            raise PlanError("only an approved plan can become stale", code="plan_stale")
        updated = replace(plan, approval={**plan.approval, "stale": True})
        path = save_plan(task_dir, updated, expected=current)
        plan.approval = updated.approval
        return path


def revise_plan(
    task_dir: str | Path,
    plan: Plan,
    *,
    objective: str | None = None,
    steps: list[dict[str, Any]] | None = None,
    acceptance_criteria: list[dict[str, Any]] | None = None,
    verification_plan: list[dict[str, Any]] | None = None,
    feedback: str | None = None,
    requester: str = "user",
) -> Plan:
    """生成 version+1 的 draft；旧版本保留，审批失效。"""
    with plan_lock(task_dir):
        validate_plan_snapshot(task_dir, plan)
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
        if feedback is not None:
            text = feedback.strip()
            if not text:
                raise PlanError("review feedback must be a nonempty string")
            payload["review_feedback"] = list(payload.get("review_feedback", [])) + [text[:4000]]
        revised = Plan.from_dict(payload)
        save_plan(task_dir, revised)
        _audit(task_dir, "plan_revised", task_id=revised.task_id, plan_id=revised.plan_id,
               version=revised.version, requester=requester, previous_version=plan.version)
        return revised


def propose_revision(
    task_dir: str | Path, plan: Plan, *, content: dict[str, Any], feedback: str, revision_id: str,
) -> Plan:
    """发布同族新版；来源由控制器绑定，模型只提供业务内容。"""
    fields = {"objective", "steps", "acceptance_criteria", "verification_plan",
              "constraints", "out_of_scope", "open_questions"}
    if not isinstance(content, dict) or set(content) - fields or not {"objective", "steps"} <= set(content):
        raise PlanError("revision requires objective/steps and cannot change identity", code="invalid_revision")
    if not isinstance(feedback, str) or not feedback.strip() or len(feedback.strip()) > 4000:
        raise PlanError("revision feedback must contain 1-4000 characters", code="invalid_revision")
    if not isinstance(revision_id, str) or len(revision_id) != 64 or any(c not in "0123456789abcdef" for c in revision_id):
        raise PlanError("invalid revision operation id", code="invalid_revision")
    if Path(task_dir).name != plan.task_id:
        raise PlanError("source plan belongs to another task", code="target_mismatch")
    with plan_lock(task_dir, require_process_lock=True):
        validate_plan_snapshot(task_dir, plan)
        if plan.status not in {"draft", "proposed", "approved"}:
            raise PlanError("source plan is not reviewable", code="plan_stale")
        origin = {"id": revision_id, "base_version": plan.version,
                  "base_hash": content_hash(plan), "feedback": feedback.strip()}
        published_path = plan_path(task_dir, plan.version + 1)
        if published_path.exists() or published_path.is_symlink():
            if published_path.is_symlink() or not published_path.is_file():
                raise PlanError("revision output is not a regular file", code="needs_reconciliation")
            published = load_plan(published_path)
            if (published.plan_id == plan.plan_id and published.task_id == plan.task_id
                    and published.version == plan.version + 1 and published.source.get("revision") == origin):
                _audit(task_dir, "plan_revision_reused", task_id=published.task_id,
                       plan_id=published.plan_id, version=published.version,
                       plan_hash=content_hash(published), revision_id=revision_id,
                       previous_version=plan.version, requester="harness")
                return published
            raise PlanError("another revision already occupies this version", code="content_changed")
        for candidate in plan_dir(task_dir).glob("plan.v*.json"):
            suffix = candidate.stem.removeprefix("plan.v")
            if suffix.isdigit() and int(suffix) > plan.version:
                raise PlanError("a newer plan version exists", code="superseded")
        payload = plan.to_dict()
        payload.update(content)
        payload.update(version=plan.version + 1, status="proposed", approval={},
                       created_at=datetime.now(timezone.utc).isoformat(),
                       review_feedback=[*plan.review_feedback, feedback.strip()],
                       source={**plan.source, "revision": origin})
        revised = Plan.from_dict(payload)
        propose_plan(task_dir, revised, requester="harness")
        return revised


def executable_plans(session_root: str | Path) -> list[tuple[Path, Plan]]:
    """按 mtime 倒序列出会话中可执行的 approved 计划（供后台排队/CLI 复用）。"""
    from app.runtime.plan_revision import PlanRevision

    root = Path(session_root)
    revisions = PlanRevision(root, session_id=root.name)
    tasks_root = Path(session_root) / "tasks"
    if not tasks_root.is_dir():
        return []
    candidates = sorted(
        tasks_root.glob("*/plan/plan.v*.json"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    result: list[tuple[Path, Plan]] = []
    for path in candidates:
        try:
            plan = load_plan(path)
            if not can_execute(plan):
                continue
            revisions.guard_review({'session_id': root.name, 'plan_task_id': plan.task_id,
                                    'plan_id': plan.plan_id, 'plan_version': plan.version, 'plan_hash': content_hash(plan)})
        except PlanError:
            continue
        result.append((path, plan))
    return result


def can_execute(plan: Plan) -> bool:
    return (
        plan.status == "approved"
        and isinstance(plan.approval, dict)
        and plan.approval.get("capability") == "plan.review"
        and not plan.approval.get("stale")
        and plan.approval.get("plan_hash") == content_hash(plan)
        and bool(plan.approval.get("approved_by"))
    )
