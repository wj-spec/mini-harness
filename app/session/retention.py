"""会话日志保留：显式轮转 trace，不丢失事件/会话恢复所需的 events.jsonl。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_BACKUPS = 3
# events.jsonl 是会话恢复的权威来源，不自动轮转；只做软上限告警。
DEFAULT_EVENTS_MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class RetentionPolicy:
    max_bytes: int = DEFAULT_MAX_BYTES
    backups: int = DEFAULT_BACKUPS


def rotate_file(path: Path, *, max_bytes: int, backups: int) -> bool:
    if backups < 1 or max_bytes < 1 or not path.is_file() or path.is_symlink():
        return False
    try:
        if path.stat().st_size <= max_bytes:
            return False
    except OSError:
        return False
    for index in range(backups - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        target = path.with_name(f"{path.name}.{index + 1}")
        try:
            if source.exists():
                source.replace(target)
        except OSError:
            return False
    try:
        path.replace(path.with_name(path.name + ".1"))
        return True
    except OSError:
        return False


def events_soft_limit() -> int:
    try:
        value = int(os.getenv("VIBE_EVENTS_MAX_BYTES", str(DEFAULT_EVENTS_MAX_BYTES)))
    except ValueError:
        value = DEFAULT_EVENTS_MAX_BYTES
    return max(0, value)


def session_events_sizes(workspace: str | Path) -> list[dict[str, Any]]:
    """只读列出各 session 的 events.jsonl 大小。"""
    root = Path(workspace) / ".vibe" / "sessions"
    items: list[dict[str, Any]] = []
    if not root.is_dir():
        return items
    for session in sorted(root.iterdir()):
        if not session.is_dir() or session.is_symlink():
            continue
        path = session / "events.jsonl"
        try:
            size = path.stat().st_size
        except OSError:
            continue
        items.append({"session": session.name, "path": str(path), "bytes": size})
    return items


def oversized_session_events(
    workspace: str | Path, *, limit: int | None = None,
) -> list[dict[str, Any]]:
    """events.jsonl 超过软上限的 session（只报告，不轮转）。"""
    soft = events_soft_limit() if limit is None else limit
    if soft <= 0:
        return []
    return [item for item in session_events_sizes(workspace) if item["bytes"] > soft]


def cleanup_workspace_logs(
    workspace: str | Path, *, policy: RetentionPolicy | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    policy = policy or RetentionPolicy()
    root = Path(workspace) / ".vibe" / "sessions"
    rotated: list[dict[str, Any]] = []
    candidates: list[Path] = []
    if root.is_dir():
        for session in sorted(root.iterdir()):
            if not session.is_dir() or session.is_symlink():
                continue
            trace = session / "trace.jsonl"
            candidates.append(trace)
    for path in candidates:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size <= policy.max_bytes:
            continue
        entry = {"path": str(path), "bytes": size}
        if dry_run:
            entry["rotated"] = False
            rotated.append(entry)
            continue
        entry["rotated"] = rotate_file(path, max_bytes=policy.max_bytes, backups=policy.backups)
        rotated.append(entry)
    events = session_events_sizes(workspace)
    soft = events_soft_limit()
    result = {
        "workspace": str(Path(workspace)),
        "max_bytes": policy.max_bytes,
        "backups": policy.backups,
        "dry_run": dry_run,
        "candidates": len(candidates),
        "rotated": rotated,
        "events_soft_limit": soft,
        "events": len(events),
        "oversized_events": [
            item for item in events if soft > 0 and item["bytes"] > soft
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if not dry_run:
        audit = Path(workspace) / ".vibe" / "retention.jsonl"
        audit.parent.mkdir(parents=True, exist_ok=True)
        with audit.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
    return result
