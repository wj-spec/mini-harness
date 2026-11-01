from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from core.state import CompletionDecision, TaskState

AUDIT_SCHEMA_VERSION = 1
GIT_TIMEOUT_SECONDS = 10.0
MAX_HASH_BYTES = 8 * 1024 * 1024
MAX_UNATTRIBUTED_SHOWN = 20


@dataclass
class AuditReport:
    """确定性环境审计结论；只做只读检查，不修改工作区。"""

    status: str
    integrity: str
    verified: bool
    findings: list[str] = field(default_factory=list)
    unattributed: list[str] = field(default_factory=list)
    changed_files: int = 0
    coverage: str = "full"
    contract_audit: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "status": self.status,
            "integrity": self.integrity,
            "verified": self.verified,
            "findings": list(self.findings),
            "unattributed": list(self.unattributed),
            "changed_files": int(self.changed_files),
            "coverage": self.coverage,
            "contract_audit": self.contract_audit,
        }


def _workspace_path(root: Path, relative: str) -> Path | None:
    try:
        candidate = (root / relative).resolve()
    except (OSError, ValueError):
        return None
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def path_metadata(path: Path) -> dict:
    """文件存在性 + 大小 + SHA-256；不跟随可疑路径、不读超限大文件。"""
    try:
        if not path.exists():
            return {"status": "missing"}
        if path.is_symlink():
            return {"status": "symlink"}
        if path.is_dir():
            return {"status": "directory"}
        size = path.stat().st_size
    except OSError:
        return {"status": "unreadable"}
    if size > MAX_HASH_BYTES:
        return {"status": "too_large", "size": size}
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return {"status": "unreadable", "size": size}
    return {"status": "present", "sha256": digest, "size": size}


def _git_prefix(workspace: Path) -> str | None:
    """workspace 相对仓库根的前缀：子目录形如 "backend/"，仓库根为空串。

    非 git 仓库或命令失败返回 None（调用方沿用现有降级）。
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--show-prefix"],
            cwd=workspace,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    # 只去掉行尾换行；目录名本身可能含空格，不能用 strip。
    return completed.stdout.decode("utf-8", errors="replace").rstrip("\r\n")


def _workspace_relative_path(repo_path: str, prefix: str) -> str | None:
    """把仓库根相对路径剥成 workspace 相对路径；workspace 外的路径返回 None。"""
    if not prefix:
        return repo_path
    if not repo_path.startswith(prefix):
        return None
    relative = repo_path[len(prefix):]
    return relative or None


def _git_status_paths(workspace: Path) -> tuple[bool, set[str]]:
    """只读 git status，返回 workspace 相对路径。

    ``git status --porcelain`` 输出仓库根相对路径；workspace 是子目录时必须
    用 ``rev-parse --show-prefix`` 剥前缀，否则基线路径会错位。
    非 git 仓库或 git 不可用时返回 (False, set())。
    """
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z"],
            cwd=workspace,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, set()
    if completed.returncode != 0:
        return False, set()
    prefix = _git_prefix(workspace)
    if prefix is None:
        return False, set()
    raw = completed.stdout.decode("utf-8", errors="replace")
    paths: set[str] = set()
    chunks = raw.split("\0")
    index = 0
    while index < len(chunks):
        entry = chunks[index]
        index += 1
        if len(entry) < 4 or entry[2] != " ":
            continue
        status = entry[:2]
        relative = _workspace_relative_path(entry[3:], prefix)
        if relative is not None:
            paths.add(relative)
        if "R" in status or "C" in status:
            if index < len(chunks) and chunks[index]:
                relative = _workspace_relative_path(chunks[index], prefix)
                if relative is not None:
                    paths.add(relative)
                index += 1
    return True, paths


def capture_baseline(workspace: str | Path) -> dict:
    """run 开始时的只读基线：git 判断 + 当时脏/未跟踪文件的 hash。"""
    root = Path(workspace).resolve()
    is_git, paths = _git_status_paths(root)
    if not is_git:
        return {"is_git": False, "paths": {}}
    recorded: dict[str, dict] = {}
    for relative in sorted(paths):
        target = _workspace_path(root, relative)
        if target is None:
            continue
        recorded[relative] = path_metadata(target)
    return {"is_git": True, "paths": recorded}


def _check_recorded_changes(
    root: Path, file_changes: list[dict], files_changed: list[str],
) -> tuple[list[str], list[str], int]:
    findings: list[str] = []
    suspect: list[str] = []
    known: set[str] = set()
    for change in file_changes:
        path = str(change.get("path") or "")
        if not path:
            continue
        known.add(path)
        target = _workspace_path(root, path)
        if target is None:
            findings.append(f"recorded path escapes workspace: {path}")
            suspect.append(path)
            continue
        current = path_metadata(target)
        expected_status = str(change.get("after_status") or "missing")
        expected_sha = change.get("after_sha256")
        if expected_status == "present":
            if current.get("status") != "present":
                findings.append(f"file changed after last recorded edit: {path}")
                suspect.append(path)
            elif expected_sha and current.get("sha256") != expected_sha:
                findings.append(f"file content differs from last recorded edit: {path}")
                suspect.append(path)
        elif expected_status == "missing":
            if current.get("status") != "missing":
                findings.append(f"recorded deletion was undone or file reappeared: {path}")
                suspect.append(path)
        else:
            findings.append(f"file could not be hashed after edit ({expected_status}): {path}")
            suspect.append(path)
    for path in files_changed:
        if str(path) not in known:
            findings.append(f"changed file has no hash record (not independently verifiable): {path}")
    return findings, suspect, len(known)


def _recorded_changes(
    state: TaskState, file_changes: list[dict] | None,
) -> tuple[list[dict], list[str]]:
    """本次要审计的改动切片。

    ``file_changes`` 为 None 时用整份 state（向后兼容）；显式传入时只审这些
    记录，``files_changed`` 由切片内路径推导。
    """
    if file_changes is None:
        changes = [
            change for change in (getattr(state, "file_changes", []) or [])
            if isinstance(change, dict)
        ]
        files_changed = [str(path) for path in (getattr(state, "files_changed", []) or [])]
        return changes, files_changed
    changes = [change for change in file_changes if isinstance(change, dict)]
    files_changed: list[str] = []
    for change in changes:
        path = str(change.get("path") or "")
        if path and path not in files_changed:
            files_changed.append(path)
    return changes, files_changed


def audit_completion(
    *,
    workspace: str | Path,
    state: TaskState,
    completion: CompletionDecision,
    baseline: dict | None = None,
    file_changes: list[dict] | None = None,
) -> AuditReport:
    """工作区审计：记录的改动是否仍是当时版本；是否有未记录的外部改动。

    ``file_changes`` 为 None 时审计整份 ``state.file_changes``；显式传入时只
    审计该切片（“只审本轮改动”），完成判定由 completion 决定，不受切片影响。
    只读：读取 git 状态与必要文件 hash，不执行写操作、不修改工作区。
    """
    root = Path(workspace).resolve()
    findings: list[str] = []
    integrity = "clean"
    coverage = "full"

    changes, files_changed = _recorded_changes(state, file_changes)
    known_paths = [
        str(change.get("path") or "")
        for change in changes
        if change.get("path")
    ]
    before_audit = {
        path: path_metadata(_workspace_path(root, path) or root)
        for path in known_paths
        if _workspace_path(root, path) is not None
    }

    change_findings, suspect_paths, changed_files = _check_recorded_changes(
        root, changes, files_changed,
    )
    findings.extend(change_findings)
    if suspect_paths:
        integrity = "suspect"

    unattributed: list[str] = []
    if baseline is None or not baseline.get("is_git"):
        coverage = "limited"
        findings.append("workspace is not a git repository; out-of-band changes are not detectable")
    else:
        current_is_git, current_paths = _git_status_paths(root)
        if not current_is_git:
            coverage = "limited"
            findings.append("git status became unavailable during audit")
        else:
            known = set(known_paths)
            base_paths = baseline.get("paths") or {}
            for relative in sorted(current_paths):
                if relative in known:
                    continue
                target = _workspace_path(root, relative)
                if target is None:
                    continue
                current = path_metadata(target)
                previous = base_paths.get(relative)
                if previous is None:
                    unattributed.append(relative)
                elif (
                    previous.get("status") != current.get("status")
                    or previous.get("sha256") != current.get("sha256")
                ):
                    unattributed.append(relative)
            if unattributed:
                integrity = "suspect"
                shown = unattributed[:MAX_UNATTRIBUTED_SHOWN]
                findings.append(
                    "workspace changed outside recorded file changes: "
                    + ", ".join(shown)
                    + (f" (+{len(unattributed) - len(shown)} more)" if len(unattributed) > len(shown) else "")
                )

    after_audit = {
        path: path_metadata(_workspace_path(root, path) or root)
        for path in known_paths
        if _workspace_path(root, path) is not None
    }
    if before_audit != after_audit:
        integrity = "violation"
        findings.append("workspace changed while the audit was running")

    if integrity == "violation":
        status = "blocked"
    elif completion.allowed and integrity == "clean":
        status = "complete"
    else:
        status = "incomplete"
    verified = bool(status == "complete" and completion.allowed)
    return AuditReport(
        status=status,
        integrity=integrity,
        verified=verified,
        findings=findings,
        unattributed=unattributed,
        changed_files=changed_files,
        coverage=coverage,
    )
