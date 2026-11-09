"""当前会话任务目录；查询不恢复或修改任务。"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
from pathlib import Path

from app.runtime.approvals import ApprovalInbox
from app.runtime.inspection import InspectionStore
from app.runtime.supervise import ACTIVE_WORKER_STATUSES, WorkerStateError, is_stale, pid_alive, read_worker_state
from app.runtime.tasks import TaskError, TaskStore

PAGE_SIZE = 5


def task_catalog(session_root: Path, workspace: Path, args: object) -> dict:
    if not isinstance(args, dict) or set(args) - {'cursor'}:
        raise ValueError('tasks accepts only cursor')
    workspace = workspace.resolve()
    session_root = Path(session_root)
    scope = hashlib.sha256(f'{workspace}\0{session_root.name}'.encode()).hexdigest()
    before = None
    cursor = args.get('cursor')
    if 'cursor' in args:
        if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
            raise ValueError('invalid task cursor')
        try:
            decoded = json.loads(base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
        except (ValueError, UnicodeError) as exc:
            raise ValueError('invalid task cursor') from exc
        if (not isinstance(decoded, list) or len(decoded) != 3 or decoded[0] != scope
                or not all(isinstance(value, str) and 0 < len(value) <= 128 for value in decoded)):
            raise ValueError('task cursor belongs to another scope or is invalid')
        before = tuple(decoded[1:])
    store = TaskStore(session_root)
    if store.tasks_root.is_symlink():
        raise OSError('tasks directory is a symlink')
    records = []
    skipped = 0
    if store.tasks_root.exists():
        for entry in store.tasks_root.iterdir():
            try:
                if entry.is_symlink() or not entry.is_dir():
                    raise ValueError('not a task directory')
                record = store.get(entry.name)
                if (record.task_id != entry.name or record.session_id != session_root.name
                        or Path(record.workspace).resolve() != workspace):
                    raise ValueError('task scope mismatch')
            except (TaskError, ValueError, OSError):
                skipped += 1
                continue
            if before is None or (record.created_at, record.task_id) < before:
                records.append(record)
    records.sort(key=lambda record: (record.created_at, record.task_id), reverse=True)
    page = records[:PAGE_SIZE]
    problems = []
    pending: dict[str, int] | None = {}
    try:
        inbox = ApprovalInbox(session_root)
        for item in inbox.pending():
            identity = item.get('task_id')
            if isinstance(identity, str):
                pending[identity] = pending.get(identity, 0) + 1
        if inbox.last_skipped:
            pending = None
            problems.append(f'审批记录有 {inbox.last_skipped} 条不可读，数量未知')
    except (OSError, ValueError) as exc:
        pending = None
        problems.append(f'审批数暂不可读：{exc}'[:500])
    rows = []
    for record in page:
        worker = None
        worker_error = None
        try:
            state = read_worker_state(store.task_dir(record.task_id))
            if state:
                foreign = state.host != socket.gethostname()
                if state.task_id != record.task_id:
                    raise WorkerStateError('worker task identity mismatch')
                worker = {'status': state.status, 'foreign_host': foreign,
                          'pid_alive': None if foreign else pid_alive(state.pid),
                          'stale': None if foreign else is_stale(state)}
        except (WorkerStateError, OSError) as exc:
            worker_error = str(exc)[:500]
        live = bool(worker and not worker['foreign_host'] and worker['pid_alive']
                    and not worker['stale'] and worker['status'] in ACTIVE_WORKER_STATUSES)
        reason = '其他主机的任务不能在本机控制' if worker and worker['foreign_host'] else worker_error
        offline = False
        if not live and not reason:
            try:
                saved = InspectionStore(session_root, workspace).page(task_id=record.task_id, limit=1)
                offline = bool(saved['records'] or saved['skipped'])
            except OSError:
                reason = '保存的检查记录暂不可读'
        stoppable = live and record.status == 'running' and any(segment.status == 'running' for segment in record.segments)
        controls = {'attach': 'live' if live else 'offline' if offline else None,
                    'attach_reason': None if live or offline else reason or '没有可连接的 worker 或保存结果',
                    'stop': stoppable,
                    'stop_reason': None if stoppable else reason or '没有可停止的本机运行段'}
        rows.append({'task_id': record.task_id, 'objective': record.objective,
                     'kind': record.kind, 'mode': record.mode, 'status': record.status, 'priority': record.priority,
                     'created_at': record.created_at, 'updated_at': record.updated_at,
                     'worker': worker, 'worker_error': worker_error,
                     'controls': controls,
                     'pending_approvals': None if pending is None else pending.get(record.task_id, 0)})
    next_cursor = None
    if len(records) > PAGE_SIZE:
        last = page[-1]
        next_cursor = base64.urlsafe_b64encode(json.dumps([scope, last.created_at, last.task_id]).encode()).decode().rstrip('=')
    return {'workspace': str(workspace), 'session_id': session_root.name,
            'records': rows, 'next_cursor': next_cursor, 'skipped': skipped, 'problems': problems}
