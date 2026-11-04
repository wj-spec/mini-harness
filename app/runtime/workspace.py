"""工作区单写者锁与 git worktree 隔离（spec: docs/specs/workspace-isolation.md P0）。"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOCK_FILE = "workspace.lock"
WORKTREE_DIR = "worktrees"


class WorkspaceError(Exception):
    pass


class WorkspaceBusy(WorkspaceError):
    pass


class WorktreeError(WorkspaceError):
    pass


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OverflowError, OSError):
        return False
    return True


@dataclass
class WorkspaceLock:
    path: Path
    task_id: str
    session_id: str
    pid: int

    def release(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if payload.get("pid") == self.pid:
            self.path.unlink(missing_ok=True)

    def __enter__(self) -> "WorkspaceLock":
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


def _lock_path(workspace: str | Path) -> Path:
    return Path(workspace) / ".vibe" / LOCK_FILE


def acquire_workspace_lock(
    workspace: str | Path, *, task_id: str, session_id: str,
) -> WorkspaceLock:
    """写任务互斥锁；读任务不调用。O_EXCL + 同 host pid 判死回收。"""
    path = _lock_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": task_id, "session_id": session_id, "pid": os.getpid(),
        "host": socket.gethostname(), "created_at": _now(),
    }
    for attempt in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
            if existing.get("host") == socket.gethostname() and _pid_alive(existing.get("pid")):
                raise WorkspaceBusy(
                    f"workspace locked by task {existing.get('task_id')} (pid {existing.get('pid')})"
                )
            path.unlink(missing_ok=True)
            if attempt == 1:
                raise WorkspaceBusy("workspace lock is stale but could not be reclaimed")
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return WorkspaceLock(path=path, task_id=task_id, session_id=session_id, pid=os.getpid())
    raise WorkspaceBusy("workspace lock unavailable")


def _git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise WorktreeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


@dataclass
class Worktree:
    task_id: str
    session_id: str
    path: Path
    branch: str
    base_commit: str
    status: str = "active"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1, "task_id": self.task_id, "session_id": self.session_id,
            "path": str(self.path), "branch": self.branch,
            "base_commit": self.base_commit, "status": self.status,
        }


class WorktreeManager:
    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).expanduser().resolve()
        self.repo_root = self._repo_root()
        self.root = self.repo_root / ".vibe" / WORKTREE_DIR

    def _repo_root(self) -> Path:
        try:
            root = _git(self.workspace, "rev-parse", "--show-toplevel")
        except (WorktreeError, OSError) as exc:
            raise WorktreeError(f"workspace is not a git repository: {exc}") from exc
        return Path(root).resolve()

    def _meta_path(self, task_id: str) -> Path:
        return self.root / f"{task_id}.meta.json"

    def create(self, task_id: str, session_id: str) -> Worktree:
        if not task_id or "/" in task_id or "\\" in task_id or task_id in {".", ".."}:
            raise WorktreeError("invalid task_id")
        path = self.root / task_id
        if path.exists():
            raise WorktreeError(f"worktree already exists: {path}")
        branch = f"vibe/{session_id}-{task_id}"
        base_commit = _git(self.repo_root, "rev-parse", "HEAD")
        self.root.mkdir(parents=True, exist_ok=True)
        _git(self.repo_root, "worktree", "add", "-b", branch, str(path), base_commit)
        worktree = Worktree(
            task_id=task_id, session_id=session_id, path=path,
            branch=branch, base_commit=base_commit,
        )
        self._meta_path(task_id).write_text(
            json.dumps(worktree.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return worktree

    def list(self) -> list[Worktree]:
        result: list[Worktree] = []
        if not self.root.is_dir():
            return result
        for meta_path in sorted(self.root.glob("*.meta.json")):
            try:
                value = json.loads(meta_path.read_text(encoding="utf-8"))
                worktree = Worktree(
                    task_id=value["task_id"], session_id=value["session_id"],
                    path=Path(value["path"]), branch=value["branch"],
                    base_commit=value["base_commit"], status=value.get("status", "active"),
                )
            except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
                continue
            result.append(worktree)
        return result

    def stale(self) -> list[Worktree]:
        return [item for item in self.list() if item.status == "active" and not item.path.is_dir()]

    def remove(self, task_id: str, *, keep_branch: bool = False) -> None:
        match = next((item for item in self.list() if item.task_id == task_id), None)
        if match is None:
            raise WorktreeError(f"unknown worktree: {task_id}")
        if match.path.is_dir():
            _git(self.repo_root, "worktree", "remove", "--force", str(match.path))
        if not keep_branch:
            subprocess.run(
                ["git", "-C", str(self.repo_root), "branch", "-D", match.branch],
                capture_output=True, text=True, timeout=30,
            )
        meta = self._meta_path(task_id)
        if meta.is_file():
            payload = json.loads(meta.read_text(encoding="utf-8"))
            payload["status"] = "removed"
            meta.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def diff_stat(self, task_id: str) -> str:
        match = next((item for item in self.list() if item.task_id == task_id), None)
        if match is None:
            raise WorktreeError(f"unknown worktree: {task_id}")
        parts = []
        try:
            tracked = _git(match.path, "diff", "--stat", match.base_commit)
        except WorktreeError:
            tracked = ""
        if tracked:
            parts.append(tracked)
        untracked = _git(match.path, "ls-files", "--others", "--exclude-standard")
        if untracked:
            parts.append("untracked:\n" + "\n".join(f" {line}" for line in untracked.splitlines()))
        return "\n".join(parts)
