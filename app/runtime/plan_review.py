"""计划审阅：准确对象查询、审阅稿、反馈修订与关闭记录。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime.plan import (
    Plan,
    PlanError,
    approve_plan,
    audit_event,
    can_execute,
    check_plan_review_window,
    content_hash,
    load_plan,
    plan_lock,
    plan_path,
    propose_plan,
    revise_plan,
    validate_plan_snapshot,
)
from app.runtime.task_runtime import RunTask, TaskRuntime, TaskRuntimeError
from app.runtime.tasks import TASK_ID_RE, TaskError, TaskStore

_PLAN_FILE_RE = re.compile(r"plan\.v([1-9][0-9]*)\.json")


@dataclass(frozen=True)
class ReviewDecision:
    receipt: dict[str, Any]
    run_task: RunTask | None = None


class _ReviewFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _valid_ref(ref: Any) -> bool:
    if not isinstance(ref, dict) or set(ref) != {
        "session_id", "plan_task_id", "plan_id", "plan_version", "plan_hash",
    }:
        return False
    for key in ("session_id", "plan_task_id", "plan_id"):
        value = ref[key]
        if (not isinstance(value, str) or not value or value != value.strip()
                or len(value) > 128 or any(ord(char) < 32 for char in value)):
            return False
    version = ref["plan_version"]
    return bool(
        TASK_ID_RE.fullmatch(ref["plan_task_id"])
        and isinstance(version, int) and not isinstance(version, bool) and version > 0
        and isinstance(ref["plan_hash"], str)
        and re.fullmatch(r"[0-9a-f]{64}", ref["plan_hash"])
    )


class PlanReview:
    """会话内的计划审阅入口；查询不改变计划或运行状态。"""

    def __init__(self, session_root: str | Path, *, session_id: str,
                 task_runtime: TaskRuntime | None = None):
        self.session_root = Path(session_root)
        self.session_id = session_id
        self.task_runtime = task_runtime
        self.store = TaskStore(self.session_root)

    def _target(self, plan: Plan) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "plan_task_id": plan.task_id,
            "plan_id": plan.plan_id,
            "plan_version": plan.version,
            "plan_hash": content_hash(plan),
        }

    def _unavailable(self, ref, code: str, message: str, replacement=None) -> dict[str, Any]:
        return {
            "outcome": "unavailable", "code": code, "message": message,
            "target": ref, "replacement_target": replacement, "launch": None,
            "summary": None, "body_page": "", "next_cursor": None,
            "available_actions": [],
        }

    def prepare_background(self, ref, *, priority: int = 0):
        """复核与排队创建共用 Plan 锁；重复准备复用原任务。"""
        try:
            return self._prepare_background(ref, priority)
        except (_ReviewFailure, OSError, TaskError, UnicodeError, RecursionError) as exc:
            raise PlanError(str(exc), code=getattr(exc, 'code', 'storage_error')) from exc

    def _prepare_background(self, ref, priority):
        from app.runtime.plan_revision import PlanRevision

        if not _valid_ref(ref) or ref['session_id'] != self.session_id:
            raise PlanError('后台准备需要准确计划身份', code='invalid_request')
        if type(priority) is not int or not 0 <= priority <= 100:
            raise PlanError('优先级须为 0–100 的整数', code='invalid_request')
        runtime = self.task_runtime
        if runtime is None or not runtime.enabled:
            raise PlanError('任务运行时未启用', code='tasks_disabled')
        if runtime.store.root.resolve() != self.session_root.resolve() or self.session_root.name != self.session_id:
            raise PlanError('任务运行时不属于当前会话', code='target_mismatch')
        directory = self.session_root / 'tasks' / ref['plan_task_id']
        self._check_path(directory, directory=True)
        with plan_lock(directory, require_process_lock=True):
            snapshot = self._read_review(ref, 0)
            if snapshot['code']:
                raise PlanError(snapshot['message'], code=snapshot['code'])
            if not snapshot['summary']['executable']:
                raise PlanError('计划尚未批准或批准已失效', code='plan_stale')
            PlanRevision(self.session_root, session_id=self.session_id, task_runtime=runtime).guard_review(ref)
            plan = self._read_plan(plan_path(directory, ref['plan_version']))
            records = self.store.list()
            if self.store.last_list_skipped:
                raise PlanError('已有任务记录无法核对', code='needs_reconciliation')
            for record in records:
                if record.mode not in {'act', 'goal'} or record.plan_id != plan.plan_id:
                    continue
                if record.status in {'queued', 'running'}:
                    raise PlanError('计划已有活动执行任务', code='plan_busy')
                if record.status == 'created' and record.plan_version == plan.version:
                    if (record.session_id != self.session_id or record.objective != plan.objective
                            or Path(record.workspace).resolve() != Path(runtime.workspace)):
                        raise PlanError('原后台任务范围无法核对', code='needs_reconciliation')
                    return record, True
            task = self.store.create(kind='run', mode='act', objective=plan.objective,
                                     workspace=runtime.workspace, parent_id=plan.task_id, goal_id=plan.goal_id,
                                     plan_id=plan.plan_id, plan_version=plan.version, priority=priority,
                                     session_id=self.session_id)
            return task, False

    def _launch_key(self, ref: dict[str, Any]) -> str:
        raw = json.dumps({"target": ref, "operation": "approve-execute"},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _operation_path(self, ref) -> Path:
        return (self.session_root / "tasks" / ref["plan_task_id"] / "plan"
                / "review-launch" / f"{self._launch_key(ref)}.json")

    def _save_operation(self, operation: dict[str, Any]) -> None:
        path = self._operation_path(operation["target"])
        self._check_path(path.parent.parent, directory=True)
        path.parent.mkdir(exist_ok=True)
        self._check_path(path.parent, directory=True)
        if path.exists() or path.is_symlink():
            self._check_path(path)
        operation["updated_at"] = datetime.now(timezone.utc).isoformat()
        fd, name = tempfile.mkstemp(prefix=".launch-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(operation, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
            for directory in (path.parent, path.parent.parent):
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            Path(name).unlink(missing_ok=True)

    def _load_operation(self, ref) -> dict[str, Any] | None:
        path = self._operation_path(ref)
        if not path.parent.exists() and not path.parent.is_symlink():
            return None
        self._check_path(path.parent, directory=True)
        if not path.exists() and not path.is_symlink():
            return None
        self._check_path(path)
        try:
            with path.open("rb") as handle:
                raw = handle.read(65537)
            if len(raw) > 65536:
                raise ValueError("operation exceeds limit")
            operation = json.loads(raw)
            if (not isinstance(operation, dict) or type(operation.get("schema_version")) is not int
                    or operation["schema_version"] != 1 or not _valid_ref(operation.get("target"))
                    or operation["target"] != ref or operation.get("key") != self._launch_key(ref)
                    or not isinstance(operation.get("task_id"), str)
                    or not TASK_ID_RE.fullmatch(operation["task_id"])
                    or operation.get("stage") not in ("reserved", "starting", "started")
                    or not isinstance(operation.get("prepare_failed", False), bool)):
                raise ValueError("invalid operation identity")
            if operation["stage"] != "reserved" and (
                not isinstance(operation.get("run_id"), str) or not operation["run_id"]
                or not isinstance(operation.get("owner_pid"), int)
                or isinstance(operation["owner_pid"], bool) or operation["owner_pid"] <= 0
                or not isinstance(operation.get("owner_host"), str) or not operation["owner_host"]
            ):
                raise ValueError("invalid operation owner")
            return operation
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise _ReviewFailure("needs_reconciliation", "启动记录损坏，请核对原任务") from exc

    def _operation_task(self, operation):
        task_id = operation["task_id"]
        try:
            directory = self.store.task_dir(task_id)
            if not directory.exists():
                if operation["stage"] != "reserved":
                    raise _ReviewFailure("needs_reconciliation", "启动记录存在，但原任务缺失")
                return None
            self._check_path(directory, directory=True)
            self._check_path(directory / "meta.json")
            record = self.store.get(task_id)
            ref = operation["target"]
            creation = record.extra.get("creation")
            if (record.task_id != task_id or not isinstance(creation, dict)
                    or not _valid_ref(creation.get("target"))
                    or creation != {"key": operation["key"], "target": ref}
                    or record.session_id != ref["session_id"] or record.plan_id != ref["plan_id"]
                    or record.plan_version != ref["plan_version"] or record.parent_id != ref["plan_task_id"]
                    or record.kind != "run" or record.mode != "act"):
                raise _ReviewFailure("needs_reconciliation", "原任务与启动凭据不一致")
            return record
        except TaskError as exc:
            raise _ReviewFailure("needs_reconciliation", "原任务无法读取，请核对已有证据") from exc

    def _check_unrecorded_execution(self, ref) -> None:
        for directory in self.store.tasks_root.iterdir():
            if not TASK_ID_RE.fullmatch(directory.name):
                continue
            self._check_path(directory, directory=True)
            path = directory / "meta.json"
            if not path.exists() and not path.is_symlink() and (directory / "plan").is_dir():
                # 兼容仅有 Plan 产物、尚未采用 TaskStore 的旧任务。
                continue
            try:
                self._check_path(path)
                record = self.store.get(directory.name)
                if record.task_id != directory.name or record.session_id != self.session_id:
                    raise TaskError("task identity mismatch")
            except (TaskError, _ReviewFailure) as exc:
                raise _ReviewFailure("needs_reconciliation", "已有任务证据不完整，请先核对") from exc
            if (record.mode in {"act", "goal"} and record.plan_id == ref["plan_id"]
                    and record.plan_version in (None, ref["plan_version"])):
                raise _ReviewFailure("needs_reconciliation", "已有绑定计划的执行任务，请核对原任务")

    def _existing_launch(self, operation) -> dict[str, Any] | None:
        record = self._operation_task(operation)
        if record is None or not record.segments:
            if operation["stage"] == "started":
                raise _ReviewFailure("needs_reconciliation", "启动记录与运行段不一致")
            return None
        first = record.segments[0]
        if first.run_id != operation["run_id"]:
            raise _ReviewFailure("needs_reconciliation", "原任务已有其它运行，不能重复启动")
        return {"task_id": record.task_id, "run_id": first.run_id, "segment": first.segment,
                "status": record.status, "run_started": False}

    def decide_review(self, ref, decision: str, request_id: str, *, busy: bool = False) -> ReviewDecision:
        if (not _valid_ref(ref) or decision not in ("approve-execute", "approve-only", "dismiss")
                or not isinstance(request_id, str) or not request_id or len(request_id) > 128
                or any(ord(char) < 32 for char in request_id)):
            return ReviewDecision(self._unavailable(None, "invalid_request", "审阅身份格式无效"))
        try:
            return self._decide_review(ref, decision, request_id, busy=busy)
        except _ReviewFailure as exc:
            return self._decision_failure(ref, exc.code, str(exc))
        except PlanError as exc:
            return self._decision_failure(ref, exc.code, str(exc))
        except (OSError, TaskError, TaskRuntimeError, UnicodeError, RecursionError):
            return self._decision_failure(ref, "storage_error", "计划操作未完成，请查询原对象")

    def _decision_failure(self, ref, code: str, message: str) -> ReviewDecision:
        snapshot = self.read_review(ref)
        if snapshot["outcome"] in {"launched", "approved_not_started"}:
            snapshot.update(code=code, message=message)
            return ReviewDecision(snapshot)
        return ReviewDecision(self._unavailable(ref, code, message))

    def _decision_snapshot(self, ref):
        snapshot = self.read_review(ref)
        if snapshot["code"] == "needs_reconciliation":
            operation = self._load_operation(ref)
            if (operation is not None and operation["stage"] == "starting"
                    and self._existing_launch(operation) is None):
                return self._read_review(ref, 0)
        return snapshot

    def _recover_preparation(self, operation):
        if operation["stage"] != "starting":
            return
        dead = False
        if operation["owner_host"] == socket.gethostname():
            try:
                os.kill(operation["owner_pid"], 0)
            except ProcessLookupError:
                dead = True
            except (OSError, OverflowError):
                pass
        if not dead and operation.get("prepare_failed") is not True:
            raise _ReviewFailure("needs_reconciliation", "启动者仍存活或无法确认，不能重复准备")
        try:
            self.store.recover_preparation(operation["task_id"])
        except TaskError as exc:
            raise _ReviewFailure("needs_reconciliation", "原任务仍被占用或状态需核对") from exc
        operation.update(stage="reserved", run_id=None, owner_pid=None, owner_host=None,
                         prepare_failed=False)
        self._save_operation(operation)

    def _decide_review(self, ref, decision: str, request_id: str, *, busy: bool) -> ReviewDecision:
        snapshot = self._decision_snapshot(ref)
        if snapshot["outcome"] not in {"ready", "approved_not_started"}:
            return ReviewDecision(snapshot)
        task_dir = self.session_root / "tasks" / ref["plan_task_id"]
        with plan_lock(task_dir, require_process_lock=True):
            snapshot = self._decision_snapshot(ref)
            if snapshot["outcome"] not in {"ready", "approved_not_started"}:
                return ReviewDecision(snapshot)
            plan = self._read_plan(plan_path(task_dir, ref["plan_version"]))
            if self._target(plan) != ref:
                return ReviewDecision(self._unavailable(ref, "content_changed", "计划内容已变化，请重新审阅"))
            if busy:
                return ReviewDecision(self._unavailable(ref, "busy", "当前运行尚未结束"))
            if decision != "dismiss":
                from app.runtime.plan_revision import PlanRevision
                PlanRevision(self.session_root, session_id=self.session_id,
                             task_runtime=self.task_runtime).guard_review(ref)
            if decision == "dismiss":
                dismiss(task_dir, plan, by="user")
                snapshot.update(outcome="dismissed", message="已关闭审阅，计划状态未改变")
                return ReviewDecision(snapshot)
            if decision == "approve-execute":
                if self.task_runtime is None or not self.task_runtime.enabled:
                    return ReviewDecision(self._unavailable(ref, "tasks_disabled", "任务运行时未启用"))
                if (self.task_runtime.store.root.resolve() != self.session_root.resolve()
                        or self.task_runtime.store.root.name != self.session_id):
                    return ReviewDecision(self._unavailable(ref, "target_mismatch", "任务运行时不属于当前会话"))
                if plan.goal_id is not None and (
                    not isinstance(plan.goal_id, str) or not plan.goal_id.strip()
                    or len(plan.goal_id.strip()) > 128
                ):
                    return ReviewDecision(self._unavailable(ref, "invalid_plan", "计划的目标绑定无效"))
            if plan.status == "approved":
                if not can_execute(plan):
                    return ReviewDecision(self._unavailable(ref, "plan_stale", "计划批准已失效，请重新审阅"))
            elif plan.status == "proposed":
                check_plan_review_window(task_dir, plan)
            elif plan.status != "draft":
                return ReviewDecision(self._unavailable(ref, "plan_stale", "当前计划状态不能批准"))
            if decision == "approve-only":
                if plan.status == "draft":
                    propose_plan(task_dir, plan, requester="user")
                if plan.status != "approved":
                    approve_plan(task_dir, plan, approved_by="user", approval_id=request_id)
                snapshot = self.read_review(ref)
                snapshot.update(outcome="approved", message="计划已批准，尚未启动")
                return ReviewDecision(snapshot)
            operation = self._load_operation(ref)
            if operation is None:
                operation = {
                    "schema_version": 1, "target": dict(ref), "key": self._launch_key(ref),
                    "task_id": self.store.reserve_task_id(), "stage": "reserved",
                    "run_id": None, "owner_pid": None, "owner_host": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                self._save_operation(operation)
            self._recover_preparation(operation)
            if plan.status == "draft":
                propose_plan(task_dir, plan, requester="user")
            if plan.status != "approved":
                approve_plan(task_dir, plan, approved_by="user", approval_id=operation["key"])
            task = self._operation_task(operation)
            if task is None:
                task = self.store.create(
                    kind="run", mode="act", objective=plan.objective,
                    workspace=self.task_runtime.workspace, plan_id=plan.plan_id,
                    plan_version=plan.version, parent_id=plan.task_id, goal_id=plan.goal_id,
                    reserved_id=operation["task_id"], creation={"key": operation["key"], "target": dict(ref)},
                )
            if (task.status not in {"created", "interrupted"} or task.segments or task.objective != plan.objective
                    or Path(task.workspace).resolve() != Path(self.task_runtime.workspace).resolve()):
                raise _ReviewFailure("needs_reconciliation", "原任务状态或执行范围不一致")
            operation.update(stage="starting", run_id=request_id, owner_pid=os.getpid(),
                             owner_host=socket.gethostname(), prepare_failed=False)
            self._save_operation(operation)
            try:
                run_task = self.task_runtime.prepare_run(request_id, {
                    "kind": "run", "mode": "act", "task_id": task.task_id,
                    "plan_id": plan.plan_id, "plan_version": plan.version,
                })
            except (OSError, TaskError, TaskRuntimeError):
                if self._existing_launch(operation) is None:
                    operation["prepare_failed"] = True
                    self._save_operation(operation)
                raise
            operation["stage"] = "started"
            receipt_pending = False
            try:
                self._save_operation(operation)
            except (OSError, _ReviewFailure):
                # 运行段已落盘；保留唯一交接，重查由 TaskStore 还原。
                receipt_pending = True
            snapshot = self.read_review(ref)
            snapshot.update(outcome="launched", message="已建立计划运行", launch={
                "task_id": task.task_id, "run_id": request_id, "segment": run_task.segment,
                "status": "running", "run_started": True,
            })
            if receipt_pending:
                snapshot.update(code="storage_error", message="运行段已建立，启动回执待恢复")
            return ReviewDecision(snapshot, run_task)

    def read_review(self, ref: dict[str, Any] | None = None, cursor: int = 0) -> dict[str, Any]:
        try:
            snapshot = self._read_review(ref, cursor)
            if ref is None:
                target = snapshot["target"]
            else:
                target = ref if _valid_ref(ref) and ref["session_id"] == self.session_id else None
            if snapshot["code"] == "invalid_request":
                target = None
            if target is not None:
                operation = self._load_operation(target)
                if operation is None and snapshot["outcome"] == "ready":
                    self._check_unrecorded_execution(target)
                elif operation is not None:
                    launch = self._existing_launch(operation)
                    if launch is not None:
                        snapshot.update(outcome="launched", launch=launch, message="已有计划运行，未重复启动")
                    elif operation["stage"] == "starting" and not operation.get("prepare_failed"):
                        raise _ReviewFailure("needs_reconciliation", "正在启动或结果未确认，请查询原任务")
                    elif snapshot["outcome"] == "ready" and snapshot["summary"]["status"] == "approved":
                        task = self._operation_task(operation)
                        snapshot.update(outcome="approved_not_started", message="已批准，尚未启动", launch={
                            "task_id": operation["task_id"], "run_id": None, "segment": 0,
                            "status": task.status if task is not None else "reserved", "run_started": False,
                        })
            return snapshot
        except _ReviewFailure as exc:
            return self._unavailable(ref if _valid_ref(ref) else None, exc.code, str(exc))
        except (PlanError, OSError, RecursionError, UnicodeError):
            return self._unavailable(ref if _valid_ref(ref) else None, "storage_error",
                                     "计划文件无法读取，请核对产物")

    def _check_path(self, path: Path, *, directory: bool = False) -> None:
        current = self.session_root
        parts = path.relative_to(self.session_root).parts
        for index in range(len(parts) + 1):
            if index:
                current = current / parts[index - 1]
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError as exc:
                raise _ReviewFailure("not_found", "找不到计划产物") from exc
            expected_dir = index < len(parts) or directory
            valid = stat.S_ISDIR(mode) if expected_dir else stat.S_ISREG(mode)
            if not valid:
                raise _ReviewFailure("needs_reconciliation", "计划路径不是普通文件或目录，请核对")

    def _read_plan(self, path: Path) -> Plan:
        self._check_path(path)
        plan = load_plan(path)
        match = _PLAN_FILE_RE.fullmatch(path.name)
        if (not match or plan.task_id != path.parent.parent.name
                or plan.version != int(match[1])):
            raise _ReviewFailure("target_mismatch", "计划身份与文件不一致")
        return plan

    def _newest_in_family(self, path: Path, plan: Plan) -> Plan:
        versions = [(int(match[1]), candidate) for candidate in path.parent.iterdir()
                    if (match := _PLAN_FILE_RE.fullmatch(candidate.name))]
        version, newest = max(versions, default=(plan.version, path))
        if version <= plan.version:
            return plan
        latest = self._read_plan(newest)
        if latest.plan_id != plan.plan_id:
            raise _ReviewFailure("target_mismatch", "同一计划目录的身份不一致")
        return latest

    def _discover(self) -> Plan:
        tasks = self.session_root / "tasks"
        self._check_path(tasks, directory=True)
        candidates: list[tuple[int, Path]] = []
        for task in tasks.iterdir():
            if not TASK_ID_RE.fullmatch(task.name):
                continue
            self._check_path(task, directory=True)
            directory = task / "plan"
            if not directory.exists() and not directory.is_symlink():
                continue
            self._check_path(directory, directory=True)
            for path in directory.iterdir():
                if _PLAN_FILE_RE.fullmatch(path.name):
                    self._check_path(path)
                    candidates.append((path.stat().st_mtime_ns, path))
        if not candidates:
            raise _ReviewFailure("not_found", "没有可审阅的计划")
        _, path = max(candidates)
        return self._newest_in_family(path, self._read_plan(path))

    def _read_review(self, ref, cursor: int) -> dict[str, Any]:
        if (not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0
                or (ref is None and cursor != 0)):
            return self._unavailable(ref if _valid_ref(ref) else None, "invalid_request",
                                     "正文分页需要准确身份和有效偏移")
        if ref is None:
            plan = self._discover()
        else:
            if not _valid_ref(ref):
                return self._unavailable(None, "invalid_request", "审阅身份格式无效")
            if ref["session_id"] != self.session_id:
                return self._unavailable(ref, "target_mismatch", "计划不属于当前会话")
            task_dir = self.session_root / "tasks" / ref["plan_task_id"]
            path = plan_path(task_dir, ref["plan_version"])
            plan = self._read_plan(path)
            if (plan.task_id != ref["plan_task_id"] or plan.plan_id != ref["plan_id"]
                    or plan.version != ref["plan_version"]):
                return self._unavailable(ref, "target_mismatch", "计划身份与文件不一致")
            latest = self._newest_in_family(path, plan)
            if latest.version > plan.version:
                return self._unavailable(ref, "superseded", "已有新版本，请重新审阅",
                                         self._target(latest))
            if content_hash(plan) != ref["plan_hash"]:
                return self._unavailable(ref, "content_changed", "计划内容已变化，请重新审阅",
                                         self._target(plan))
        body = render_markdown(plan, include_review_state=False)
        if cursor >= len(body):
            return self._unavailable(self._target(plan), "invalid_request", "正文偏移超出范围")
        page = body[cursor:cursor + 16000]
        next_cursor = cursor + len(page)
        return {
            "outcome": "ready", "code": None, "target": self._target(plan),
            "replacement_target": None,
            "message": "计划已读取", "launch": None,
            "summary": {"objective": plan.objective, "status": plan.status,
                        "executable": can_execute(plan),
                        "step_count": len(plan.steps),
                        "criterion_count": len(plan.acceptance_criteria),
                        "question_count": len(plan.open_questions)},
            "body_page": page, "next_cursor": next_cursor if next_cursor < len(body) else None,
            "available_actions": [],
        }


def review_intent(plan: Plan) -> dict[str, Any]:
    """审阅请求的稳定身份，供 UI/审计重开同一份计划。"""
    return {
        "kind": "plan-review",
        "plan_id": plan.plan_id,
        "plan_task_id": plan.task_id,
        "plan_version": plan.version,
        "plan_hash": content_hash(plan),
        "status": plan.status,
    }


def _plan_candidates(session_root: str | Path) -> list[tuple[Path, Plan]]:
    tasks_root = Path(session_root) / "tasks"
    if not tasks_root.is_dir():
        return []
    candidates: list[tuple[float, Path, Plan]] = []
    for path in tasks_root.glob("*/plan/plan.v*.json"):
        try:
            item = load_plan(path)
        except PlanError:
            continue
        candidates.append((path.stat().st_mtime, path, item))
    candidates.sort(key=lambda row: (row[0], row[2].version), reverse=True)
    return [(path, item) for _, path, item in candidates]


def latest_plan(session_root: str | Path) -> tuple[Path, Plan] | None:
    candidates = _plan_candidates(session_root)
    return candidates[0] if candidates else None


def executable_plan(session_root: str | Path) -> tuple[Path, Plan] | None:
    from app.runtime.plan import can_execute

    for path, plan in _plan_candidates(session_root):
        if can_execute(plan):
            return path, plan
    return None


def render_markdown(plan: Plan, *, include_review_state: bool = True) -> str:
    """把结构化 Plan 渲成可审阅的 Markdown 稿（JSON 仍是机器契约）。"""
    approval = plan.approval if isinstance(plan.approval, dict) else {}
    lines = [
        f"# {plan.objective}",
        "",
        f"- 计划：`{plan.plan_id}` · v{plan.version}"
        + (f" · 状态 {plan.status}" if include_review_state else ""),
        f"- 审阅身份：`{review_intent(plan)['plan_hash'][:16]}`",
    ]
    if include_review_state and approval.get("approved_by"):
        lines.append(
            f"- 批准：{approval.get('approved_by')} · {approval.get('approved_at') or ''}"
            + (" · stale" if approval.get("stale") else "")
        )
    lines += ["", "## 目标与成功标准"]
    criteria = [item for item in plan.acceptance_criteria]
    if not criteria:
        lines.append("- （未提供验收标准）")
    for item in criteria:
        marked = "已确认" if item.get("confirmed") is True else "候选"
        required = "required" if item.get("required", True) is not False else "optional"
        lines.append(f"- [{marked}] `{item.get('id', '?')}` {item.get('text', '')}（{required}）")
    lines += ["", "## 实施步骤"]
    if not plan.steps:
        lines.append("- （无步骤）")
    for step in plan.steps:
        lines.append(f"### {step.step_id}. {step.title}")
        detail = [f"kind={step.kind}", f"risk={step.risk}"]
        if step.depends_on:
            detail.append("depends_on=" + ",".join(step.depends_on))
        if step.files:
            detail.append("files=" + ", ".join(step.files))
        lines.append("- " + " · ".join(detail))
        if step.acceptance:
            lines.append(f"- 步骤验收：{step.acceptance}")
        if step.verification:
            verification = json.dumps(step.verification, sort_keys=True, ensure_ascii=False)
            lines.append(f"- 步骤验证：`{verification}`")
    lines += ["", "## 验证命令"]
    if not plan.verification_plan:
        lines.append("- （未提供可信验证命令）")
    for item in plan.verification_plan:
        covers = ",".join(item.get("covers") or [])
        lines.append(
            f"- `{item.get('id', '?')}`（covers={covers or '-'}，source={item.get('source', '?')}）"
            f" `{item.get('command', '')}`"
        )
    lines += ["", "## 约束、范围与待决定"]
    for label, values in (
        ("约束", plan.constraints),
        ("范围外", plan.out_of_scope),
        ("待决定", plan.open_questions),
    ):
        lines.append(f"### {label}")
        if not values:
            lines.append("- （无）")
        for value in values:
            lines.append(f"- {value}")
    lines += ["", "## 审阅反馈"]
    if not plan.review_feedback:
        lines.append("- （暂无）")
    for item in plan.review_feedback:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def request_changes(
    task_dir: str | Path, plan: Plan, feedback: str, *, requester: str = "user",
) -> Plan:
    """把“要求修改”的反馈折成 version+1 draft；审批失效，等待重新批准。"""
    with plan_lock(task_dir):
        text = (feedback or "").strip()
        if not text:
            raise PlanError("request changes requires nonempty feedback")
        if plan.status not in {"proposed", "approved", "draft"}:
            raise PlanError(f"cannot request changes in plan status {plan.status}")
        revised = revise_plan(task_dir, plan, feedback=text, requester=requester)
        audit_event(
            task_dir, "plan_changes_requested",
            task_id=plan.task_id, plan_id=plan.plan_id,
            previous_version=plan.version, version=revised.version,
            requester=requester, intent=review_intent(plan),
        )
        return revised


def dismiss(
    task_dir: str | Path, plan: Plan, *, by: str = "user", note: str = "",
) -> None:
    """关闭审阅但不改计划状态；计划保持 proposed/draft，显式批准仍需另行发生。"""
    with plan_lock(task_dir):
        validate_plan_snapshot(task_dir, plan)
        audit_event(
            task_dir, "plan_review_dismissed",
            task_id=plan.task_id, plan_id=plan.plan_id, version=plan.version,
            dismissed_by=by, note=(note or "")[:1000], intent=review_intent(plan),
        )
