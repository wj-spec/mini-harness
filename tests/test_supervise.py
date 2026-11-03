"""WorkerSupervisor P0a 测试（spec: docs/specs/process-supervision.md）。"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timedelta, timezone

import pytest

from app.runtime.supervise import (
    WorkerState,
    WorkerSupervisor,
    WorkerStateError,
    heartbeat,
    heartbeat_expired,
    is_stale,
    pid_alive,
    read_worker_state,
    should_recover,
    write_worker_state,
)
from app.runtime.tasks import TaskStore


def _session_roots(tmp_path):
    session = tmp_path / "session"
    store = TaskStore(session)
    return session, store


def _state(task_id, *, pid=None, host=None, status="running", heartbeat_at=None, started_at=None):
    now = datetime.now(timezone.utc)
    return WorkerState(
        schema_version=1, task_id=task_id, pid=pid or os.getpid(),
        host=host or socket.gethostname(), status=status,
        started_at=started_at or now.isoformat(),
        heartbeat_at=heartbeat_at or now.isoformat(),
    )


def test_worker_state_roundtrip_and_heartbeat(tmp_path):
    session, store = _session_roots(tmp_path)
    task = store.create(kind="goal", objective="测试", workspace=str(tmp_path), mode="goal")
    directory = store.task_dir(task.task_id)
    assert read_worker_state(directory) is None

    state = _state(task.task_id, pid=os.getpid())
    write_worker_state(directory, state)
    loaded = read_worker_state(directory)
    assert loaded == state

    old = datetime.now(timezone.utc) - timedelta(minutes=5)
    state.heartbeat_at = old.isoformat()
    write_worker_state(directory, state)
    updated = heartbeat(directory)
    assert updated.heartbeat_at != old.isoformat()
    assert not heartbeat_expired(updated, ttl_seconds=30)


def test_corrupt_worker_state_is_isolated(tmp_path):
    session, store = _session_roots(tmp_path)
    first = store.create(kind="goal", objective="a", workspace=str(tmp_path), mode="goal")
    second = store.create(kind="goal", objective="b", workspace=str(tmp_path), mode="goal")
    (store.task_dir(first.task_id) / "worker.json").write_text("{bad", encoding="utf-8")
    write_worker_state(store.task_dir(second.task_id), _state(second.task_id))

    snapshots = WorkerSupervisor(session).snapshot()
    by_id = {snapshot.task.task_id: snapshot for snapshot in snapshots}
    assert by_id[first.task_id].worker is None
    assert by_id[second.task_id].worker is not None


def test_stale_detection_and_recover(tmp_path):
    session, store = _session_roots(tmp_path)
    task = store.create(kind="goal", objective="崩溃", workspace=str(tmp_path), mode="goal")
    store.transition(task.task_id, "running")
    write_worker_state(store.task_dir(task.task_id), _state(task.task_id, pid=999_999_999))

    supervisor = WorkerSupervisor(session)
    snapshot = next(item for item in supervisor.snapshot() if item.task.task_id == task.task_id)
    assert snapshot.stale and not snapshot.pid_alive

    recovered = supervisor.recover()
    assert any(item.task_id == task.task_id for item in recovered)
    assert store.get(task.task_id).status == "interrupted"
    worker = read_worker_state(store.task_dir(task.task_id))
    assert worker is not None and worker.status == "interrupted"


def test_heartbeat_expired_with_live_pid_is_not_recovered(tmp_path):
    session, store = _session_roots(tmp_path)
    task = store.create(kind="goal", objective="卡住", workspace=str(tmp_path), mode="goal")
    store.transition(task.task_id, "running")
    old = datetime.now(timezone.utc) - timedelta(minutes=5)
    state = _state(task.task_id, pid=os.getpid(), heartbeat_at=old.isoformat())
    write_worker_state(store.task_dir(task.task_id), state)

    snapshot = WorkerSupervisor(session).snapshot()[0]
    assert snapshot.stale and snapshot.pid_alive
    assert not should_recover(state)
    # 任务没有 stale lock 时 TaskStore.recover_stale 也会把它标 interrupted；
    # 但 worker 判死逻辑本身不应把活着的 pid 当死。
    assert should_recover(state) is False


def test_foreign_host_is_not_auto_recovered(tmp_path):
    session, store = _session_roots(tmp_path)
    task = store.create(kind="goal", objective="远程", workspace=str(tmp_path), mode="goal")
    store.transition(task.task_id, "running")
    state = _state(task.task_id, pid=999_999_999, host="other-host")
    write_worker_state(store.task_dir(task.task_id), state)
    assert not is_stale(state)
    assert not should_recover(state)
    snapshot = WorkerSupervisor(session).snapshot()[0]
    assert snapshot.foreign_host and not snapshot.stale


def test_invalid_worker_state_rejected(tmp_path):
    session, store = _session_roots(tmp_path)
    task = store.create(kind="goal", objective="坏", workspace=str(tmp_path), mode="goal")
    (store.task_dir(task.task_id) / "worker.json").write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    with pytest.raises(WorkerStateError):
        read_worker_state(store.task_dir(task.task_id))


def test_pid_alive():
    assert pid_alive(os.getpid())
    assert not pid_alive(999_999_999)
    assert not pid_alive(0)
