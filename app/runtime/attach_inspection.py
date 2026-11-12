"""attach 的任务范围只读检查；不持有 Agent 或执行队列。"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

from app.runtime.inspection import InspectionStore, capture_file_source
from app.runtime.plan_review import PlanReview
from app.runtime.tasks import TaskStore
from app.session.store import SessionStore


def task_file_source(task_dir, workspace):
    try:
        value = json.loads((Path(task_dir) / 'worktree.json').read_text())
        return capture_file_source(value['path'])
    except FileNotFoundError:
        return capture_file_source(workspace)
    except (OSError, ValueError, TypeError, KeyError):
        return None


class AttachInspection:
    def __init__(self, session_root: Path, task_id: str):
        self.session_root = Path(session_root).resolve()
        self.task_id = task_id
        self.tasks = TaskStore(self.session_root)
        task = self.tasks.get(task_id)
        self.workspace = str(Path(task.workspace).resolve())
        self.store = InspectionStore(self.session_root, self.workspace)
        self.known = OrderedDict()

    @staticmethod
    def _key(target):
        return json.dumps(target, sort_keys=True, ensure_ascii=False, allow_nan=False)

    def _remember(self, records):
        for record in records:
            if record['scope']['taskId'] != self.task_id:
                raise ValueError('inspection task mismatch')
            refs = [record['plan']['target']] if record.get('plan') else []
            if record.get('revision'):
                refs.extend([record['revision']['source'], record['revision']['result']])
            for ref in refs:
                if ref:
                    self.known[self._key(ref)] = None
        while len(self.known) > 128:
            self.known.popitem(last=False)

    def handle(self, frame):
        if not isinstance(frame, dict) or frame.get('type') != 'command':
            return None
        command, args = frame.get('command'), frame.get('args', {})
        if command != 'inspections' and not (command == 'plan' and isinstance(args, dict) and args.get('action') == 'review'):
            return None
        identity = frame.get('id')
        envelope = {'v': 1, 'id': identity if isinstance(identity, str) and len(identity) <= 128 else None}
        def error(code, message):
            return {**envelope, 'type': 'error', 'code': code, 'message': message}
        if (type(frame.get('v')) is not int or frame['v'] != 1 or not isinstance(identity, str) or not identity.strip()
                or len(identity) > 128 or set(frame) - {'v', 'type', 'id', 'command', 'args'} or not isinstance(args, dict)):
            return error('invalid_request', '检查查询格式无效')
        try:
            if command == 'inspections':
                if set(args) - {'cursor', 'task_id'} or any(not isinstance(value, str) for value in args.values()):
                    return error('invalid_request', '历史查询仅接受 cursor 和 task_id')
                if args.get('task_id', self.task_id) != self.task_id:
                    return error('inspection_scope_mismatch', '只能读取 attach 绑定任务的结果')
                page = self.store.page(task_id=self.task_id, before=args.get('cursor'), limit=3)
                self._remember(page['records'])
                return {**envelope, 'type': 'inspection_history', 'workspace': self.workspace,
                        'session_id': self.session_root.name, 'task_id': self.task_id, **page}
            if set(args) - {'action', 'target', 'cursor'} or type(args.get('cursor', 0)) is not int or args.get('cursor', 0) < 0:
                return error('invalid_request', '计划查询格式无效')
            target = args.get('target')
            if self._key(target) not in self.known:
                return error('inspection_scope_mismatch', '先选择本任务的历史记录，再读取其准确计划')
            review = PlanReview(self.session_root, session_id=self.session_root.name).read_review(target, args.get('cursor', 0))
            return {**envelope, 'type': 'plan_review', **review, 'available_actions': []}
        except ValueError as exc:
            return error('invalid_request', str(exc)[:1000])
        except (OSError, TypeError, KeyError) as exc:
            return error('inspection_unavailable', f'检查记录暂不可读：{exc}'[:1000])

    def bootstrap(self, *, offline: bool, control: bool):
        session = SessionStore(self.session_root)
        try:
            metadata = json.loads(session.meta_path.read_text())
        except (OSError, ValueError):
            metadata = {}
        capabilities = ['attach', 'tasks', 'inspection-history', 'plan-review']
        if offline:
            capabilities.append('attach-offline')
        elif control:
            capabilities.extend(['run', 'cancel', 'steer', 'approval_once', 'approval_scopes', 'commands', 'resume'])
        task = self.tasks.get(self.task_id)
        worktree = None
        try:
            value = json.loads((self.tasks.task_dir(self.task_id) / 'worktree.json').read_text())
            worktree = {key: value.get(key) for key in ('path', 'branch', 'base_commit')}
        except (OSError, ValueError):
            pass
        source = task_file_source(self.tasks.task_dir(self.task_id), self.workspace)
        events = [
            {'v': 1, 'type': 'ready', 'id': None, 'workspace': self.workspace,
             'file_source': source,
             'session_id': session.id, 'session_path': str(self.session_root), 'session_purpose': session.purpose or 'user',
             'model': str(metadata.get('model') or 'unknown'), 'provider': 'attach', 'resumed': True, 'capabilities': capabilities},
            {'v': 1, 'type': 'task_snapshot', 'id': None, 'file_source': source, 'task': {
                'task_id': task.task_id, 'kind': task.kind, 'status': task.status,
                'segment': task.segments[-1].segment if task.segments else 0,
                'run_id': task.segments[-1].run_id if task.segments else None, 'objective': task.objective,
                'goal_id': task.goal_id, 'mode': task.mode, 'plan_id': task.plan_id, 'plan_version': task.plan_version,
                'worktree': worktree}, 'approvals': []},
            *([{'v': 1, 'type': 'status', 'id': None, 'phase': 'idle'},
               {'v': 1, 'type': 'notice', 'id': None, 'message': 'worker 未连接，当前为离线结果只读；查看历史不会重新运行任务。'}] if offline else []),
        ]
        # attach 时重放已批准计划的步骤快照，避免只能看到任务状态而看不到子任务进度。
        if task.plan_id and task.plan_version and task.segments:
            try:
                from app.runtime.plan import find_plan
                from app.runtime.plan_execution import load_ledger, progress_payload
                found = find_plan(self.session_root, task.plan_id, int(task.plan_version))
                if found is not None:
                    task_dir, plan = found
                    run_id = task.segments[-1].run_id
                    if run_id:
                        events.append({'v': 1, 'type': 'plan_progress', 'id': run_id,
                                       **progress_payload(plan, load_ledger(task_dir, plan.version))})
            except (OSError, ValueError, TypeError):
                pass
        return events
