"""--tasks 只读任务列表测试。"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone

import pytest

from app import __main__ as entrypoint
from app.config import Config
from app.runtime.approvals import ApprovalInbox
from app.runtime.supervise import WorkerState, write_worker_state
from app.runtime.tasks import TaskStore
from app.session.store import SessionStore


def _seed(tmp_path):
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="长任务目标", workspace=str(tmp_path), mode="goal")
    plan_task = store.create(kind="run", objective="计划任务", workspace=str(tmp_path),
                             mode="act", plan_id="pl-cli", plan_version=2, priority=4)
    ApprovalInbox(session.root).submit(
        plan_task.task_id, tool="bash", tier="exec",
        decisions=["once", "deny"], preview="ls",
    )
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
    assert "APPROVALS" in out and "PRIORITY" in out
    plan_row = next(line for line in out.splitlines() if plan_task.task_id in line)
    assert "\t1\t" in plan_row
    assert "\t4\t" in plan_row


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


def test_tasks_json_output(tmp_path, monkeypatch, capsys):
    import json
    _session, _store, _task, plan_task = _seed(tmp_path)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--tasks", "--tasks-json"])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["skipped_task_dirs"] == 0
    rows = {item["task_id"]: item for item in payload["tasks"]}
    assert rows[plan_task.task_id]["plan_id"] == "pl-cli"
    assert rows[plan_task.task_id]["mode"] == "act"
    assert rows[plan_task.task_id]["pending_approvals"] == 1
    assert rows[plan_task.task_id]["priority"] == 4
    assert rows[_task.task_id]["pending_approvals"] == 0


def test_tasks_reports_corrupt_task_dirs(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    session_root = max((tmp_path / ".vibe" / "sessions").iterdir(),
                       key=lambda path: path.stat().st_mtime)
    corrupt = session_root / "tasks" / "t-20261107-000000-badbad"
    corrupt.mkdir(parents=True)
    (corrupt / "meta.json").write_text("{bad json", encoding="utf-8")
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--tasks", "--tasks-json", "--workspace", str(tmp_path)])
    entrypoint.main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["skipped_task_dirs"] == 1
    assert "skipped 1 corrupt task directories" in captured.err
