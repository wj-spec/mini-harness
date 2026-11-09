"""后台 worker 监督基础（spec: docs/specs/process-supervision.md）。"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime.tasks import TaskError, TaskRecord, TaskStore

WORKER_SCHEMA_VERSION = 1
WORKER_HEARTBEAT_TTL_SECONDS = 30.0
WORKER_STATUSES = (
    "starting", "running", "waiting_approval", "stopping",
    "exited", "failed", "killed", "interrupted",
)
ACTIVE_WORKER_STATUSES = frozenset({"starting", "running", "waiting_approval", "stopping"})


class WorkerStateError(ValueError):
    pass


@dataclass
class WorkerState:
    schema_version: int
    task_id: str
    pid: int
    host: str
    status: str
    started_at: str
    heartbeat_at: str
    exit_code: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "pid": self.pid,
            "host": self.host,
            "status": self.status,
            "started_at": self.started_at,
            "heartbeat_at": self.heartbeat_at,
            "exit_code": self.exit_code,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "WorkerState":
        if not isinstance(value, dict):
            raise WorkerStateError("worker state must be an object")
        if value.get("schema_version") != WORKER_SCHEMA_VERSION:
            raise WorkerStateError("unsupported worker schema_version")
        task_id = value.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise WorkerStateError("worker.task_id must be a nonempty string")
        pid = value.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise WorkerStateError("worker.pid must be a positive integer")
        host = value.get("host")
        if not isinstance(host, str) or not host:
            raise WorkerStateError("worker.host must be a nonempty string")
        status = value.get("status")
        if status not in WORKER_STATUSES:
            raise WorkerStateError("invalid worker.status")
        for key in ("started_at", "heartbeat_at"):
            if not isinstance(value.get(key), str) or not value[key]:
                raise WorkerStateError(f"worker.{key} must be a nonempty string")
        exit_code = value.get("exit_code")
        if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
            raise WorkerStateError("worker.exit_code must be an integer or null")
        error = value.get("error")
        if error is not None and not isinstance(error, str):
            raise WorkerStateError("worker.error must be a string or null")
        return cls(
            schema_version=WORKER_SCHEMA_VERSION, task_id=task_id, pid=pid, host=host,
            status=status, started_at=value["started_at"],
            heartbeat_at=value["heartbeat_at"], exit_code=exit_code, error=error,
        )


@dataclass
class WorkerSnapshot:
    task: TaskRecord
    worker: WorkerState | None
    stale: bool
    pid_alive: bool
    foreign_host: bool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def worker_state_path(task_dir: str | Path) -> Path:
    return Path(task_dir) / "worker.json"


def read_worker_state(task_dir: str | Path) -> WorkerState | None:
    path = worker_state_path(task_dir)
    if path.is_symlink():
        raise WorkerStateError("worker.json is a symlink")
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise WorkerStateError(f"worker.json unreadable: {exc}") from exc
    try:
        return WorkerState.from_dict(json.loads(raw))
    except json.JSONDecodeError as exc:
        raise WorkerStateError("worker.json is not valid JSON") from exc


def write_worker_state(task_dir: str | Path, state: WorkerState) -> None:
    directory = Path(task_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = worker_state_path(directory)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def heartbeat(task_dir: str | Path, *, now: str | None = None) -> WorkerState:
    state = read_worker_state(task_dir)
    if state is None:
        raise WorkerStateError("worker.json does not exist")
    state.heartbeat_at = now or _now()
    write_worker_state(task_dir, state)
    return state


def pid_alive(pid: int) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OverflowError, OSError):
        return False
    return True


def heartbeat_expired(
    state: WorkerState, *, ttl_seconds: float = WORKER_HEARTBEAT_TTL_SECONDS,
    now: datetime | None = None,
) -> bool:
    if ttl_seconds <= 0:
        return False
    current = now or datetime.now(timezone.utc)
    try:
        heartbeat_at = datetime.fromisoformat(state.heartbeat_at)
    except ValueError:
        return True
    if heartbeat_at.tzinfo is None:
        heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
    return (current - heartbeat_at).total_seconds() > ttl_seconds


def is_stale(
    state: WorkerState, *, ttl_seconds: float = WORKER_HEARTBEAT_TTL_SECONDS,
    now: datetime | None = None,
) -> bool:
    if state.host != socket.gethostname():
        return False
    return (not pid_alive(state.pid)) or heartbeat_expired(
        state, ttl_seconds=ttl_seconds, now=now,
    )


def should_recover(state: WorkerState) -> bool:
    """只有同 host 且 pid 不存在才自动判死；心跳过期但 pid 活着交用户。"""
    return state.host == socket.gethostname() and not pid_alive(state.pid)


def find_worker_state(task_dir: str | Path) -> WorkerState | None:
    try:
        return read_worker_state(task_dir)
    except WorkerStateError:
        return None


class WorkerSupervisor:
    def __init__(self, session_root: str | Path):
        self.session_root = Path(session_root)
        self.store = TaskStore(self.session_root)

    def recovery_blocked(self) -> bool:
        """活跃或无法核对的 worker 记录不能当成崩溃。"""
        directory = self.store.tasks_root
        try:
            if directory.is_symlink():
                return True
            if not directory.exists():
                return False
            for task_dir in directory.iterdir():
                if task_dir.is_symlink():
                    return True
                if not task_dir.is_dir():
                    continue
                worker = read_worker_state(task_dir)
                if worker is None:
                    continue
                if worker.task_id != task_dir.name:
                    return True
                if worker.status in ACTIVE_WORKER_STATUSES and (worker.host != socket.gethostname() or pid_alive(worker.pid)):
                    return True
        except (OSError, WorkerStateError):
            return True
        return False

    def snapshot(self) -> list[WorkerSnapshot]:
        snapshots: list[WorkerSnapshot] = []
        for record in self.store.list():
            task_dir = self.store.task_dir(record.task_id)
            worker = find_worker_state(task_dir)
            alive = pid_alive(worker.pid) if worker is not None else False
            foreign = worker is not None and worker.host != socket.gethostname()
            snapshots.append(WorkerSnapshot(
                task=record, worker=worker,
                stale=is_stale(worker) if worker is not None else False,
                pid_alive=alive, foreign_host=foreign,
            ))
        return snapshots

    def recover(self) -> list[TaskRecord]:
        recovered: list[TaskRecord] = []
        seen: set[str] = set()
        for record in self.store.recover_stale():
            recovered.append(record)
            seen.add(record.task_id)
        for snapshot in self.snapshot():
            worker = snapshot.worker
            if worker is None or worker.status not in ACTIVE_WORKER_STATUSES:
                continue
            if not should_recover(worker):
                continue
            worker.status = "interrupted"
            worker.error = "worker heartbeat/lock stale"
            write_worker_state(self.store.task_dir(worker.task_id), worker)
            try:
                self.store.append_event(
                    worker.task_id, "worker_interrupted",
                    data={"pid": worker.pid, "host": worker.host},
                )
            except TaskError:
                pass
            if worker.task_id not in seen:
                try:
                    recovered.append(self.store.get(worker.task_id))
                    seen.add(worker.task_id)
                except TaskError:
                    pass
        return recovered
