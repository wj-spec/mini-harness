"""本地通知通道（09 P1）：后台任务生命周期写入 .vibe/notifications.jsonl。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NOTIFY_FILE = "notifications.jsonl"


def notify_path(workspace: str | Path) -> Path:
    return Path(workspace) / ".vibe" / NOTIFY_FILE


def notify(workspace: str | Path, kind: str, **data: Any) -> bool:
    if not isinstance(kind, str) or not kind.strip():
        return False
    path = notify_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "kind": kind.strip(),
        "ts": datetime.now(timezone.utc).isoformat(),
        **data,
    }
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def read_notifications(workspace: str | Path) -> list[dict[str, Any]]:
    path = notify_path(workspace)
    if not path.is_file():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result
