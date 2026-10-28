from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from app import permissions
from core.models import ToolCall


@dataclass
class ApprovalDecision:
    allowed: bool
    scope: str  # once | session | workspace | deny


ApprovalHandler = Callable[[ToolCall, str], Awaitable[ApprovalDecision]]


class ApprovalGate:
    """写工具的审批闸；默认 fail closed。"""

    WORKSPACE_TOOLS = frozenset({"write_file", "edit_file", "bash"})

    def __init__(
        self,
        handler: ApprovalHandler | None = None,
        audit_path: str | Path | None = None,
    ):
        self.handler = handler
        self.audit_path = Path(audit_path) if audit_path else None
        self.session_allowed: set[tuple[str | None, str, str]] = set()
        self.workspace_allowed: set[str] = set()
        self.once_only: set[str] = set()
        self._auditors: dict[str, Callable[[ToolCall, str], None]] = {}
        # 由 Agent 注入：把工具 risk 映射成审批层级；缺失时退回旧的名字集合行为。
        self.tier_resolver: Callable[[ToolCall], str] | None = None

    def require_once(self, name: str, audit: Callable[[ToolCall, str], None]) -> None:
        self.once_only.add(name)
        self._auditors[name] = audit

    def _tier(self, call: ToolCall) -> str | None:
        if self.tier_resolver is None:
            return None
        return permissions.tier_for(self.tier_resolver(call))

    def decisions_for(self, call: ToolCall) -> list[str]:
        if call.name in self.once_only:
            return ["once", "deny"]
        tier = self._tier(call)
        if tier is None:
            return ["once", "session", *(["workspace"] if call.name in self.WORKSPACE_TOOLS else []), "deny"]
        return permissions.scopes_for(tier, workspace_eligible=call.name in self.WORKSPACE_TOOLS)

    @staticmethod
    def _workspace(workspace: str | Path | None) -> str | None:
        return str(Path(workspace).resolve()) if workspace is not None else None

    @staticmethod
    def _path(path: str, workspace: str | None) -> str:
        return str((Path(workspace) / path if workspace else Path(path)).resolve())

    def _key(self, call: ToolCall, workspace: str | None) -> tuple[str | None, str, str]:
        if call.name in {"write_file", "edit_file"} and "path" in call.args:
            target = self._path(str(call.args["path"]), workspace)
        elif call.name == "bash" and "command" in call.args:
            target = json.dumps(
                {
                    "command": str(call.args["command"]),
                    "cwd": self._path(str(call.args.get("cwd", ".")), workspace),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        else:
            target = json.dumps(call.args, ensure_ascii=False, sort_keys=True)
        return (workspace, call.name, target)

    def permissions(self, workspace: str | Path | None) -> dict:
        root = self._workspace(workspace)
        return {
            "workspace_allowed": root is not None and root in self.workspace_allowed,
            "session_rules": sum(key[0] == root for key in self.session_allowed),
        }

    def clear(self, workspace: str | Path | None) -> None:
        root = self._workspace(workspace)
        if root is not None:
            self.workspace_allowed.discard(root)
        self.session_allowed = {key for key in self.session_allowed if key[0] != root}

    async def request(
        self, call: ToolCall, preview: str, workspace: str | Path | None = None,
    ) -> ApprovalDecision:
        root = self._workspace(workspace)
        tier = self._tier(call)
        workspace_eligible = call.name in self.WORKSPACE_TOOLS
        workspace_scope = (
            root is not None and workspace_eligible
            and (tier is None or permissions.scope_allowed(
                tier, "workspace", workspace_eligible=workspace_eligible))
        )
        if workspace_scope and root in self.workspace_allowed:
            self._audit(call, "workspace_allow", preview, root)
            return ApprovalDecision(True, "workspace")

        key = self._key(call, root)
        if call.name not in self.once_only and key in self.session_allowed:
            self._audit(call, "session_allow", preview, root)
            return ApprovalDecision(True, "session")

        if self.handler is None:
            self._audit(call, "deny_no_handler", preview, root)
            return ApprovalDecision(False, "deny")

        try:
            decision = await self.handler(call, preview)
        except asyncio.CancelledError:
            self._audit(call, "cancelled", preview, root)
            raise
        if decision.allowed:
            if call.name in self.once_only and decision.scope != "once":
                decision = ApprovalDecision(False, "deny")
            elif decision.scope == "workspace":
                if not workspace_scope:
                    self._audit(call, "deny_workspace_scope", preview, root)
                    return ApprovalDecision(False, "deny")
                self.workspace_allowed.add(root)
            elif decision.scope == "session":
                if tier is not None and not permissions.scope_allowed(
                        tier, "session", workspace_eligible=workspace_eligible):
                    self._audit(call, "deny_scope_not_allowed", preview, root)
                    return ApprovalDecision(False, "deny")
                self.session_allowed.add(key)
            elif decision.scope != "once":
                self._audit(call, "deny_scope_not_allowed", preview, root)
                return ApprovalDecision(False, "deny")
        self._audit(call, decision.scope if decision.allowed else "deny", preview, root)
        return decision

    def _audit(
        self, call: ToolCall, decision: str, preview: str, workspace: str | None,
    ) -> None:
        if call.name in self._auditors:
            self._auditors[call.name](call, decision)
        if self.audit_path is None:
            return
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        args = json.dumps(call.args, ensure_ascii=False, sort_keys=True)
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": call.name,
            "workspace": workspace,
            "args_hash": hashlib.sha256(args.encode("utf-8")).hexdigest()[:16],
            "preview_hash": hashlib.sha256(preview.encode("utf-8")).hexdigest()[:16],
            "decision": decision,
        }
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
