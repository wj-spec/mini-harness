"""P10：当前会话 rounds 账本只读视图（深链到验证记录）。"""

from __future__ import annotations

from pathlib import Path

from app.session.store import SessionStore

MAX_ROUNDS = 50
MAX_GOAL = 1000


def _field(record: dict, key: str, *, kind: type, default=None):
    value = record.get(key, default)
    if value is None and default is None:
        return None
    if kind is bool:
        if value is None:
            return None
        if not isinstance(value, bool):
            raise ValueError(f"round {key} invalid")
        return value
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"round {key} invalid")
        return value
    if kind is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"round {key} invalid")
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"round {key} invalid")
    return value


def session_rounds(session: SessionStore, *, limit: int = MAX_ROUNDS, workspace: str | Path | None = None) -> dict:
    limit = max(1, min(int(limit), 200))
    records = session.load_rounds()
    skipped = int(getattr(session, "last_rounds_skipped", 0) or 0)
    entries: list[dict] = []
    for record in reversed(records):
        try:
            if not isinstance(record, dict):
                raise ValueError("round record invalid")
            index = _field(record, "round_index", kind=int)
            run_id = _field(record, "run_id", kind=str)
            if not run_id:
                raise ValueError("round run_id invalid")
            goal = _field(record, "goal", kind=str, default="") or ""
            goal_truncated = len(goal) > MAX_GOAL
            status = _field(record, "status", kind=str, default="unknown") or "unknown"
            task_kind = record.get("task_kind")
            stop_reason = record.get("stop_reason")
            entries.append({
                "index": index,
                "run_id": run_id,
                "goal": goal[:MAX_GOAL],
                "goal_truncated": goal_truncated,
                "task_kind": task_kind if isinstance(task_kind, str) else None,
                "status": status,
                "stop_reason": stop_reason if isinstance(stop_reason, str) else None,
                "steps": _field(record, "steps", kind=int, default=0) or 0,
                "cost_usd": _field(record, "cost_usd", kind=float, default=0.0) or 0.0,
                "verified": _field(record, "verified", kind=bool),
                "saved_at": record.get("saved_at") if isinstance(record.get("saved_at"), str) else None,
            })
        except (TypeError, ValueError):
            skipped += 1
    total = len(records)
    truncated = len(entries) > limit
    return {
        "workspace": str(Path(workspace).resolve()) if workspace is not None else "",
        "session_id": session.id,
        "purpose": session.purpose,
        "total": total,
        "skipped": skipped,
        "truncated": truncated,
        "rounds": entries[:limit],
    }
