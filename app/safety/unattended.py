"""无人值守运行 profile（09 P0）：默认保守，只读/工作区写可用，执行/网络/MCP/远端默认拒绝。

profile 文件：<workspace>/.vibe/unattended.json；缺省使用 conservative profile。
只有本机用户能修改该文件；worker 启动时读取并写入任务事件。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

PROFILE_VERSION = 1
PROFILE_PATH = Path(".vibe") / "unattended.json"

READ_TOOLS = frozenset({
    "read_file", "list_files", "list_dir", "grep", "glob", "read", "ls",
})
WRITE_TOOLS = frozenset({"write_file", "edit_file", "apply_patch", "write", "edit", "patch"})
EXEC_TOOLS = frozenset({"bash", "run_command", "shell", "exec"})
NETWORK_TOOLS = frozenset({"web_search", "web_fetch", "http_request", "network"})
REMOTE_TOOLS = frozenset({"browser", "browser_action", "computer_use", "remote_action"})


class UnattendedProfileError(ValueError):
    pass


@dataclass(frozen=True)
class UnattendedProfile:
    schema_version: int = PROFILE_VERSION
    profile_id: str = "unattended-conservative"
    allow_read: bool = True
    allow_write: bool = True
    allow_exec: bool = False
    allow_network: bool = False
    allow_mcp: bool = False
    allow_remote_action: bool = False
    require_worktree: bool = True
    approval_mode: str = "queue"

    def allows_tool(self, tool: str, *, worktree: bool = True) -> bool:
        name = str(tool or "").strip().lower()
        if name.startswith("mcp_") or name.startswith("mcp."):
            return self.allow_mcp
        if name in REMOTE_TOOLS or name.startswith("browser_"):
            return self.allow_remote_action
        if name in NETWORK_TOOLS or name.startswith("web_"):
            return self.allow_network
        if name in EXEC_TOOLS:
            return self.allow_exec
        if name in WRITE_TOOLS:
            return self.allow_write and (worktree or not self.require_worktree)
        if name in READ_TOOLS:
            return self.allow_read
        # 未知工具默认拒绝：保守 profile 不为新工具自动开口。
        return False

    def denial_reason(self, tool: str, *, worktree: bool = True) -> str | None:
        if self.allows_tool(tool, worktree=worktree):
            return None
        name = str(tool or "").strip().lower()
        if name.startswith("mcp_") or name.startswith("mcp."):
            return "mcp disabled by unattended profile"
        if name in REMOTE_TOOLS or name.startswith("browser_"):
            return "remote action disabled by unattended profile"
        if name in NETWORK_TOOLS or name.startswith("web_"):
            return "network disabled by unattended profile"
        if name in EXEC_TOOLS:
            return "exec disabled by unattended profile (set allow_exec=true explicitly)"
        if name in WRITE_TOOLS:
            return "worktree is required for background writes"
        return "tool disabled by unattended profile"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def hash_payload(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)


RELAXABLE_FLAGS = ("allow_exec", "allow_network", "allow_mcp", "allow_remote_action")


def relaxed_settings(profile: UnattendedProfile) -> list[str]:
    """相对 conservative 默认值被放宽的项（用于启动审计/告警）。"""
    defaults = UnattendedProfile()
    relaxed: list[str] = []
    for name in RELAXABLE_FLAGS:
        if getattr(profile, name) and not getattr(defaults, name):
            relaxed.append(name)
    if not profile.require_worktree:
        relaxed.append("require_worktree=false")
    if profile.approval_mode != defaults.approval_mode:
        relaxed.append(f"approval_mode={profile.approval_mode}")
    return relaxed


def profile_hash(profile: UnattendedProfile) -> str:
    return hashlib.sha256(profile.hash_payload().encode("utf-8")).hexdigest()


def _known_fields() -> set[str]:
    return {item.name for item in fields(UnattendedProfile)}


def load_profile(workspace: str | Path) -> UnattendedProfile:
    path = Path(workspace) / PROFILE_PATH
    override = os.getenv("VIBE_UNATTENDED_PROFILE")
    if override:
        path = Path(override).expanduser()
    if not path.exists():
        return UnattendedProfile()
    if path.is_symlink():
        raise UnattendedProfileError("unattended profile must not be a symlink")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UnattendedProfileError(f"invalid unattended profile: {exc}") from exc
    if not isinstance(raw, dict):
        raise UnattendedProfileError("unattended profile must be an object")
    unknown = set(raw) - _known_fields()
    if unknown:
        raise UnattendedProfileError(f"unknown unattended profile keys: {sorted(unknown)}")
    if raw.get("schema_version", PROFILE_VERSION) != PROFILE_VERSION:
        raise UnattendedProfileError("unsupported unattended profile schema_version")
    try:
        return UnattendedProfile(**raw)
    except TypeError as exc:
        raise UnattendedProfileError(str(exc)) from exc
