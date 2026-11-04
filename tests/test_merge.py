"""MergeGate P1 测试（spec: docs/specs/workspace-isolation.md）。"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from app.runtime.approvals import ApprovalInbox
from app.runtime.merge import MergeDrift, MergeDirty, MergeGate, MergeNotApproved
from app.runtime.tasks import TaskStore
from app.runtime.workspace import WorktreeManager
from app.session.store import SessionStore

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is required")


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


def _task(repo):
    session = SessionStore.new(repo, "fake", purpose="test")
    task = TaskStore(session.root).create(kind="goal", objective="mergeq", workspace=str(repo), mode="goal")
    return session, task


def _prepare_with_change(repo, session, task):
    worktree = WorktreeManager(repo).create(task.task_id, session.id)
    (worktree.path / "feature.txt").write_text("feature\n", encoding="utf-8")
    return worktree


def test_merge_requires_approval_and_merges(repo):
    session, task = _task(repo)
    _prepare_with_change(repo, session, task)
    gate = MergeGate(repo)
    with pytest.raises(MergeNotApproved):
        gate.apply(task.task_id, session_root=session.root)

    approval = gate.request_approval(task.task_id, session_root=session.root)
    ApprovalInbox(session.root).resolve(task.task_id, approval["approval_id"], decision="once")
    result = gate.apply(task.task_id, session_root=session.root)
    assert result["merged"] is True and result["post_merge_verified"] is False
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "feature\n"
    status = subprocess.run([GIT, "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True, check=True)
    assert status.stdout.strip() == ""


def test_merge_detects_main_drift(repo):
    session, task = _task(repo)
    _prepare_with_change(repo, session, task)
    gate = MergeGate(repo)
    approval = gate.request_approval(task.task_id, session_root=session.root)
    ApprovalInbox(session.root).resolve(task.task_id, approval["approval_id"], decision="once")
    (repo / "user.txt").write_text("user edit\n", encoding="utf-8")
    subprocess.run([GIT, "-C", str(repo), "add", "user.txt"], check=True)
    subprocess.run([GIT, "-C", str(repo), "commit", "-qm", "user"], check=True)
    with pytest.raises((MergeDrift, MergeDirty)):
        gate.apply(task.task_id, session_root=session.root)


def test_merge_blocks_dirty_main(repo):
    session, task = _task(repo)
    _prepare_with_change(repo, session, task)
    gate = MergeGate(repo)
    approval = gate.request_approval(task.task_id, session_root=session.root)
    ApprovalInbox(session.root).resolve(task.task_id, approval["approval_id"], decision="once")
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(MergeDirty):
        gate.apply(task.task_id, session_root=session.root)
