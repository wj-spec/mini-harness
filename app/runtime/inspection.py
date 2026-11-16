"""运行检查快照：只保存明确归属的事实，读取不改变执行状态。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from core.task.outcome import valid_outcome

MAX_BYTES = 256 * 1024
_NAME = re.compile(r"^\d{20}-[0-9a-f]{64}\.json$")


def _clip(value, limit):
    return str(value or '').encode('utf-8')[:limit].decode('utf-8', errors='ignore')


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')


def capture_file_source(root):
    try:
        path = Path(root).resolve(strict=True)
        stat = path.stat()
        return {'root': str(path), 'identity': f'{stat.st_dev}:{stat.st_ino}'} if path.is_dir() else None
    except (OSError, ValueError, TypeError):
        return None


class InspectionStore:
    def __init__(self, session_root: str | Path, workspace: str | Path):
        self.session_root = Path(session_root).resolve()
        self.workspace = str(Path(workspace).resolve())
        self.root = self.session_root / 'inspections'

    def start(self, request_id: str, objective: str, *, task=None, tools_root=None):
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise ValueError('invalid inspection request ID')
        key = hashlib.sha256(request_id.encode()).hexdigest()
        if any(self.root.glob(f'*-{key}.json')):
            raise ValueError('inspection request already exists')
        file = self.root / f'{time.time_ns():020d}-{key}.json'
        return RunInspection(self, file, request_id, objective, task, tools_root)

    def _read(self, file):
        try:
            if not _NAME.fullmatch(file.name) or file.is_symlink():
                return None
            with file.open('rb') as stream:
                data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                return None
            envelope = json.loads(data)
            snapshot = envelope['snapshot']
            if type(envelope['schema_version']) is not int or envelope['schema_version'] != 1 or envelope['checksum'] != hashlib.sha256(_encoded(snapshot)).hexdigest():
                return None
            if snapshot['workspace'] != self.workspace or snapshot['sessionId'] != self.session_root.name:
                return None
            request_id = snapshot['scope']['requestId']
            if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
                return None
            suffix = '-' + hashlib.sha256(request_id.encode()).hexdigest() + '.json'
            if not file.name.endswith(suffix) or len(list(self.root.glob('*' + suffix))) != 1:
                return None
            source = snapshot.get('fileSource')
            if source is not None and (not isinstance(source, dict) or set(source) != {'root', 'identity'}
                    or not isinstance(source['root'], str) or not Path(source['root']).is_absolute()
                    or not isinstance(source['identity'], str) or not re.fullmatch(r'\d+:\d+', source['identity'])):
                return None
            snapshot['fileSource'] = source
            return snapshot
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return None

    def read(self, request_id: str):
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            return None
        files = list(self.root.glob('*-' + hashlib.sha256(request_id.encode()).hexdigest() + '.json'))
        return self._read(files[0]) if len(files) == 1 else None

    def page(self, *, before=None, task_id=None, limit=20):
        if type(limit) is not int or not 1 <= limit <= 20 or before is not None and (not isinstance(before, str) or not _NAME.fullmatch(before)):
            raise ValueError('invalid inspection page')
        if task_id is not None and (not isinstance(task_id, str) or not task_id or len(task_id) > 128):
            raise ValueError('invalid inspection task')
        files = sorted((file for file in self.root.glob('*.json') if _NAME.fullmatch(file.name) and (before is None or file.name < before)), reverse=True)
        records, skipped, scanned = [], 0, 0
        for file in files[:256]:
            scanned += 1
            value = self._read(file)
            if value is None:
                skipped += 1
            elif task_id is None or value['scope']['taskId'] == task_id:
                records.append(value)
            if len(records) == limit:
                break
        return {'records': records, 'skipped': skipped,
                'next_cursor': files[scanned - 1].name if scanned and scanned < len(files) else None}


class RunInspection:
    def __init__(self, store, file, request_id, objective, task, tools_root=None):
        self.store, self.file, self.error = store, file, None
        self.snapshot = {
            'workspace': store.workspace, 'sessionId': store.session_root.name,
            'fileSource': capture_file_source(tools_root if tools_root is not None else store.workspace),
            'scope': {'requestId': request_id, 'traceRunId': None, 'taskId': None, 'segment': None,
                      'taskStatus': None, 'status': 'unknown', 'objective': _clip(objective, 8000)},
            'plan': None, 'revision': None, 'files': [], 'omittedFiles': 0,
            'verification': {'requestId': request_id, 'status': None, 'stopReason': '', 'verified': None,
                             'completionAllowed': None, 'completionMessages': [], 'omittedCommands': 0, 'commands': []},
        }
        self._task(task)
        self._save()

    def _task(self, task):
        if not isinstance(task, dict):
            return
        scope = self.snapshot['scope']
        if scope['taskId'] and (scope['taskId'] != task.get('task_id') or scope['segment'] != task.get('segment')):
            return
        scope.update(taskId=task.get('task_id'), segment=task.get('segment'), taskStatus=task.get('status'))
        if isinstance(task.get('objective'), str):
            scope['objective'] = _clip(task['objective'], 8000)

    def _save(self):
        temporary = self.file.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            self.store.root.mkdir(parents=True, exist_ok=True)
            snapshot = self.snapshot
            data = _encoded({'schema_version': 1, 'snapshot': snapshot, 'checksum': hashlib.sha256(_encoded(snapshot)).hexdigest()})
            if len(data) > MAX_BYTES:
                raise ValueError('inspection exceeds size limit')
            with temporary.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.file)
            self.error = None
        except (OSError, ValueError, TypeError) as exc:
            self.error = f'{type(exc).__name__}: {exc}'
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def bind_plan(self, plan, relation):
        from app.runtime.plan import content_hash
        target = {'session_id': self.store.session_root.name, 'plan_task_id': plan.task_id, 'plan_id': plan.plan_id,
                  'plan_version': plan.version, 'plan_hash': content_hash(plan)}
        self.snapshot['plan'] = {'target': target, 'relation': relation, 'objective': _clip(plan.objective, 8000),
                                 'status': plan.status, 'stepCount': len(plan.steps), 'problem': None}
        revision = self.snapshot['revision']
        if relation == 'revision-result' and revision:
            revision['result'] = target
        self._save()

    def bind_revision(self, revision):
        self.bind_plan(revision.plan, 'revision-source')
        self.snapshot['revision'] = {'id': revision.revision_id, 'source': self.snapshot['plan']['target'], 'result': None}
        self._save()

    def trace(self, run_id):
        if isinstance(run_id, str) and run_id:
            self.snapshot['scope']['traceRunId'] = _clip(run_id, 256)

    def observe(self, kind, **data):
        if kind == 'task_state':
            self._task(data.get('task')); self._save(); return
        if kind not in {'tool_call', 'tool_result'}:
            return
        call = data.get('call')
        if not isinstance(call, dict):
            return
        name, args = call.get('name'), call.get('args') or {}
        if name == 'bash':
            verification = self.snapshot['verification']; commands = verification['commands']
            identity = _clip(call.get('id') or f'command-{verification["omittedCommands"] + len(commands)}', 256)
            item = next((command for command in reversed(commands) if command['id'] == identity and command['pending']), None) if kind == 'tool_result' else None
            if item is None:
                item = {'id': identity, 'command': _clip(args.get('command'), 1000), 'verification': args.get('verification') is True,
                        'pending': True, 'ok': None, 'execution': None, 'output': '', 'truncated': False}
                commands.append(item)
                if len(commands) > 20:
                    commands.pop(0); verification['omittedCommands'] += 1
            if kind == 'tool_result':
                output = data.get('output') or ''
                item.update(pending=False, ok=data.get('ok') if type(data.get('ok')) is bool else None,
                            execution=data.get('execution'), output=_clip(output, 2000),
                            truncated=len(str(output).encode()) > 2000 or len(str(args.get('command') or '').encode()) > 1000)
        elif kind == 'tool_result' and data.get('ok') is True and name in {'write_file', 'edit_file', 'apply_patch'}:
            paths = [args.get('path')]
            if name == 'apply_patch':
                from app.tools.patch import parse_patch
                paths = [file.target for file in parse_patch(str(args.get('patch') or ''))]
            files = self.snapshot['files']
            for path in paths:
                if not isinstance(path, str):
                    continue
                path = path.removeprefix('./')
                if not path or path.startswith('/') or any(part in {'', '.', '..'} for part in path.split('/')) or re.search(r'[\x00-\x1f\x7f]', path) or len(path.encode()) > 512:
                    continue
                if path in files:
                    files.remove(path)
                files.append(path)
                if len(files) > 200:
                    files.pop(0); self.snapshot['omittedFiles'] += 1
        else:
            return
        self._save()

    def finish(self, result):
        self.trace(result.get('run_id'))
        self.snapshot['scope']['status'] = 'finished'
        completion = result.get('completion') or {}
        self.snapshot['verification'].update(
            outcome=result.get('outcome') if valid_outcome(result.get('outcome')) else None,
            receipt=(_clip(result.get('receipt'), 11940) + '…（已截断，完整记录见轮次账本）'
                     if len(str(result.get('receipt') or '').encode('utf-8')) > 12000 else _clip(result.get('receipt'), 12000)),
            status=result.get('status'), stopReason=_clip(result.get('stop_reason'), 256),
            verified=result.get('verified'), completionAllowed=completion.get('allowed'),
            completionMessages=[_clip(message, 500) for message in completion.get('messages', [])[:10]],
        )
        self._save()
