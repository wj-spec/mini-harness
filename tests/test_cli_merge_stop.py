"""--merge-task / --stop CLI 测试。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app import __main__ as entrypoint
from app.config import Config
from app.runtime.approvals import ApprovalInbox
from app.runtime.supervise import WorkerState, read_worker_state, write_worker_state
from app.runtime.tasks import TaskStore
from app.runtime.workspace import WorktreeManager
from app.session.store import SessionStore

GIT = shutil.which("git")


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run([GIT, "init", "-q"], cwd=root, check=True)
    subprocess.run([GIT, "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run([GIT, "config", "user.name", "Test"], cwd=root, check=True)
    (root / ".gitignore").write_text(".vibe/\n", encoding="utf-8")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run([GIT, "add", "."], cwd=root, check=True)
    subprocess.run([GIT, "commit", "-qm", "initial"], cwd=root, check=True)
    return root


@pytest.mark.skipif(GIT is None, reason="git required")
def test_merge_task_cli_requests_then_merges(repo, monkeypatch, capsys):
    session = SessionStore.new(repo, "fake", purpose="test")
    task = TaskStore(session.root).create(kind="goal", objective="merge", workspace=str(repo), mode="goal")
    worktree = WorktreeManager(repo).create(task.task_id, session.id)
    (worktree.path / "cli.txt").write_text("cli", encoding="utf-8")
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=repo))
    monkeypatch.setattr(sys, "argv", ["vibe", "--merge-task", task.task_id, "--session", session.id, "--workspace", str(repo), "--fake"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    captured = capsys.readouterr()
    out = captured.out
    assert "approval requested" in out, (out, captured.err)
    approval_id = [line.split(": ")[-1] for line in out.splitlines() if "approval requested" in line][0]

    monkeypatch.setattr(sys, "argv", ["vibe", "--merge-task", task.task_id, "--session", session.id,
                                      "--workspace", str(repo), "--approve-merge", approval_id, "--fake"])
    entrypoint.main()
    assert (repo / "cli.txt").is_file()
    from app.runtime.notifications import read_notifications
    kinds = [item["kind"] for item in read_notifications(repo)]
    assert "merge_completed" in kinds and "merge_approval_requested" in kinds


def test_stop_cli_marks_worker(tmp_path, monkeypatch, capsys):
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="stop", workspace=str(tmp_path), mode="goal")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        write_worker_state(store.task_dir(task.task_id), WorkerState(
            schema_version=1, task_id=task.task_id, pid=child.pid,
            host=__import__("socket").gethostname(), status="running",
            started_at="2026-11-03T00:00:00+00:00", heartbeat_at="2026-11-03T00:00:00+00:00",
        ))
        monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
        monkeypatch.setattr(sys, "argv", ["vibe", "--stop", "--workspace", str(tmp_path)])
        entrypoint.main()
        assert "workers" in capsys.readouterr().out
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill(); child.wait()


def test_pause_task_requires_workspace_and_does_not_write_stop(monkeypatch, capsys, tmp_path):
    import json
    from app import __main__ as entrypoint

    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: entrypoint.Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--pause-task", "t-20261106-000000-abcdef", "--workspace", str(tmp_path)])
    entrypoint.main()
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["paused"] is True and payload["workers"] == []
    assert not (tmp_path / ".vibe" / "stop").exists()
