"""--tasks 只读任务列表测试。"""

from __future__ import annotations

import socket
from datetime import datetime, timezone

import pytest

from app import __main__ as entrypoint
from app.config import Config
from app.runtime.supervise import WorkerState, write_worker_state
from app.runtime.tasks import TaskStore
from app.session.store import SessionStore


def _seed(tmp_path):
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="长任务目标", workspace=str(tmp_path), mode="goal")
    plan_task = store.create(kind="run", objective="计划任务", workspace=str(tmp_path),
                             mode="act", plan_id="pl-cli", plan_version=2)
    write_worker_state(store.task_dir(task.task_id), WorkerState(
        schema_version=1, task_id=task.task_id, pid=999_999_999,
        host=socket.gethostname(), status="running",
        started_at=datetime.now(timezone.utc).isoformat(),
        heartbeat_at=datetime.now(timezone.utc).isoformat(),
    ))
    return session, store, task, plan_task


def test_tasks_lists_latest_session_tasks(tmp_path, monkeypatch, capsys):
    _session, _store, _task, plan_task = _seed(tmp_path)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--tasks", "--workspace", str(tmp_path)])
    entrypoint.main()
    out = capsys.readouterr().out
    assert "TASK_ID" in out and "WORKER" in out and "PLAN" in out
    assert "长任务目标" in out
    assert "goal" in out and "running" in out
    assert "pl-cliv2" in out and "plan_task" not in out
    assert plan_task.task_id in out


def test_tasks_without_session_is_read_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--tasks"])
    entrypoint.main()
    assert "no tasks under" in capsys.readouterr().out
    assert not (tmp_path / ".vibe" / "sessions").exists()


def test_tasks_rejects_other_modes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--tasks", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--tasks cannot be combined" in capsys.readouterr().err
