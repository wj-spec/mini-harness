"""任务运行时基础：TaskRecord + TaskStore。

实现 spec：docs/specs/task-runtime.md。只负责持久化任务身份/生命周期，
不执行工具、不审批、不碰工作区内容。任务目录位于 session 目录下：
``<session>/tasks/<task_id>/{meta.json,events.jsonl,lock}``。
"""

from __future__ import annotations

import errno
import json
import os
import re
import secrets
import shutil
import socket
import stat
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 1

TASK_KINDS = ("goal", "run", "plan", "subagent")
TASK_MODES = ("goal", "act", "plan", "chat")
TASK_STATUSES = (
    "created", "queued", "running", "paused", "completed", "failed",
    "cancelled", "interrupted",
)
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
SEGMENT_STATUSES = ("running", "stopped", "done", "failed", "cancelled", "interrupted")
SEGMENT_END_STATUSES = frozenset({"stopped", "done", "failed", "cancelled", "interrupted"})
AUTONOMY_LEVELS = ("ask", "edits", "workspace", "full")

TASK_ID_RE = re.compile(r"^t-\d{8}-\d{6}-[0-9a-f]{6}$")

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"running", "queued", "cancelled"}),
    "queued": frozenset({"running", "failed", "cancelled"}),
    "running": frozenset({"paused", "completed", "failed", "cancelled", "interrupted"}),
    "paused": frozenset({"running", "cancelled", "failed"}),
    "interrupted": frozenset({"running", "paused", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

TRANSITION_EVENTS = {
    "queued": "task_queued",
    "paused": "task_paused",
    "completed": "task_completed",
    "failed": "task_failed",
    "cancelled": "task_cancelled",
    "interrupted": "task_interrupted",
}

BUDGET_DEFAULTS = {"max_run_tokens": 0, "max_goal_tokens": 0, "max_wall_seconds": 0}
APPROVAL_DEFAULTS = {"autonomy": "ask", "scope": "own", "capabilities": []}


class TaskError(Exception):
    """TaskStore 的基础异常。"""


class TaskNotFound(TaskError):
    pass


class TaskCorrupt(TaskError):
    pass


class TaskBusy(TaskError):
    pass


class InvalidTaskTransition(TaskError):
    pass


class TaskIOError(TaskError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _new_task_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"t-{stamp}-{secrets.token_hex(3)}"


def _require_text(value: Any, name: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    result = value.strip()
    if max_length is not None and len(result) > max_length:
        raise ValueError(f"{name} exceeds {max_length} characters")
    return result


def _validate_task_id(task_id: Any) -> str:
    if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
        raise ValueError("invalid task_id")
    return task_id


def _validate_workspace(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("workspace must be a nonempty path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("workspace must be an absolute path")
    return str(path.resolve())


def _validate_budget(value: Any) -> dict[str, int]:
    if value is None:
        return dict(BUDGET_DEFAULTS)
    if not isinstance(value, dict):
        raise ValueError("budget must be an object")
    unknown = set(value) - set(BUDGET_DEFAULTS)
    if unknown:
        raise ValueError(f"unknown budget fields: {sorted(unknown)}")
    result = dict(BUDGET_DEFAULTS)
    for key, raw in value.items():
        if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
            raise ValueError(f"budget.{key} must be a non-negative integer")
        result[key] = raw
    return result


def _validate_approval_policy(value: Any) -> dict[str, Any]:
    if value is None:
        return {**APPROVAL_DEFAULTS, "capabilities": []}
    if not isinstance(value, dict):
        raise ValueError("approval_policy must be an object")
    unknown = set(value) - set(APPROVAL_DEFAULTS)
    if unknown:
        raise ValueError(f"unknown approval_policy fields: {sorted(unknown)}")
    result = {**APPROVAL_DEFAULTS, "capabilities": []}
    if "autonomy" in value:
        if value["autonomy"] not in AUTONOMY_LEVELS:
            raise ValueError("unknown autonomy level")
        result["autonomy"] = value["autonomy"]
    if "scope" in value:
        result["scope"] = _require_text(value["scope"], "approval_policy.scope", max_length=64)
    if "capabilities" in value:
        capabilities = value["capabilities"]
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) and item for item in capabilities
        ):
            raise ValueError("approval_policy.capabilities must be a list of strings")
        result["capabilities"] = list(dict.fromkeys(capabilities))
    return result


@dataclass
class SegmentRecord:
    segment: int
    run_id: str
    status: str
    stop_reason: str | None = None
    round_ref: str | None = None
    started_at: str = ""
    ended_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment": self.segment,
            "run_id": self.run_id,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "round_ref": self.round_ref,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "SegmentRecord":
        if not isinstance(data, dict):
            raise ValueError("segment must be an object")
        segment = data.get("segment")
        if not isinstance(segment, int) or isinstance(segment, bool) or segment < 1:
            raise ValueError("segment number must be a positive integer")
        run_id = _require_text(data.get("run_id"), "segment.run_id", max_length=128)
        status = data.get("status")
        if status not in SEGMENT_STATUSES:
            raise ValueError("invalid segment status")
        stop_reason = data.get("stop_reason")
        if stop_reason is not None and not isinstance(stop_reason, str):
            raise ValueError("segment.stop_reason must be a string or null")
        round_ref = data.get("round_ref")
        if round_ref is not None and not isinstance(round_ref, str):
            raise ValueError("segment.round_ref must be a string or null")
        started_at = data.get("started_at")
        if not isinstance(started_at, str) or not started_at:
            raise ValueError("segment.started_at must be a string")
        ended_at = data.get("ended_at")
        if ended_at is not None and not isinstance(ended_at, str):
            raise ValueError("segment.ended_at must be a string or null")
        return cls(
            segment=segment, run_id=run_id, status=status, stop_reason=stop_reason,
            round_ref=round_ref, started_at=started_at, ended_at=ended_at,
        )


@dataclass
class TaskRecord:
    schema_version: int
    task_id: str
    session_id: str
    kind: str
    status: str
    objective: str
    mode: str
    workspace: str
    parent_id: str | None = None
    goal_id: str | None = None
    plan_id: str | None = None
    plan_version: int | None = None
    priority: int = 0
    budget: dict[str, int] = field(default_factory=lambda: dict(BUDGET_DEFAULTS))
    approval_policy: dict[str, Any] = field(
        default_factory=lambda: {**APPROVAL_DEFAULTS, "capabilities": []}
    )
    segments: list[SegmentRecord] = field(default_factory=list)
    result_ref: str | None = None
    created_at: str = ""
    updated_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "kind": self.kind,
            "status": self.status,
            "objective": self.objective,
            "mode": self.mode,
            "parent_id": self.parent_id,
            "goal_id": self.goal_id,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "priority": self.priority,
            "workspace": self.workspace,
            "budget": dict(self.budget),
            "approval_policy": {
                **self.approval_policy,
                "capabilities": list(self.approval_policy.get("capabilities", [])),
            },
            "segments": [segment.to_dict() for segment in self.segments],
            "result_ref": self.result_ref,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        for key, value in self.extra.items():
            if key not in payload:
                payload[key] = value
        return payload

    @classmethod
    def from_dict(cls, data: Any) -> "TaskRecord":
        if not isinstance(data, dict):
            raise ValueError("task meta must be an object")
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported task schema_version")
        known = {
            "schema_version", "task_id", "session_id", "kind", "status", "objective",
            "mode", "parent_id", "goal_id", "plan_id", "plan_version", "priority",
            "workspace", "budget", "approval_policy",
            "segments", "result_ref", "created_at", "updated_at",
        }
        task_id = _validate_task_id(data.get("task_id"))
        session_id = _require_text(data.get("session_id"), "session_id", max_length=128)
        kind = data.get("kind")
        if kind not in TASK_KINDS:
            raise ValueError("invalid task kind")
        status = data.get("status")
        if status not in TASK_STATUSES:
            raise ValueError("invalid task status")
        objective = _require_text(data.get("objective"), "objective", max_length=4000)
        mode = data.get("mode")
        if mode not in TASK_MODES:
            raise ValueError("invalid task mode")
        workspace = _validate_workspace(data.get("workspace"))
        parent_id = data.get("parent_id")
        if parent_id is not None:
            parent_id = _require_text(parent_id, "parent_id", max_length=128)
        goal_id = data.get("goal_id")
        if goal_id is not None:
            goal_id = _require_text(goal_id, "goal_id", max_length=128)
        plan_id = data.get("plan_id")
        if plan_id is not None:
            plan_id = _require_text(plan_id, "plan_id", max_length=128)
        plan_version = data.get("plan_version")
        if plan_version is not None and (not isinstance(plan_version, int) or isinstance(plan_version, bool) or plan_version < 1):
            raise ValueError("invalid plan_version")
        priority = data.get("priority", 0)
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 0 or priority > 100:
            raise ValueError("invalid priority")
        segments_raw = data.get("segments", [])
        if not isinstance(segments_raw, list):
            raise ValueError("segments must be a list")
        segments = [SegmentRecord.from_dict(item) for item in segments_raw]
        created_at = _require_text(data.get("created_at"), "created_at", max_length=64)
        updated_at = _require_text(data.get("updated_at"), "updated_at", max_length=64)
        result_ref = data.get("result_ref")
        if result_ref is not None:
            result_ref = _require_text(result_ref, "result_ref", max_length=512)
        return cls(
            schema_version=SCHEMA_VERSION,
            task_id=task_id,
            session_id=session_id,
            kind=kind,
            status=status,
            objective=objective,
            mode=mode,
            workspace=workspace,
            parent_id=parent_id,
            goal_id=goal_id,
            plan_id=plan_id,
            plan_version=plan_version,
            priority=priority,
            budget=_validate_budget(data.get("budget")),
            approval_policy=_validate_approval_policy(data.get("approval_policy")),
            segments=segments,
            result_ref=result_ref,
            created_at=created_at,
            updated_at=updated_at,
            extra={key: value for key, value in data.items() if key not in known},
        )


class TaskStore:
    """持久化 TaskRecord/TaskEvent；一个 session 根对应一个 store。"""

    def __init__(self, session_root: str | Path):
        self.root = Path(session_root)
        self.tasks_root = self.root / "tasks"
        self.last_list_skipped = 0

    def task_dir(self, task_id: str) -> Path:
        _validate_task_id(task_id)
        directory = self.tasks_root / task_id
        if directory.is_symlink():
            raise TaskCorrupt("task directory is a symlink")
        return directory

    @staticmethod
    def reserve_task_id() -> str:
        """生成候选身份；调用者必须先持久化凭据再创建任务。"""
        return _new_task_id()

    def create(
        self,
        *,
        kind: str,
        objective: str,
        workspace: str,
        mode: str = "act",
        budget: dict[str, int] | None = None,
        approval_policy: dict[str, Any] | None = None,
        parent_id: str | None = None,
        goal_id: str | None = None,
        plan_id: str | None = None,
        plan_version: int | None = None,
        priority: int = 0,
        session_id: str | None = None,
        reserved_id: str | None = None,
        creation: dict[str, Any] | None = None,
    ) -> TaskRecord:
        if kind not in TASK_KINDS:
            raise ValueError("invalid task kind")
        if mode not in TASK_MODES:
            raise ValueError("invalid task mode")
        objective = _require_text(objective, "objective", max_length=4000)
        workspace = _validate_workspace(workspace)
        budget = _validate_budget(budget)
        approval_policy = _validate_approval_policy(approval_policy)
        parent_id = None if parent_id is None else _require_text(
            parent_id, "parent_id", max_length=128,
        )
        goal_id = None if goal_id is None else _require_text(goal_id, "goal_id", max_length=128)
        plan_id = None if plan_id is None else _require_text(plan_id, "plan_id", max_length=128)
        if plan_version is not None and (not isinstance(plan_version, int) or isinstance(plan_version, bool) or plan_version < 1):
            raise ValueError("invalid plan_version")
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 0 or priority > 100:
            raise ValueError("priority must be an integer between 0 and 100")

        if (reserved_id is None) != (creation is None):
            raise ValueError("reserved_id and creation are required together")
        if reserved_id is not None:
            _validate_task_id(reserved_id)
            if (not isinstance(creation, dict) or set(creation) != {"key", "target"}
                    or not isinstance(creation.get("key"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", creation["key"])
                    or not isinstance(creation.get("target"), dict)):
                raise ValueError("invalid task creation credential")
            creation = json.loads(json.dumps(creation, ensure_ascii=False))

        self.tasks_root.mkdir(parents=True, exist_ok=True)
        for _ in range(8):
            task_id = reserved_id or _new_task_id()
            directory = self.task_dir(task_id)
            try:
                directory.mkdir()
            except FileExistsError:
                if reserved_id is not None:
                    raise TaskCorrupt("reserved task directory already exists")
                continue
            break
        else:  # pragma: no cover - 极小概率
            raise TaskIOError("could not allocate a unique task id")

        now = _now()
        record = TaskRecord(
            schema_version=SCHEMA_VERSION,
            task_id=task_id,
            session_id=session_id or self.root.name or "session",
            kind=kind,
            status="created",
            objective=objective,
            mode=mode,
            workspace=workspace,
            parent_id=parent_id,
            goal_id=goal_id,
            plan_id=plan_id,
            plan_version=plan_version,
            priority=priority,
            budget=budget,
            approval_policy=approval_policy,
            created_at=now,
            updated_at=now,
            extra={"creation": creation} if creation is not None else {},
        )
        try:
            self._write_meta(record)
            self.append_event(task_id, "task_created", data={"kind": kind, "mode": mode})
        except Exception:
            # 普通创建清理半成品；预留创建保留恢复证据。
            if reserved_id is None:
                shutil.rmtree(directory, ignore_errors=True)
            raise
        return record

    def set_priority(self, task_id: str, priority: int) -> TaskRecord:
        """调整尚未开始的 created 任务优先级；运行中/已结束任务不允许改。"""
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 0 or priority > 100:
            raise ValueError("priority must be an integer between 0 and 100")
        record = self.get(task_id)
        if record.status != "created":
            raise ValueError(f"only created tasks can be reprioritized (status={record.status})")
        updated = replace(record, priority=priority, updated_at=_now())
        self._write_meta(updated)
        self.append_event(task_id, "task_priority_set", data={"priority": priority})
        return updated

    def get(self, task_id: str) -> TaskRecord:
        directory = self.task_dir(task_id)
        return self._read_meta(directory)

    def list(
        self, *, status: str | None = None, kind: str | None = None,
    ) -> list[TaskRecord]:
        self.last_list_skipped = 0
        if status is not None and status not in TASK_STATUSES:
            raise ValueError("invalid task status filter")
        if kind is not None and kind not in TASK_KINDS:
            raise ValueError("invalid task kind filter")
        if not self.tasks_root.is_dir():
            return []
        records: list[TaskRecord] = []
        for entry in sorted(self.tasks_root.iterdir()):
            if entry.is_symlink() or not entry.is_dir():
                self.last_list_skipped += 1
                continue
            if not TASK_ID_RE.fullmatch(entry.name):
                self.last_list_skipped += 1
                continue
            try:
                record = self._read_meta(entry)
            except (TaskCorrupt, TaskNotFound, OSError):
                self.last_list_skipped += 1
                continue
            if status is not None and record.status != status:
                continue
            if kind is not None and record.kind != kind:
                continue
            records.append(record)
        return sorted(records, key=lambda record: record.created_at)

    def transition(
        self,
        task_id: str,
        target: str,
        *,
        reason: str | None = None,
        data: dict[str, Any] | None = None,
        actor: str = "harness",
    ) -> TaskRecord:
        if target not in TASK_STATUSES:
            raise ValueError("invalid task status")
        record = self.get(task_id)
        if target == record.status:
            return record
        if target not in ALLOWED_TRANSITIONS[record.status]:
            raise InvalidTaskTransition(
                f"cannot transition {record.status} -> {target} for {task_id}"
            )
        previous = record.status
        record = replace(record, status=target, updated_at=_now())
        self._write_meta(record)
        event = TRANSITION_EVENTS.get(target)
        if event is None:
            event = "task_resumed" if previous in {"paused", "interrupted"} else "task_started"
        payload = {"from": previous, "to": target, "reason": reason}
        if data:
            payload.update(data)
        self.append_event(task_id, event, data=payload, actor=actor)
        return record

    def append_event(
        self,
        task_id: str,
        event: str,
        *,
        data: dict[str, Any] | None = None,
        actor: str = "harness",
    ) -> None:
        task_id = _validate_task_id(task_id)
        # 只为已存在的任务追加事件，避免拼写/越权创建孤儿目录
        self.get(task_id)
        if not isinstance(event, str) or not event:
            raise ValueError("event must be a nonempty string")
        if actor not in {"user", "harness", "worker", "tui"}:
            raise ValueError("invalid event actor")
        directory = self.task_dir(task_id)
        events = self.read_events(task_id)
        sequence = max((item.get("seq", 0) for item in events), default=0) + 1
        payload = {
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "seq": sequence,
            "ts": _now(),
            "event": event,
            "actor": actor,
            "data": dict(data or {}),
        }
        try:
            with (directory / "events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
                handle.flush()
        except OSError as exc:
            raise TaskIOError(str(exc)) from exc

    def read_events(self, task_id: str) -> list[dict[str, Any]]:
        path = self.task_dir(task_id) / "events.jsonl"
        if not path.is_file() or path.is_symlink():
            return []
        events: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(value, dict)
                and isinstance(value.get("seq"), int)
                and isinstance(value.get("event"), str)
            ):
                events.append(value)
        return sorted(events, key=lambda item: item["seq"])

    def start_segment(self, task_id: str, *, run_id: str) -> SegmentRecord:
        run_id = _require_text(run_id, "run_id", max_length=128)
        record = self.get(task_id)
        if record.status not in {"created", "running", "paused"}:
            raise InvalidTaskTransition(
                f"cannot start a segment while task is {record.status}"
            )
        if record.status != "running":
            record = self.transition(
                task_id, "running", reason="segment_start", data={"run_id": run_id},
            )
        if any(segment.status == "running" for segment in record.segments):
            raise TaskBusy("task already has an active segment")
        number = max((segment.segment for segment in record.segments), default=0) + 1
        segment = SegmentRecord(
            segment=number, run_id=run_id, status="running", started_at=_now(),
        )
        record = replace(
            record, segments=[*record.segments, segment], updated_at=_now(),
        )
        self._write_meta(record)
        self.append_event(
            task_id, "segment_started",
            data={"segment": number, "run_id": run_id},
        )
        return segment

    def end_segment(
        self,
        task_id: str,
        *,
        run_id: str,
        status: str,
        stop_reason: str | None = None,
        round_ref: str | None = None,
    ) -> SegmentRecord:
        if status not in SEGMENT_END_STATUSES:
            raise ValueError("invalid segment end status")
        record = self.get(task_id)
        index = next(
            (
                position for position in range(len(record.segments) - 1, -1, -1)
                if record.segments[position].run_id == run_id
                and record.segments[position].status == "running"
            ),
            None,
        )
        if index is None:
            raise InvalidTaskTransition("run_id does not match an active segment")
        ended = replace(
            record.segments[index],
            status=status,
            stop_reason=stop_reason,
            round_ref=round_ref,
            ended_at=_now(),
        )
        segments = list(record.segments)
        segments[index] = ended
        record = replace(record, segments=segments, updated_at=_now())
        self._write_meta(record)
        self.append_event(
            task_id, "segment_ended",
            data={
                "segment": ended.segment, "run_id": run_id, "status": status,
                "stop_reason": stop_reason, "round_ref": round_ref,
            },
        )
        return ended

    def acquire_lock(self, task_id: str) -> None:
        self.get(task_id)  # 先校验任务存在，避免为空目录建锁
        directory = self.task_dir(task_id)
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "task_id": task_id,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "created_at": _now(),
        }
        try:
            descriptor = os.open(
                directory / "lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600,
            )
        except FileExistsError as exc:
            raise TaskBusy(f"task {task_id} is locked") from exc
        except OSError as exc:
            raise TaskIOError(str(exc)) from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def release_lock(self, task_id: str) -> None:
        path = self.task_dir(task_id) / "lock"
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise TaskIOError(str(exc)) from exc

    def recover_stale(self) -> list[TaskRecord]:
        recovered: list[TaskRecord] = []
        if not self.tasks_root.is_dir():
            return recovered
        for entry in sorted(self.tasks_root.iterdir()):
            if entry.is_symlink() or not entry.is_dir():
                continue
            if not TASK_ID_RE.fullmatch(entry.name):
                continue
            try:
                record = self._read_meta(entry)
            except (TaskCorrupt, TaskNotFound, OSError):
                continue
            if record.status != "running":
                continue
            stale, lock = self._lock_is_stale(entry)
            if not stale:
                continue
            self.release_lock(record.task_id)
            self.append_event(
                record.task_id, "lock_stale_recovered", data={"lock": lock},
            )
            for segment in record.segments:
                if segment.status == "running":
                    self.end_segment(record.task_id, run_id=segment.run_id,
                                     status="interrupted", stop_reason="stale_lock")
            recovered.append(
                self.transition(
                    record.task_id, "interrupted",
                    reason="stale_lock", data={"lock": lock},
                )
            )
        return recovered

    def recover_preparation(self, task_id: str, *, expected_segments: int = 0) -> TaskRecord:
        """恢复未落段的准备，保留已关闭历史；调用者须证明退出或准备失败。"""
        record = self.get(task_id)
        if (type(expected_segments) is not int or expected_segments < 0
                or len(record.segments) != expected_segments
                or any(segment.status == "running" for segment in record.segments)
                or (record.status == "paused" and expected_segments == 0)
                or record.status not in {"created", "running", "interrupted", "paused"}):
            raise TaskBusy("task has execution evidence or cannot recover preparation")
        path = self.task_dir(task_id) / "lock"
        if path.is_symlink():
            raise TaskCorrupt("task lock is a symlink")
        if path.exists():
            try:
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise TaskCorrupt("task lock is not a regular file")
                with path.open("rb") as handle:
                    raw = handle.read(4097)
                if len(raw) > 4096:
                    raise TaskCorrupt("task lock exceeds limit")
                payload = json.loads(raw)
            except (OSError, ValueError, RecursionError) as exc:
                raise TaskCorrupt("invalid task lock") from exc
            if (not isinstance(payload, dict) or payload.get("task_id") != task_id
                    or not isinstance(payload.get("pid"), int) or isinstance(payload["pid"], bool)
                    or payload["pid"] <= 0 or not isinstance(payload.get("host"), str)):
                raise TaskCorrupt("invalid task lock owner")
            if payload["host"] != socket.gethostname() or _pid_alive(payload["pid"]):
                raise TaskBusy("task preparation is still owned")
            self.release_lock(task_id)
        if record.status == "running":
            record = self.transition(task_id, "interrupted", reason="preparation_recovered")
        return record

    # ---- 内部工具 ----

    def _read_meta(self, directory: Path) -> TaskRecord:
        path = directory / "meta.json"
        if path.is_symlink():
            raise TaskCorrupt("meta.json is a symlink")
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise TaskNotFound(f"task meta not found: {directory.name}") from exc
        except OSError as exc:
            raise TaskCorrupt(str(exc)) from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TaskCorrupt(f"invalid task meta: {directory.name}") from exc
        try:
            return TaskRecord.from_dict(payload)
        except (ValueError, TypeError) as exc:
            raise TaskCorrupt(f"invalid task meta: {directory.name}: {exc}") from exc

    def _write_meta(self, record: TaskRecord) -> None:
        directory = self.task_dir(record.task_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "meta.json"
        initial = not target.exists()
        temporary = None
        try:
            fd, name = tempfile.mkstemp(prefix=".meta-", suffix=".tmp", dir=directory)
            temporary = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            if os.name == "posix":
                for parent in (directory, directory.parent) if initial else (directory,):
                    directory_fd = os.open(parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
        except OSError as exc:
            raise TaskIOError(str(exc)) from exc
        finally:
            try:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _lock_is_stale(self, directory: Path) -> tuple[bool, dict[str, Any] | None]:
        path = directory / "lock"
        if not path.exists() or path.is_symlink():
            return True, None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return True, {"reason": "invalid_lock"}
        if not isinstance(payload, dict):
            return True, {"reason": "invalid_lock"}
        if payload.get("host") != socket.gethostname():
            return False, payload
        if _pid_alive(payload.get("pid")):
            return False, payload
        return True, payload


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OverflowError, OSError) as exc:
        if isinstance(exc, OSError) and exc.errno in {errno.ESRCH, errno.EINVAL}:
            return False
        return True
    return True
