"""WorkspaceLock/WorktreeManager P0 测试（spec: docs/specs/workspace-isolation.md）。"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from app.runtime.workspace import (
    WorktreeError,
    WorktreeManager,
    WorkspaceBusy,
    acquire_workspace_lock,
)

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is required")


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run([GIT, "init", "-q"], cwd=root, check=True)
    subprocess.run([GIT, "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run([GIT, "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run([GIT, "add", "."], cwd=root, check=True)
    subprocess.run([GIT, "commit", "-qm", "initial"], cwd=root, check=True)
    (root / ".vibe").mkdir()
    (root / ".gitignore").write_text(".vibe/\n", encoding="utf-8")
    subprocess.run([GIT, "add", ".gitignore"], cwd=root, check=True)
    subprocess.run([GIT, "commit", "-qm", "ignore vibe"], cwd=root, check=True)
    return root


def test_workspace_lock_serializes_and_reclaims_stale(repo):
    lock = acquire_workspace_lock(repo, task_id="t-1", session_id="s-1")
    try:
        with pytest.raises(WorkspaceBusy):
            acquire_workspace_lock(repo, task_id="t-2", session_id="s-1")
    finally:
        lock.release()

    path = repo / ".vibe" / "workspace.lock"
    path.write_text(json.dumps({
        "task_id": "t-dead", "session_id": "s-1", "pid": 999_999_999,
        "host": socket.gethostname(), "created_at": "2026-11-03T00:00:00+00:00",
    }), encoding="utf-8")
    reclaimed = acquire_workspace_lock(repo, task_id="t-3", session_id="s-1")
    assert json.loads(path.read_text(encoding="utf-8"))["task_id"] == "t-3"
    reclaimed.release()


def test_worktree_isolates_writes_and_diff(repo):
    manager = WorktreeManager(repo)
    worktree = manager.create("t-1", "s-1")
    assert worktree.path.is_dir()
    assert worktree.path != repo
    assert manager.list()[0].branch == worktree.branch

    (worktree.path / "only-in-worktree.txt").write_text("x", encoding="utf-8")
    assert not (repo / "only-in-worktree.txt").exists()
    assert "only-in-worktree.txt" in manager.diff_stat("t-1")

    manager.remove("t-1")
    assert not worktree.path.exists()
    branches = subprocess.run([GIT, "-C", str(repo), "branch"], capture_output=True, text=True, check=True)
    assert worktree.branch not in branches.stdout


def test_worktree_list_reports_missing_path_stale(repo):
    manager = WorktreeManager(repo)
    worktree = manager.create("t-2", "s-1")
    shutil.rmtree(worktree.path)
    assert [item.task_id for item in manager.stale()] == ["t-2"]


def test_non_git_workspace_is_rejected(tmp_path):
    with pytest.raises(WorktreeError):
        WorktreeManager(tmp_path)
