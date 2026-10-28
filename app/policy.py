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
        # 由 Agent 注入：用户/项目策略覆盖（可为空）。
        self.policy = None

    def require_once(self, name: str, audit: Callable[[ToolCall, str], None]) -> None:
        self.once_only.add(name)
        self._auditors[name] = audit

    def _tier(self, call: ToolCall) -> str | None:
        if self.tier_resolver is None:
            return None
        return permissions.tier_for(self.tier_resolver(call))

    def _policy_outcome(self, call: ToolCall, tier: str | None):
        if self.policy is None or tier is None:
            return None
        return self.policy.evaluate(call, tier=tier)

    def decisions_for(self, call: ToolCall) -> list[str]:
        tier = self._tier(call)
        outcome = self._policy_outcome(call, tier)
        if outcome is not None:
            if outcome.decision == "deny":
                return ["deny"]
            if outcome.decision in {"allow", "once"}:
                return ["once", "deny"]
        if call.name in self.once_only:
            return ["once", "deny"]
        if tier is None:
            base = ["once", "session", *(["workspace"] if call.name in self.WORKSPACE_TOOLS else []), "deny"]
        else:
            base = permissions.scopes_for(tier, workspace_eligible=call.name in self.WORKSPACE_TOOLS)
        if outcome is not None and outcome.scopes is not None:
            allowed = set(outcome.scopes) | {"deny"}
            base = [scope for scope in base if scope in allowed]
        return base

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
        outcome = self._policy_outcome(call, tier)
        force_once = outcome is not None and outcome.decision == "once"
        allowed_scopes = set(outcome.scopes) if outcome is not None and outcome.scopes else None
        if outcome is not None:
            if outcome.decision == "deny":
                self._audit(call, "deny_policy", preview, root)
                return ApprovalDecision(False, "deny")
            if outcome.decision == "allow":
                if tier == "remote_action":
                    self._audit(call, "deny_policy_floor", preview, root)
                    return ApprovalDecision(False, "deny")
                self._audit(call, "policy_allow", preview, root)
                return ApprovalDecision(True, "policy")
        workspace_eligible = call.name in self.WORKSPACE_TOOLS
        workspace_scope = (
            root is not None and workspace_eligible and not force_once
            and (allowed_scopes is None or "workspace" in allowed_scopes)
            and (tier is None or permissions.scope_allowed(
                tier, "workspace", workspace_eligible=workspace_eligible))
        )
        if workspace_scope and root in self.workspace_allowed:
            self._audit(call, "workspace_allow", preview, root)
            return ApprovalDecision(True, "workspace")

        key = self._key(call, root)
        session_granted = (
            not force_once and call.name not in self.once_only and key in self.session_allowed
            and (allowed_scopes is None or "session" in allowed_scopes)
        )
        if session_granted:
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
            if force_once and decision.scope != "once":
                self._audit(call, "deny_policy_once", preview, root)
                return ApprovalDecision(False, "deny")
            if call.name in self.once_only and decision.scope != "once":
                decision = ApprovalDecision(False, "deny")
            elif decision.scope == "workspace":
                if not workspace_scope:
                    self._audit(call, "deny_workspace_scope", preview, root)
                    return ApprovalDecision(False, "deny")
                self.workspace_allowed.add(root)
            elif decision.scope == "session":
                if allowed_scopes is not None and "session" not in allowed_scopes:
                    self._audit(call, "deny_policy_scope", preview, root)
                    return ApprovalDecision(False, "deny")
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
