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

def test_merge_runs_post_merge_verification(repo):
    session, task = _task(repo)
    _prepare_with_change(repo, session, task)
    gate = MergeGate(repo)
    approval = gate.request_approval(task.task_id, session_root=session.root)
    ApprovalInbox(session.root).resolve(task.task_id, approval["approval_id"], decision="once")
    result = gate.apply(
        task.task_id, session_root=session.root,
        verify_commands=["test -f feature.txt", "test -f missing.txt"],
    )
    assert result["post_merge_verified"] is False
    assert [item["ok"] for item in result["verification"]] == [True, False]


def _approved_plan(session, repo, *, command="test -f feature.txt"):
    from app.runtime.plan import Plan, approve_plan, propose_plan

    plan_task = TaskStore(session.root).create(
        kind="run", objective="计划", workspace=str(repo), mode="plan",
    )
    task_dir = TaskStore(session.root).task_dir(plan_task.task_id)
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-merge", "task_id": plan_task.task_id,
        "version": 1, "objective": "合并后重验",
        "steps": [{"step_id": "s1", "title": "改文件"}],
        "acceptance_criteria": [{"id": "ac1", "text": "文件存在", "confirmed": True}],
        "verification_plan": [{"id": "v1", "command": command, "covers": ["ac1"],
                               "source": "plan_approval"}],
    })
    propose_plan(task_dir, plan)
    approve_plan(task_dir, plan, approved_by="alice")
    return plan


def _plan_bound_task(repo, session):
    from app.runtime.tasks import TaskStore

    plan = _approved_plan(session, repo)
    task = TaskStore(session.root).create(
        kind="run", objective="合并计划任务", workspace=str(repo), mode="act",
        plan_id=plan.plan_id, plan_version=plan.version,
    )
    return plan, task


def test_plan_verification_commands_only_for_approved_plan(repo):
    from app.runtime.merge import plan_verification_commands
    from app.runtime.plan import Plan, propose_plan
    from app.runtime.tasks import TaskStore

    session = SessionStore.new(repo, "fake", purpose="test")
    plan_task = TaskStore(session.root).create(kind="run", objective="计划", workspace=str(repo), mode="plan")
    task_dir = TaskStore(session.root).task_dir(plan_task.task_id)
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-x", "task_id": plan_task.task_id,
        "version": 1, "objective": "未批准", "steps": [{"step_id": "s1", "title": "读"}],
        "acceptance_criteria": [{"id": "ac1", "text": "ok", "confirmed": True}],
        "verification_plan": [{"id": "v1", "command": "test -f README.md", "covers": ["ac1"]}],
    })
    propose_plan(task_dir, plan)
    task = TaskStore(session.root).create(
        kind="run", objective="绑定", workspace=str(repo), mode="act",
        plan_id="pl-x", plan_version=1,
    )
    assert plan_verification_commands(session.root, task) == []
    assert plan_verification_commands(session.root, _task(repo)[1]) == []


def test_plan_verification_commands_from_bound_plan(repo):
    from app.runtime.merge import plan_verification_commands

    session = SessionStore.new(repo, "fake", purpose="test")
    plan, task = _plan_bound_task(repo, session)
    assert plan_verification_commands(session.root, task) == ["test -f feature.txt"]


def test_merge_approval_preview_lists_verify_commands(repo):
    session, task = _task(repo)
    _prepare_with_change(repo, session, task)
    approval = MergeGate(repo).request_approval(
        task.task_id, session_root=session.root, verify_commands=["true"],
        include_preview=True,
    )
    assert "合并后验收命令" in approval["preview_redacted"]
    assert "- true" in approval["preview_redacted"]


def test_merge_plan_commands_run_after_merge(repo):
    from app.runtime.merge import plan_verification_commands

    session = SessionStore.new(repo, "fake", purpose="test")
    plan, task = _plan_bound_task(repo, session)
    worktree = WorktreeManager(repo).create(task.task_id, session.id)
    (worktree.path / "feature.txt").write_text("feature\n", encoding="utf-8")
    gate = MergeGate(repo)
    commands = plan_verification_commands(session.root, task)
    approval = gate.request_approval(
        task.task_id, session_root=session.root, verify_commands=commands,
    )
    ApprovalInbox(session.root).resolve(task.task_id, approval["approval_id"], decision="once")
    result = gate.apply(task.task_id, session_root=session.root, verify_commands=commands)
    assert result["merged"] is True
    assert result["post_merge_verified"] is True
    assert result["verify_commands"] == ["test -f feature.txt"]
