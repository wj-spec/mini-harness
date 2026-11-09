"""旧会话只读目录、内容节选与打开前身份核验。"""
from __future__ import annotations

import base64
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from app.session.store import SessionStore
from core.contracts import message_from_dict


def session_identity(store: SessionStore, workspace: Path, purpose: str) -> str:
    if store.root.is_symlink() or store.meta_path.is_symlink():
        raise ValueError('会话目录或元数据已被替换')
    raw = store.meta_path.read_bytes()
    metadata = json.loads(raw)
    if (not isinstance(metadata, dict) or metadata.get('id', store.id) != store.id
            or metadata.get('purpose', 'user') != purpose
            or Path(metadata.get('workspace', str(workspace))).resolve() != workspace.resolve()):
        raise ValueError('会话元数据范围不符')
    info = store.root.stat()
    return hashlib.sha256(f'{info.st_dev}:{info.st_ino}:'.encode() + raw).hexdigest()


def open_selected_session(workspace: Path, session_id: str, purpose: str, identity: str) -> SessionStore:
    if not isinstance(identity, str) or len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
        raise ValueError('会话身份无效')
    store = SessionStore.open_existing(workspace, session_id, purpose=purpose)
    if store is None or session_identity(store, workspace, purpose) != identity:
        raise ValueError('所选会话已删除、替换或用途变化；请刷新后重新选择')
    return store


def _timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat(timespec='microseconds')
    except (ValueError, OverflowError):
        return None


def _describe(store: SessionStore, workspace: Path, purpose: str) -> dict:
    identity = session_identity(store, workspace, purpose)
    metadata = json.loads(store.meta_path.read_text())
    updated = _timestamp(metadata.get('created_at'))
    count = 0
    title = ''
    recent = deque(maxlen=3)
    truncated = False
    with store.events_path.open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('消息记录无效')
            if event.get('type') != 'message':
                continue
            message = message_from_dict(event.get('message'))
            count += 1
            updated = _timestamp(event.get('ts')) or updated
            if message.role == 'user' and not title:
                title = message.content[:1000]
            label = {'user': '你', 'assistant': '助手', 'tool': '工具'}.get(message.role, message.role)
            content = message.content or ('（工具调用）' if message.tool_calls else '（空消息）')
            truncated = truncated or len(content) > 1800
            recent.append(f'{label}：{content[:1800]}' + ('…' if len(content) > 1800 else ''))
    if updated is None:
        raise ValueError('会话缺少可核对的时间')
    return {'session_id': store.id, 'identity': identity, 'title': title or '（空会话）' if not count else title or '（无用户标题）',
            'updated_at': updated, 'message_count': count,
            'preview': '\n\n'.join(recent) or '尚未发送消息。', 'truncated': truncated or count > 3}


def session_catalog(current: SessionStore, workspace: Path, args: object) -> dict:
    if not isinstance(args, dict) or set(args) - {'cursor'}:
        raise ValueError('sessions accepts only cursor')
    purpose = current.purpose
    if purpose not in {'user', 'test'}:
        raise ValueError('当前会话用途无法核对')
    workspace = workspace.resolve()
    scope = hashlib.sha256(f'{workspace}\0{purpose}'.encode()).hexdigest()
    before = None
    if 'cursor' in args:
        cursor = args['cursor']
        if not isinstance(cursor, str) or not 0 < len(cursor) <= 512:
            raise ValueError('invalid session cursor')
        try:
            decoded = json.loads(base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
        except (ValueError, UnicodeError) as exc:
            raise ValueError('invalid session cursor') from exc
        if (not isinstance(decoded, list) or len(decoded) != 3 or decoded[0] != scope
                or not all(isinstance(v, str) and 0 < len(v) <= 128 for v in decoded)):
            raise ValueError('session cursor belongs to another scope or is invalid')
        before = tuple(decoded[1:])
    base = workspace / '.vibe/sessions'
    if base.is_symlink() or not base.resolve().is_relative_to(workspace):
        raise OSError('会话目录范围不可核对')
    rows = []
    skipped = 0
    for path in base.iterdir() if base.is_dir() else []:
        try:
            if path.is_symlink() or not path.is_dir():
                raise ValueError('会话目录不可读')
            candidate = SessionStore(path)
            if candidate.purpose in {'user', 'test'} and candidate.purpose != purpose:
                continue
            store = SessionStore.open_existing(workspace, path.name, purpose=purpose)
            if store is None:
                raise ValueError('会话不可读')
            row = _describe(store, workspace, purpose)
            if before is None or (row['updated_at'], row['session_id']) < before:
                rows.append(row)
        except (OSError, ValueError, TypeError, UnicodeError):
            skipped += 1
    rows.sort(key=lambda row: (row['updated_at'], row['session_id']), reverse=True)
    page = rows[:5]
    cursor = None
    if len(rows) > 5:
        last = page[-1]
        cursor = base64.urlsafe_b64encode(json.dumps([scope, last['updated_at'], last['session_id']]).encode()).decode().rstrip('=')
    return {'workspace': str(workspace), 'session_id': current.id, 'purpose': purpose,
            'records': page, 'next_cursor': cursor, 'skipped': skipped}
