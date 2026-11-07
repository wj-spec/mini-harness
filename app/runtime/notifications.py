"""本地通知通道（09 P1）：后台任务生命周期写入 .vibe/notifications.jsonl。"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

NOTIFY_FILE = "notifications.jsonl"
WEBHOOK_ENV = "VIBE_NOTIFY_WEBHOOK"
WEBHOOK_KINDS_ENV = "VIBE_NOTIFY_WEBHOOK_KINDS"
WEBHOOK_TIMEOUT_ENV = "VIBE_NOTIFY_WEBHOOK_TIMEOUT_SECONDS"
DEFAULT_WEBHOOK_TIMEOUT = 3.0
MAX_WEBHOOK_BYTES = 64 * 1024


def webhook_url_for(kind: str) -> str | None:
    """外部告警 webhook 配置：只在显式 URL + kind 匹配时返回。"""
    url = (os.getenv(WEBHOOK_ENV) or "").strip()
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    raw_kinds = (os.getenv(WEBHOOK_KINDS_ENV) or "").strip()
    if raw_kinds:
        allowed = {item.strip() for item in raw_kinds.split(",") if item.strip()}
        if "*" not in allowed and kind not in allowed:
            return None
    return url


def _webhook_timeout() -> float:
    try:
        value = float(os.getenv(WEBHOOK_TIMEOUT_ENV, str(DEFAULT_WEBHOOK_TIMEOUT)))
    except ValueError:
        value = DEFAULT_WEBHOOK_TIMEOUT
    return max(0.1, min(value, 30.0))


def post_webhook(url: str, payload: dict[str, Any]) -> bool:
    """POST 一条通知到外部 webhook；失败静默（不能影响主流程）。测试可替换。"""
    try:
        import httpx
        httpx.post(url, json=payload, timeout=_webhook_timeout())
    except Exception:
        return False
    return True


def _dispatch_webhook(record: dict[str, Any]) -> None:
    kind = str(record.get("kind") or "")
    url = webhook_url_for(kind)
    if url is None:
        return
    payload = dict(record)
    try:
        encoded = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return
    if len(encoded.encode("utf-8")) > MAX_WEBHOOK_BYTES:
        payload = {
            "schema_version": record.get("schema_version", 1),
            "kind": kind, "ts": record.get("ts"), "truncated": True,
        }
    thread = threading.Thread(target=post_webhook, args=(url, payload), daemon=True)
    thread.start()


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
        max_bytes = int(os.getenv("VIBE_NOTIFICATIONS_MAX_BYTES", str(8 * 1024 * 1024)))
        if path.is_file() and path.stat().st_size >= max_bytes:
            backup = path.with_name(path.name + ".1")
            backup.unlink(missing_ok=True)
            path.replace(backup)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        _dispatch_webhook(record)
        return True
    except OSError:
        return False


def read_notifications(
    workspace: str | Path, *, stats: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    path = notify_path(workspace)
    if not path.is_file():
        return []
    result = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        if stats is not None:
            stats["skipped"] = stats.get("skipped", 0) + 1
        return []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            if stats is not None:
                stats["skipped"] = stats.get("skipped", 0) + 1
            continue
        if isinstance(item, dict):
            result.append(item)
        elif stats is not None:
            stats["skipped"] = stats.get("skipped", 0) + 1
    return result


def workspace_from_task_dir(task_dir: str | Path) -> Path:
    """从任务目录反推工作区：.../.vibe/sessions/<sid>/tasks/<tid>。"""
    path = Path(task_dir)
    for candidate in (path, *path.parents):
        if candidate.name == ".vibe":
            return candidate.parent
    return path.parent
