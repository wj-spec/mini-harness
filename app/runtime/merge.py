"""Worktree 合并门禁（spec: docs/specs/workspace-isolation.md P1）。"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.runtime.approvals import ApprovalInbox
from app.runtime.workspace import Worktree, WorktreeError, WorktreeManager


class MergeError(Exception):
    pass


class MergeNotApproved(MergeError):
    pass


class MergeDrift(MergeError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


class MergeDirty(MergeError):
    pass


class MergeConflict(MergeError):
    def __init__(self, message: str, *, files: list[str] | None = None):
        super().__init__(message)
        self.files = files or []


@dataclass
class MergeCandidate:
    task_id: str
    branch: str
    base_commit: str
    head_commit: str
    main_head: str
    diff_stat: str
    changed_files: list[str] = field(default_factory=list)
    main_dirty: list[str] = field(default_factory=list)
    drift: bool = False
    committed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "branch": self.branch,
            "base_commit": self.base_commit, "head_commit": self.head_commit,
            "main_head": self.main_head, "diff_stat": self.diff_stat,
            "changed_files": list(self.changed_files),
            "main_dirty": list(self.main_dirty),
            "drift": self.drift, "committed": self.committed,
        }


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=60,
    )


def _git_checked(cwd: Path, *args: str) -> str:
    result = _git(cwd, *args)
    if result.returncode != 0:
        raise MergeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


class MergeGate:
    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).expanduser().resolve()
        self.manager = WorktreeManager(self.workspace)
        self.repo_root = self.manager.repo_root

    def _worktree(self, task_id: str) -> Worktree:
        match = next((item for item in self.manager.list() if item.task_id == task_id), None)
        if match is None:
            raise MergeError(f"unknown worktree for task {task_id}")
        if not match.path.is_dir():
            raise MergeError(f"worktree is missing: {match.path}")
        return match

    def prepare(self, task_id: str, *, message: str | None = None) -> MergeCandidate:
        """把 worktree 内改动提交到任务分支，并生成 diff/漂移报告。"""
        worktree = self._worktree(task_id)
        _git_checked(worktree.path, "add", "-A")
        status = _git_checked(worktree.path, "status", "--porcelain")
        committed = False
        if status:
            _git_checked(worktree.path, "commit", "-m", message or f"vibe task {task_id}")
            committed = True
        head = _git_checked(worktree.path, "rev-parse", "HEAD")
        base = worktree.base_commit
        diff_stat = _git_checked(self.repo_root, "diff", "--stat", f"{base}..{head}")
        names = _git_checked(self.repo_root, "diff", "--name-only", f"{base}..{head}")
        changed = [line for line in names.splitlines() if line.strip()]
        main_head = _git_checked(self.repo_root, "rev-parse", "HEAD")
        dirty = _git_checked(self.repo_root, "status", "--porcelain")
        main_dirty = [line for line in dirty.splitlines() if line.strip() and not line.startswith("?? .vibe/")]
        return MergeCandidate(
            task_id=task_id, branch=worktree.branch, base_commit=base,
            head_commit=head, main_head=main_head, diff_stat=diff_stat,
            changed_files=changed, main_dirty=main_dirty, drift=main_head != base,
            committed=committed,
        )

    def request_approval(
        self, task_id: str, *, session_root: str | Path,
        requester: str = "harness", include_preview: bool = False,
    ) -> dict[str, Any]:
        candidate = self.prepare(task_id)
        inbox = ApprovalInbox(session_root)
        return inbox.submit(
            task_id, tool="merge_worktree", tier="workspace_write",
            decisions=["once", "deny"], requester=requester,
            preview=candidate.diff_stat or "(empty diff)",
            preview_ref=f"branch:{candidate.branch}",
            include_preview=include_preview,
        )

    def approval_for(self, task_id: str, *, session_root: str | Path) -> dict[str, Any] | None:
        inbox = ApprovalInbox(session_root)
        for item in inbox.items(task_id):
            if item.get("tool") == "merge_worktree" and item.get("status") == "resolved":
                if item.get("decision") != "deny":
                    return item
        return None

    def apply(
        self, task_id: str, *, session_root: str | Path,
        message: str | None = None,
    ) -> dict[str, Any]:
        approval = self.approval_for(task_id, session_root=session_root)
        if approval is None:
            raise MergeNotApproved("merge_worktree approval is required")
        candidate = self.prepare(task_id, message=message)
        if candidate.main_dirty:
            raise MergeDirty("main workspace has uncommitted changes: " + "; ".join(candidate.main_dirty[:5]))
        if candidate.drift:
            raise MergeDrift(
                "main workspace HEAD moved since worktree base",
                details={"base_commit": candidate.base_commit, "main_head": candidate.main_head},
            )
        result = _git(self.repo_root, "merge", "--no-ff", candidate.branch,
                      "-m", message or f"merge task {task_id}")
        if result.returncode != 0:
            files_result = _git(self.repo_root, "diff", "--name-only", "--diff-filter=U")
            files = [line for line in files_result.stdout.splitlines() if line.strip()]
            _git(self.repo_root, "merge", "--abort")
            raise MergeConflict(result.stderr.strip() or "merge conflict", files=files)
        merged_commit = _git_checked(self.repo_root, "rev-parse", "HEAD")
        return {
            "merged": True, "commit": merged_commit, "branch": candidate.branch,
            "base_commit": candidate.base_commit, "changed_files": candidate.changed_files,
            "diff_stat": candidate.diff_stat, "post_merge_verified": False,
            "approval_id": approval.get("approval_id"),
        }
