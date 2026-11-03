from __future__ import annotations

from datetime import datetime, timezone

ROUND_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _completion_dict(completion) -> dict | None:
    if completion is None:
        return None
    if isinstance(completion, dict):
        return dict(completion)
    to_dict = getattr(completion, "to_dict", None)
    return to_dict() if callable(to_dict) else None


def build_round_record(
    *,
    run_id: str,
    goal: str,
    task_kind: str,
    status: str,
    stop_reason: str,
    steps: int,
    usage: dict | None,
    cost_usd: float,
    started_at: str,
    state,
    completion=None,
    audit: dict | None = None,
    verified: bool | None = None,
    receipt: str = "",
    start_mutation_seq: int = 0,
    start_file_changes: int = 0,
    start_verifications: int = 0,
    start_files_changed: int = 0,
    start_command_results: int = 0,
) -> dict:
    """一次 run 结束时的紧凑轮次记录。

    只保存可核对的证据引用（路径、前后 hash、命令结果摘要），
    不保存文件正文与完整命令输出；正文由 git / .vibe/backups 持有。
    """
    return {
        "schema_version": ROUND_SCHEMA_VERSION,
        "run_id": str(run_id or ""),
        "goal": str(goal or ""),
        "task_kind": str(task_kind or "qa"),
        "status": str(status or ""),
        "stop_reason": str(stop_reason or ""),
        "steps": int(steps or 0),
        "usage": dict(usage or {}),
        "cost_usd": float(cost_usd or 0.0),
        "started_at": str(started_at or ""),
        "ended_at": _now(),
        "verified": verified,
        "completion": _completion_dict(completion),
        "audit": dict(audit) if isinstance(audit, dict) else None,
        "receipt": str(receipt or ""),
        "task_state": {
            "goal": str(getattr(state, "goal", "") or ""),
            "plan": list(getattr(state, "plan", []) or []),
            "done": list(getattr(state, "done", []) or []),
            "open": list(getattr(state, "open", []) or []),
            "files_changed": list(getattr(state, "files_changed", []) or [])[max(0, int(start_files_changed)):],
            "tests_run": list(getattr(state, "tests_run", []) or []),
            "notes": list(getattr(state, "notes", []) or [])[-20:],
            "constraints": list(getattr(state, "constraints", []) or []),
            "file_changes": list(getattr(state, "file_changes", []) or [])[max(0, int(start_file_changes)):],
            "verifications": list(getattr(state, "verifications", []) or [])[max(0, int(start_verifications)):],
            "mutation_seq": max(0, int(getattr(state, "mutation_seq", 0) or 0) - max(0, int(start_mutation_seq))),
            "last_verification_seq": getattr(state, "last_verification_seq", None),
            "last_verification_ok": getattr(state, "last_verification_ok", None),
            "command_results_count": max(
                0, len(getattr(state, "command_results", []) or []) - max(0, int(start_command_results))
            ),
        },
    }
