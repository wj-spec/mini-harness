"""任务/目标/段预算账本（spec: docs/specs/budget-ledger.md P0）。"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

LEDGER_FILE = "budget.jsonl"
SCHEMA_VERSION = 1
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class BudgetError(Exception):
    pass


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _number(value: Any) -> float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


class BudgetLedger:
    def __init__(self, session_root: str | Path):
        self.root = Path(session_root)
        self.path = self.root / LEDGER_FILE

    def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        payload = dict(entry)
        payload.setdefault("schema_version", SCHEMA_VERSION)
        payload.setdefault("entry_id", f"b-{int(time.time() * 1_000_000)}-{uuid.uuid4().hex[:6]}")
        payload.setdefault("recorded_at", _now())
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
                handle.flush()
        except OSError as exc:
            raise BudgetError(str(exc)) from exc
        return payload

    def entries(self, *, task_id: str | None = None) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        result: list[dict[str, Any]] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue
            if task_id is not None and value.get("task_id") != task_id:
                continue
            result.append(value)
        return result

    def on_trace(
        self, *, task_id: str, goal_id: str | None, parent_id: str | None,
        node_kind: str, segment: int, run_id: str, event: dict[str, Any],
    ) -> dict[str, Any] | None:
        kind = event.get("type")
        base = {
            "task_id": task_id, "goal_id": goal_id, "parent_id": parent_id,
            "node_kind": node_kind, "segment": segment, "run_id": run_id,
            "source": "trace",
        }
        if kind == "llm_end":
            entry = {
                **base, "entry_kind": "llm",
                "tokens_in": int(_number(event.get("prompt_tokens"))),
                "tokens_out": int(_number(event.get("completion_tokens"))),
                "cost_usd": _number(event.get("cost_usd")),
                "estimated": bool(event.get("usage_estimated", False)),
                "tool_calls": 0, "active_seconds": 0.0,
                "approval_wait_seconds": 0.0,
            }
        elif kind == "tool_end":
            entry = {
                **base, "entry_kind": "tool", "tokens_in": 0, "tokens_out": 0,
                "cost_usd": 0.0, "estimated": False, "tool_calls": 1,
                "active_seconds": 0.0, "approval_wait_seconds": 0.0,
                "tool": event.get("name"),
            }
        elif kind == "approval_wait":
            entry = {
                **base, "entry_kind": "approval_wait", "tokens_in": 0, "tokens_out": 0,
                "cost_usd": 0.0, "estimated": False, "tool_calls": 0,
                "active_seconds": 0.0,
                "approval_wait_seconds": _number(event.get("seconds")),
            }
        elif kind == "run_end":
            entry = {
                **base, "entry_kind": "run_close", "tokens_in": 0, "tokens_out": 0,
                "cost_usd": 0.0, "estimated": bool(event.get("usage_estimated", False)),
                "tool_calls": 0,
                "active_seconds": _number(event.get("duration_ms")) / 1000.0,
                "approval_wait_seconds": 0.0,
                "status": event.get("status"),
                "stop_reason": event.get("stop_reason"),
            }
        else:
            return None
        return self.append(entry)

    def close_segment(
        self, *, task_id: str, goal_id: str | None, parent_id: str | None,
        node_kind: str, segment: int, run_id: str, status: str,
        stop_reason: str | None = None, round_ref: str | None = None,
        verified: bool | None = None,
    ) -> dict[str, Any]:
        return self.append({
            "task_id": task_id, "goal_id": goal_id, "parent_id": parent_id,
            "node_kind": node_kind, "segment": segment, "run_id": run_id,
            "entry_kind": "segment_close", "tokens_in": 0, "tokens_out": 0,
            "cost_usd": 0.0, "estimated": False, "tool_calls": 0,
            "active_seconds": 0.0, "approval_wait_seconds": 0.0,
            "status": status, "stop_reason": stop_reason,
            "round_ref": round_ref, "verified": verified,
            "terminal_status": None, "terminal_reason": stop_reason,
            "source": "runtime",
        })

    def close_task(
        self, *, task_id: str, goal_id: str | None, parent_id: str | None,
        node_kind: str, status: str, reason: str | None = None,
    ) -> dict[str, Any]:
        if status not in TERMINAL_STATUSES:
            raise BudgetError(f"task_close requires terminal status, got {status}")
        return self.append({
            "task_id": task_id, "goal_id": goal_id, "parent_id": parent_id,
            "node_kind": node_kind, "segment": 0, "run_id": None,
            "entry_kind": "task_close", "tokens_in": 0, "tokens_out": 0,
            "cost_usd": 0.0, "estimated": False, "tool_calls": 0,
            "active_seconds": 0.0, "approval_wait_seconds": 0.0,
            "terminal_status": status, "terminal_reason": reason,
            "source": "runtime",
        })

    def totals(
        self, *, task_id: str | None = None, goal_id: str | None = None,
        segment: int | None = None,
    ) -> dict[str, Any]:
        tokens_in = tokens_out = 0
        cost = active = approval = 0.0
        tool_calls = 0
        entries = 0
        for entry in self.entries(task_id=task_id):
            if goal_id is not None and entry.get("goal_id") != goal_id:
                continue
            if segment is not None and entry.get("segment") != segment:
                continue
            if entry.get("entry_kind") not in {"llm", "tool", "approval_wait", "run_close"}:
                continue
            entries += 1
            tokens_in += int(_number(entry.get("tokens_in")))
            tokens_out += int(_number(entry.get("tokens_out")))
            cost += _number(entry.get("cost_usd"))
            active += _number(entry.get("active_seconds"))
            approval += _number(entry.get("approval_wait_seconds"))
            tool_calls += int(_number(entry.get("tool_calls")))
        return {
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "tokens_total": tokens_in + tokens_out, "cost_usd": cost,
            "active_seconds": active, "approval_wait_seconds": approval,
            "tool_calls": tool_calls, "entries": entries,
        }

    def check_start(
        self, task_id: str, *,
        max_tokens: int = 0, max_wall_seconds: float = 0,
        started_at: str | None = None, now: Any = None,
    ) -> dict[str, Any]:
        totals = self.totals(task_id=task_id)
        if max_tokens > 0 and totals["tokens_total"] >= max_tokens:
            return {
                "allowed": False, "reason": "task_token_budget",
                "used_tokens": totals["tokens_total"], "limit_tokens": max_tokens,
                "remaining_tokens": 0,
            }
        if max_wall_seconds > 0 and started_at:
            from datetime import datetime, timezone
            current = now or datetime.now(timezone.utc)
            try:
                started = datetime.fromisoformat(started_at)
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                elapsed = (current - started).total_seconds()
            except (ValueError, TypeError):
                elapsed = 0.0
            if elapsed >= max_wall_seconds:
                return {
                    "allowed": False, "reason": "task_wall_budget",
                    "used_seconds": elapsed, "limit_seconds": max_wall_seconds,
                    "remaining_seconds": 0,
                }
        return {
            "allowed": True, "reason": None,
            "used_tokens": totals["tokens_total"], "limit_tokens": max_tokens,
            "remaining_tokens": max(0, max_tokens - totals["tokens_total"]) if max_tokens else 0,
        }

    def task_terminal(self, task_id: str) -> str | None:
        for entry in reversed(self.entries(task_id=task_id)):
            if entry.get("entry_kind") == "task_close":
                value = entry.get("terminal_status")
                return value if isinstance(value, str) else None
        return None
