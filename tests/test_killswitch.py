"""workspace kill switch 测试（spec: docs/specs/workspace-isolation.md）。"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time

from app.runtime.killswitch import active_workers, stop_file, stop_workers
from app.runtime.supervise import WorkerState, read_worker_state, write_worker_state
from app.runtime.tasks import TaskStore
from app.session.store import SessionStore


def _seed(tmp_path):
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="kill", workspace=str(tmp_path), mode="goal")
    return session, store, task


def _state(task):
    return WorkerState(
        schema_version=1, task_id=task.task_id, pid=0, host=socket.gethostname(),
        status="running", started_at="2026-11-03T00:00:00+00:00",
        heartbeat_at="2026-11-03T00:00:00+00:00",
    )


def test_stop_workers_terminates_and_marks_state(tmp_path):
    session, store, task = _seed(tmp_path)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        state = _state(task)
        state.pid = child.pid
        write_worker_state(store.task_dir(task.task_id), state)
        assert [item[0] for item in active_workers(tmp_path)] == [task.task_id]

        results = stop_workers(tmp_path, grace=0.5)
        assert results and results[0]["signalled"]
        time.sleep(0.2)
        assert child.poll() is not None
        assert stop_file(tmp_path).is_file()
        updated = read_worker_state(store.task_dir(task.task_id))
        assert updated is not None and updated.status == "killed"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_stop_specific_task_only(tmp_path):
    session, store, first = _seed(tmp_path)
    second = store.create(kind="goal", objective="other", workspace=str(tmp_path), mode="goal")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        state = _state(second)
        state.pid = child.pid
        write_worker_state(store.task_dir(second.task_id), state)
        results = stop_workers(tmp_path, task_id=first.task_id, grace=0.2)
        assert results == []
        assert child.poll() is None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
