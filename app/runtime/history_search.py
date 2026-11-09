"""P10：跨会话本地全文检索（只读、用途隔离、进程内增量索引）。"""

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
MAX_INDEX_MESSAGES = 200_000

_STATS = {"index_builds": 0, "session_reloads": 0, "cache_hits": 0}
_CACHE: dict[tuple[str, str], "_Index"] = {}


def search_stats() -> dict:
    """索引命中/重建计数，供性能与增量回归核对。"""
    return dict(_STATS)


def clear_history_search_cache() -> None:
    _CACHE.clear()
    for key in _STATS:
        _STATS[key] = 0


class _SessionEntry:
    __slots__ = ("session_id", "identity", "title", "created", "signature", "messages", "corrupt", "error")

    def __init__(self) -> None:
        self.session_id = ""
        self.identity = ""
        self.title = ""
        self.created = ""
        self.signature: tuple | None = None
        self.messages: list[dict] = []
        self.corrupt = 0
        self.error: str | None = None


class _Index:
    __slots__ = ("workspace", "purpose", "sessions", "partial")

    def __init__(self, workspace: Path, purpose: str) -> None:
        self.workspace = workspace
        self.purpose = purpose
        self.sessions: list[_SessionEntry] = []
        self.partial = False


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


def _created_at(store: SessionStore) -> str:
    try:
        metadata = json.loads(store.meta_path.read_text(encoding="utf-8"))
        created = metadata.get("created_at") if isinstance(metadata, dict) else None
        return created if isinstance(created, str) else ""
    except (OSError, json.JSONDecodeError):
        return ""


def _signature(store: SessionStore) -> tuple | None:
    """事件与元数据的身份签名；任何变化都会触发该会话重建索引。"""
    try:
        events = store.events_path.stat()
        meta = store.meta_path.stat()
        root = store.root.stat()
        return (root.st_ino, events.st_ino, events.st_size, events.st_mtime_ns,
                meta.st_ino, meta.st_size, meta.st_mtime_ns)
    except OSError:
        return None


def _load_entry(store: SessionStore, workspace: Path, purpose: str) -> _SessionEntry:
    entry = _SessionEntry()
    entry.session_id = store.id
    entry.identity = session_identity(store, workspace, purpose)
    entry.created = _created_at(store)
    entry.signature = _signature(store)
    with store.events_path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                entry.corrupt += 1
                continue
            if not isinstance(event, dict) or event.get("type") != "message":
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                entry.corrupt += 1
                continue
            role = message.get("role")
            role = role if role in {"user", "assistant", "tool"} else "assistant"
            content = message.get("content")
            content = content if isinstance(content, str) else ""
            if role == "user" and not entry.title and content.strip():
                entry.title = content.strip()[:MAX_TITLE]
            entry.messages.append({
                "index": len(entry.messages),
                "role": role,
                "content": content,
                "ts": event.get("ts") if isinstance(event.get("ts"), str) else None,
            })
    return entry


def _refresh(index: _Index) -> None:
    base = index.workspace / ".vibe/sessions"
    if base.is_symlink() or not base.resolve().is_relative_to(index.workspace):
        raise OSError("会话目录范围不可核对")
    existing = {entry.session_id: entry for entry in index.sessions}
    sessions: list[_SessionEntry] = []
    partial = False
    for path in base.iterdir() if base.is_dir() else []:
        try:
            if path.is_symlink() or not path.is_dir():
                continue
            candidate = SessionStore(path)
            if candidate.purpose in {"user", "test"} and candidate.purpose != index.purpose:
                continue
            store = SessionStore.open_existing(index.workspace, path.name, purpose=index.purpose)
            if store is None:
                continue
        except (OSError, ValueError, TypeError):
            continue
        signature = _signature(store)
        if signature is None:
            continue
        cached = existing.get(store.id)
        if cached is not None and cached.signature == signature:
            sessions.append(cached)
            continue
        try:
            sessions.append(_load_entry(store, index.workspace, index.purpose))
            _STATS["session_reloads"] += 1
        except (OSError, ValueError, TypeError):
            failed = _SessionEntry()
            failed.session_id = store.id
            failed.error = "会话记录暂不可读"
            sessions.append(failed)
    sessions.sort(key=lambda entry: (entry.created, entry.session_id), reverse=True)
    total = sum(len(entry.messages) for entry in sessions if entry.error is None)
    if total > MAX_INDEX_MESSAGES:
        partial = True
        kept: list[_SessionEntry] = []
        count = 0
        for entry in sessions:
            if entry.error is None:
                if count + len(entry.messages) > MAX_INDEX_MESSAGES:
                    break
                count += len(entry.messages)
            kept.append(entry)
        sessions = kept
    index.sessions = sessions
    index.partial = partial


def _index(workspace: Path, purpose: str) -> _Index:
    key = (str(workspace), purpose)
    index = _CACHE.get(key)
    if index is None:
        index = _Index(workspace, purpose)
        _CACHE[key] = index
        _STATS["index_builds"] += 1
    else:
        _STATS["cache_hits"] += 1
    _refresh(index)
    return index


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
    after = _decode_cursor(args["cursor"], scope) if "cursor" in args else None

    index = _index(workspace, purpose)
    sessions = index.sessions
    if after is not None:
        position = next((numbered for numbered, entry in enumerate(sessions) if entry.session_id == after[0]), None)
        if position is None:
            raise ValueError("search cursor 已失效；请重新搜索")
    else:
        position = 0

    skipped = sum(entry.corrupt for entry in sessions) + sum(1 for entry in sessions if entry.error)
    matches: list[dict[str, Any]] = []
    next_cursor: str | None = None
    stop = False
    for entry in sessions[position:]:
        if stop:
            break
        for message in entry.messages:
            if after is not None and entry.session_id == after[0] and message["index"] <= after[1]:
                continue
            if needle not in message["content"].lower():
                continue
            matches.append({
                "session_id": entry.session_id,
                "identity": entry.identity,
                "title": entry.title,
                "updated_at": entry.created,
                "role": message["role"],
                "snippet": _snippet(message["content"], needle),
                "ts": message["ts"],
                "message_index": message["index"],
            })
            if len(matches) > PAGE:
                matches = matches[:PAGE]
                last = matches[-1]
                next_cursor = _encode_cursor(scope, last["session_id"], last["message_index"])
                stop = True
                break
    return {
        "workspace": str(workspace),
        "session_id": current.id,
        "purpose": purpose,
        "query": query,
        "matches": matches,
        "next_cursor": next_cursor,
        "skipped": skipped,
        "truncated": index.partial,
    }
