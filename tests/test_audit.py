import asyncio
import hashlib
import os
import shutil
import subprocess

import pytest

from app.agent import Agent
from app.audit import audit_completion, capture_baseline
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry
from core.models import Response, ToolCall
from core.state import CompletionDecision, TaskState, completion_decision

def run(coro):
    return asyncio.run(coro)

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")

ENV = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
ENV.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "LC_ALL": "C"})


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=ENV, check=True)


def _init_repo(path):
    git(path, "init", "-q")
    (path / "a.txt").write_text("one\n")
    git(path, "add", "-A")
    git(path, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")
    return path


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _change(path, kind="modify", before="old", after=None):
    return {
        "path": path, "kind": kind, "before_sha256": before, "after_sha256": after,
        "before_status": "present", "after_status": "present",
        "size": 3, "step": 1, "tool": "write_file",
        "tool_call_id": "w1", "approval_scope": "once", "at": "2026-01-01T00:00:00+00:00",
    }


def _verified_state(path, after_sha):
    state = TaskState(
        goal="修改 a.txt", file_changes=[_change("a.txt", after=after_sha)],
        files_changed=["a.txt"], mutation_seq=1,
        last_verification_seq=1, last_verification_ok=True,
    )
    return state, completion_decision(state)


def test_audit_clean_when_recorded_hash_matches(tmp_path):
    root = _init_repo(tmp_path)
    baseline = capture_baseline(root)
    (root / "a.txt").write_text("two\n")
    state, decision = _verified_state(root / "a.txt", _sha(root / "a.txt"))

    report = audit_completion(
        workspace=root, state=state, completion=decision, baseline=baseline,
    )

    assert report.status == "complete"
    assert report.integrity == "clean"
    assert report.verified is True
    assert report.changed_files == 1


def test_audit_marks_suspect_when_file_changed_after_record(tmp_path):
    root = _init_repo(tmp_path)
    baseline = capture_baseline(root)
    (root / "a.txt").write_text("two\n")
    recorded_sha = _sha(root / "a.txt")
    (root / "a.txt").write_text("three\n")
    state, decision = _verified_state(root / "a.txt", recorded_sha)

    report = audit_completion(
        workspace=root, state=state, completion=decision, baseline=baseline,
    )

    assert report.integrity == "suspect"
    assert report.verified is False
    assert any("differs from last recorded edit" in item for item in report.findings)


def test_audit_marks_unattributed_new_file(tmp_path):
    root = _init_repo(tmp_path)
    baseline = capture_baseline(root)
    (root / "untracked.txt").write_text("outside\n")
    state = TaskState(goal="解释项目")
    decision = CompletionDecision(True)

    report = audit_completion(
        workspace=root, state=state, completion=decision, baseline=baseline,
    )

    assert report.integrity == "suspect"
    assert report.status == "incomplete"
    assert report.verified is False
    assert "untracked.txt" in report.unattributed


def test_audit_ignores_preexisting_dirty_file_in_baseline(tmp_path):
    root = _init_repo(tmp_path)
    (root / "a.txt").write_text("user was already editing\n")
    baseline = capture_baseline(root)
    state = TaskState(goal="解释项目")
    decision = CompletionDecision(True)

    report = audit_completion(
        workspace=root, state=state, completion=decision, baseline=baseline,
    )

    assert report.integrity == "clean"
    assert report.status == "complete"


def test_audit_non_git_workspace_reports_limited_coverage(tmp_path):
    (tmp_path / "a.txt").write_text("new\n")
    state, decision = _verified_state(tmp_path / "a.txt", _sha(tmp_path / "a.txt"))

    report = audit_completion(
        workspace=tmp_path, state=state, completion=decision, baseline=None,
    )

    assert report.coverage == "limited"
    assert report.status == "complete"
    assert report.verified is True
    assert any("not a git repository" in item for item in report.findings)


async def _allow(call, preview):
    return ApprovalDecision(True, "workspace")


def test_agent_workspace_audit_detects_unrecorded_bash_change(tmp_path):
    root = _init_repo(tmp_path)
    provider = FakeProvider([
        Response(tool_calls=[ToolCall(
            "write_file", {"path": "a.txt", "content": "new"}, id="w1",
        )]),
        Response(tool_calls=[ToolCall(
            "bash", {"command": "true", "verification": True}, id="b1",
        )]),
        Response(tool_calls=[ToolCall(
            "bash", {"command": "echo broken > a.txt"}, id="b2",
        )]),
        "done",
    ])
    rounds = []
    agent = Agent(
        provider, ToolRegistry(root), approval=ApprovalGate(_allow),
        completion_guard=True, workspace_audit=True, on_round=rounds.append,
    )

    result = run(agent.run("修改 a.txt"))

    assert result.status == "done"
    assert result.verified is False
    assert result.audit is not None
    assert result.audit["integrity"] == "suspect"
    assert "a.txt" in (result.audit["findings"] and " ".join(result.audit["findings"]))
    assert len(rounds) == 1
    assert rounds[0]["audit"] is not None


def _init_subdir_workspace(tmp_path):
    """仓库根下建 backend/ 子目录作为 workspace，返回 (repo_root, backend)。"""
    root = _init_repo(tmp_path)
    backend = root / "backend"
    backend.mkdir()
    (backend / "nested.txt").write_text("one\n")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "backend")
    return root, backend


def test_audit_subdirectory_workspace_baseline_uses_workspace_relative_paths(tmp_path):
    root, backend = _init_subdir_workspace(tmp_path)
    (backend / "nested.txt").write_text("user was already editing\n")

    baseline = capture_baseline(backend)

    assert baseline["is_git"] is True
    assert set(baseline["paths"]) == {"nested.txt"}

    report = audit_completion(
        workspace=backend, state=TaskState(goal="解释项目"),
        completion=CompletionDecision(True), baseline=baseline,
    )
    assert report.coverage == "full"
    assert report.integrity == "clean"
    assert report.status == "complete"


def test_audit_subdirectory_workspace_detects_pre_dirty_file_changed_externally(tmp_path):
    root, backend = _init_subdir_workspace(tmp_path)
    (backend / "nested.txt").write_text("user was already editing\n")
    baseline = capture_baseline(backend)
    assert set(baseline["paths"]) == {"nested.txt"}
    # 仓库根的文件不在 workspace 内，不应参与子目录审计。
    (root / "a.txt").write_text("outside workspace\n")
    (backend / "nested.txt").write_text("changed outside recorded edits\n")

    report = audit_completion(
        workspace=backend, state=TaskState(goal="解释项目"),
        completion=CompletionDecision(True), baseline=baseline,
    )

    assert report.coverage == "full"
    assert report.integrity == "suspect"
    assert "nested.txt" in report.unattributed
    assert any("nested.txt" in item for item in report.findings)
    assert all("a.txt" not in item for item in report.findings)


def test_audit_file_changes_slice_excludes_old_changes(tmp_path):
    root = _init_repo(tmp_path)
    (root / "b.txt").write_text("old\n")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "b")
    baseline = capture_baseline(root)
    (root / "b.txt").write_text("new\n")
    state = TaskState(
        goal="修改 a.txt 与 b.txt",
        file_changes=[_change("a.txt", after="stale-recorded-hash"),
                      _change("b.txt", after=_sha(root / "b.txt"))],
        files_changed=["a.txt", "b.txt"], mutation_seq=2,
        last_verification_seq=2, last_verification_ok=True,
    )
    decision = completion_decision(state)
    assert decision.allowed is True

    full = audit_completion(
        workspace=root, state=state, completion=decision, baseline=baseline,
    )
    assert full.changed_files == 2
    assert any("a.txt" in item for item in full.findings)

    sliced = audit_completion(
        workspace=root, state=state, completion=decision, baseline=baseline,
        file_changes=[state.file_changes[1]],
    )
    assert sliced.changed_files == 1
    assert sliced.integrity == "clean"
    assert sliced.status == "complete"
    assert not any("a.txt" in item for item in sliced.findings)


def test_audit_empty_file_changes_slice_skips_recorded_checks(tmp_path):
    root = _init_repo(tmp_path)
    baseline = capture_baseline(root)
    state = TaskState(
        goal="解释项目", file_changes=[_change("a.txt", after="stale-recorded-hash")],
        files_changed=["a.txt"], mutation_seq=1,
    )

    report = audit_completion(
        workspace=root, state=state, completion=CompletionDecision(True),
        baseline=baseline, file_changes=[],
    )

    assert report.changed_files == 0
    assert report.integrity == "clean"
    assert report.status == "complete"
    assert not any("a.txt" in item for item in report.findings)


def test_audit_file_changes_slice_still_detects_unattributed(tmp_path):
    root = _init_repo(tmp_path)
    (root / "b.txt").write_text("old\n")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "b")
    baseline = capture_baseline(root)
    (root / "b.txt").write_text("new\n")
    (root / "outside.txt").write_text("outside\n")
    state = TaskState(
        goal="修改 b.txt", file_changes=[_change("b.txt", after=_sha(root / "b.txt"))],
        files_changed=["b.txt"], mutation_seq=1,
        last_verification_seq=1, last_verification_ok=True,
    )

    report = audit_completion(
        workspace=root, state=state, completion=completion_decision(state),
        baseline=baseline, file_changes=list(state.file_changes),
    )

    assert report.changed_files == 1
    assert report.integrity == "suspect"
    assert report.unattributed == ["outside.txt"]
    assert any("outside.txt" in item for item in report.findings)
