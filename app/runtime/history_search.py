"""P10：跨会话本地全文检索（只读、用途隔离）。"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from app.runtime.session_navigation import session_identity
from app.session.store import SessionStore

PAGE = 20
MAX_QUERY = 200
MAX_SNIPPET = 280
MAX_TITLE = 200
MAX_SCAN_MESSAGES = 200_000


def _scope(workspace: Path, purpose: str) -> str:
    return hashlib.sha256(f"{workspace}\0{purpose}".encode()).hexdigest()


def _encode_cursor(scope: str, session_id: str, message_index: int) -> str:
    raw = json.dumps([scope, session_id, message_index]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: Any, scope: str) -> tuple[str, int]:
    if not isinstance(cursor, str) or not 0 < len(cursor) <= 512:
        raise ValueError("invalid search cursor")
    try:
        decoded = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid search cursor") from exc
    if (not isinstance(decoded, list) or len(decoded) != 3 or decoded[0] != scope
            or not isinstance(decoded[1], str) or not 0 < len(decoded[1]) <= 128
            or isinstance(decoded[2], bool) or not isinstance(decoded[2], int) or decoded[2] < 0):
        raise ValueError("search cursor belongs to another scope or is invalid")
    return decoded[1], decoded[2]


def _snippet(content: str, needle: str) -> str:
    compact = " ".join(content.split())
    lowered = compact.lower()
    at = lowered.find(needle)
    if at < 0:
        return compact[:MAX_SNIPPET]
    start = max(0, at - 60)
    end = min(len(compact), at + len(needle) + 160)
    text = compact[start:end]
    if start:
        text = "…" + text
    if end < len(compact):
        text += "…"
    return text[:MAX_SNIPPET]


def _session_order(store: SessionStore) -> tuple[str, str]:
    try:
        metadata = json.loads(store.meta_path.read_text(encoding="utf-8"))
        created = metadata.get("created_at") if isinstance(metadata, dict) else None
        return (created if isinstance(created, str) else "", store.id)
    except (OSError, json.JSONDecodeError):
        return ("", store.id)


def _candidates(workspace: Path, purpose: str) -> list[SessionStore]:
    base = workspace / ".vibe/sessions"
    if base.is_symlink() or not base.resolve().is_relative_to(workspace):
        raise OSError("会话目录范围不可核对")
    stores: list[SessionStore] = []
    for path in base.iterdir() if base.is_dir() else []:
        try:
            if path.is_symlink() or not path.is_dir():
                continue
            candidate = SessionStore(path)
            if candidate.purpose in {"user", "test"} and candidate.purpose != purpose:
                continue
            store = SessionStore.open_existing(workspace, path.name, purpose=purpose)
            if store is None:
                continue
            stores.append(store)
        except (OSError, ValueError, TypeError):
            continue
    stores.sort(key=_session_order, reverse=True)
    return stores


def history_search(current: SessionStore, workspace: str | Path, args: object) -> dict:
    """在当前工作区、当前用途的会话消息中做大小写不敏感子串检索。"""
    if not isinstance(args, dict) or set(args) - {"query", "cursor"}:
        raise ValueError("search accepts only query and cursor")
    query = args.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY:
        raise ValueError(f"search query must be a nonempty string of at most {MAX_QUERY} characters")
    query = query.strip()
    needle = query.lower()
    purpose = current.purpose
    if purpose not in {"user", "test"}:
        raise ValueError("当前会话用途无法核对")
    workspace = Path(workspace).resolve()
    scope = _scope(workspace, purpose)
    after = None
    if "cursor" in args:
        after = _decode_cursor(args["cursor"], scope)

    stores = _candidates(workspace, purpose)
    if after is not None:
        position = next((index for index, store in enumerate(stores) if store.id == after[0]), None)
        if position is None:
            raise ValueError("search cursor 已失效；请重新搜索")
    else:
        position = 0

    matches: list[dict[str, Any]] = []
    skipped = 0
    scanned = 0
    truncated = False
    next_cursor: str | None = None
    stop = False
    for store in stores[position:]:
        if stop:
            break
        try:
            identity = session_identity(store, workspace, purpose)
        except (OSError, ValueError, TypeError):
            skipped += 1
            continue
        order = _session_order(store)
        title = ""
        message_index = -1
        try:
            with store.events_path.open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        skipped += 1
                        continue
                    if not isinstance(event, dict) or event.get("type") != "message":
                        continue
                    message = event.get("message")
                    if not isinstance(message, dict):
                        skipped += 1
                        continue
                    message_index += 1
                    scanned += 1
                    if scanned > MAX_SCAN_MESSAGES:
                        truncated = True
                        stop = True
                        break
                    role = message.get("role")
                    role = role if role in {"user", "assistant", "tool"} else "assistant"
                    content = message.get("content")
                    content = content if isinstance(content, str) else ""
                    if role == "user" and not title and content.strip():
                        title = content.strip()[:MAX_TITLE]
                    if after is not None and store.id == after[0] and message_index <= after[1]:
                        continue
                    if needle not in content.lower():
                        continue
                    matches.append({
                        "session_id": store.id,
                        "identity": identity,
                        "title": title,
                        "updated_at": order[0],
                        "role": role,
                        "snippet": _snippet(content, needle),
                        "ts": (event.get("ts") if isinstance(event.get("ts"), str) else None),
                        "message_index": message_index,
                    })
                    if len(matches) > PAGE:
                        matches = matches[:PAGE]
                        last = matches[-1]
                        next_cursor = _encode_cursor(scope, last["session_id"], last["message_index"])
                        stop = True
                        break
        except (OSError, UnicodeError):
            skipped += 1
    return {
        "workspace": str(workspace),
        "session_id": current.id,
        "purpose": purpose,
        "query": query,
        "matches": matches,
        "next_cursor": next_cursor,
        "skipped": skipped,
        "truncated": truncated,
    }
