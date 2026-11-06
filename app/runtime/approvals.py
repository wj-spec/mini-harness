"""审批收件箱（spec: docs/specs/approval-inbox.md P0）。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

APPROVAL_FILE = "approvals.jsonl"
SCHEMA_VERSION = 1
PENDING_STATUSES = frozenset({"pending"})
TERMINAL_STATUSES = frozenset({"resolved", "expired", "cancelled"})
MAX_PREVIEW_BYTES = 8 * 1024

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(VIBE_API_KEY|OPENAI_API_KEY|DEEPSEEK_API_KEY|AWS_[A-Z0-9_]*|GITHUB_TOKEN)\b\s*[=:]\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{8,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
)


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def redact_preview(text: str, *, home: str | None = None) -> str:
    """确定性脱敏：不依赖模型；只做已知模式替换。"""
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    if home:
        result = result.replace(home, "~")
    return result


def preview_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ApprovalError(Exception):
    pass


class ApprovalNotFound(ApprovalError):
    pass


class ApprovalMismatch(ApprovalError):
    pass


class ApprovalInbox:
    def __init__(self, session_root: str | Path):
        self.root = Path(session_root)
        self.tasks_root = self.root / "tasks"

    def path_for(self, task_id: str) -> Path:
        from app.runtime.tasks import TaskStore
        return TaskStore(self.root).task_dir(task_id) / APPROVAL_FILE

    def submit(
        self, task_id: str, *,
        approval_id: str | None = None,
        tool: str,
        tier: str | None = None,
        decisions: list[str] | None = None,
        requester: str = "parent",
        preview: str = "",
        preview_ref: str | None = None,
        expires_at: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        subagent_id: str | None = None,
        delegation_id: str | None = None,
        policy_hash: str | None = None,
        project_policy_hash: str | None = None,
        include_preview: bool = False,
    ) -> dict[str, Any]:
        approval_id = approval_id or f"a-{uuid.uuid4().hex[:12]}"
        existing = self._latest()
        if approval_id in existing and existing[approval_id].get("status") == "pending":
            raise ApprovalMismatch(f"duplicate pending approval_id: {approval_id}")
        try:
            max_pending = int(os.getenv("VIBE_APPROVAL_MAX_PENDING", "100"))
        except ValueError:
            max_pending = 100
        max_pending = max(1, min(max_pending, 1000))
        if len(self.pending(task_id)) >= max_pending:
            raise ApprovalError(f"approval inbox full for task {task_id}")
        body = None
        if include_preview:
            body = redact_preview(preview, home=str(Path.home()))[:MAX_PREVIEW_BYTES]
        entry = {
            "schema_version": SCHEMA_VERSION,
            "type": "requested",
            "approval_id": approval_id,
            "task_id": task_id,
            "session_id": session_id,
            "tool": tool,
            "tier": tier,
            "decisions": list(decisions or []),
            "requester": requester,
            "preview_hash": preview_hash(preview),
            "preview_ref": preview_ref,
            "preview_redacted": body,
            "expires_at": expires_at,
            "agent_id": agent_id,
            "subagent_id": subagent_id,
            "delegation_id": delegation_id,
            "policy_hash": policy_hash,
            "project_policy_hash": project_policy_hash,
            "created_at": _now(),
        }
        self._append(entry)
        return entry

    def items(self, task_id: str | None = None) -> list[dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        for raw in self._read(task_id):
            approval_id = raw.get("approval_id")
            if not isinstance(approval_id, str):
                continue
            if raw.get("type") == "requested":
                item = dict(raw)
                item["status"] = "pending"
                states[approval_id] = item
            elif raw.get("type") in {"resolved", "expired", "cancelled"}:
                item = states.get(approval_id) or {"approval_id": approval_id, "task_id": raw.get("task_id")}
                item = {**item, **raw}
                item["status"] = raw["type"]
                states[approval_id] = item
        return list(states.values())

    def pending(self, task_id: str | None = None) -> list[dict[str, Any]]:
        return [item for item in self.items(task_id) if item.get("status") == "pending"]

    def resolve(
        self, task_id: str, approval_id: str, *,
        decision: str, scope: str = "once", decided_by: str = "user",
        decider: str = "user",
    ) -> dict[str, Any]:
        item = next((entry for entry in self.items(task_id) if entry.get("approval_id") == approval_id), None)
        if item is None:
            raise ApprovalNotFound(approval_id)
        if item.get("status") != "pending":
            raise ApprovalMismatch(f"approval already {item.get('status')}: {approval_id}")
        event = {
            "schema_version": SCHEMA_VERSION, "type": "resolved",
            "approval_id": approval_id, "task_id": task_id,
            "decision": decision, "scope": scope,
            "decided_by": decided_by, "decider": decider,
            "decided_at": _now(),
        }
        self._append(event)
        return {**item, **event, "status": "resolved"}

    def expire(self, task_id: str, *, reason: str = "approval_timeout") -> list[dict[str, Any]]:
        expired: list[dict[str, Any]] = []
        for item in self.pending(task_id):
            event = {
                "schema_version": SCHEMA_VERSION, "type": "expired",
                "approval_id": item["approval_id"], "task_id": task_id,
                "decided_by": "system", "decider": "auto",
                "terminal_reason": reason, "decided_at": _now(),
            }
            self._append(event)
            expired.append({**item, **event, "status": "expired"})
        return expired

    # ---- 内部 ----

    def _append(self, entry: dict[str, Any]) -> None:
        task_id = entry.get("task_id")
        if not isinstance(task_id, str):
            raise ApprovalError("approval entry requires task_id")
        path = self.path_for(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()

    def _read(self, task_id: str | None = None) -> list[dict[str, Any]]:
        if task_id is not None:
            paths = [self.path_for(task_id)]
        else:
            paths = sorted(self.tasks_root.glob(f"*/{APPROVAL_FILE}"))
        result: list[dict[str, Any]] = []
        for path in paths:
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    result.append(value)
        return result

    def _latest(self) -> dict[str, dict[str, Any]]:
        return {item["approval_id"]: item for item in self.items() if item.get("approval_id")}
