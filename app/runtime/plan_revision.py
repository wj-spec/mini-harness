"""计划修订：持久请求、只读查询与一次初始运行交接。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.runtime.plan import Plan, PlanError, content_hash, plan_lock, plan_path
from app.runtime.plan_review import PlanReview, _ReviewFailure, _valid_ref
from app.runtime.task_runtime import RunTask, TaskRuntime, TaskRuntimeError
from app.runtime.tasks import TASK_ID_RE, TaskError, TaskStore


@dataclass(frozen=True)
class RevisionDecision:
    receipt: dict[str, Any]
    run_task: RunTask | None = None


@dataclass(frozen=True)
class RevisionContext:
    plan: Plan
    feedback: str
    revision_id: str
    task_dir: Path


class PlanRevision:
    """反馈绑定所见版本；查询和重试都不能隐式续跑。"""

    def __init__(self, session_root: str | Path, *, session_id: str, task_runtime: TaskRuntime | None = None):
        self.root = Path(session_root)
        self.session_id = session_id
        self.runtime = task_runtime
        self.store = TaskStore(self.root)
        self.review = PlanReview(self.root, session_id=session_id, task_runtime=task_runtime)

    def _identity(self, ref, feedback):
        if not _valid_ref(ref) or not isinstance(feedback, str) or not 0 < len(feedback.strip()) <= 4000:
            raise PlanError("需要准确计划身份和 1–4000 字反馈", code="invalid_request")
        if ref['session_id'] != self.session_id:
            raise PlanError("计划不属于当前会话", code="target_mismatch")
        target = {'operation': 'plan-revision', 'source': dict(ref), 'feedback': feedback.strip()}
        key = hashlib.sha256(json.dumps(target, sort_keys=True, ensure_ascii=False,
                                       separators=(',', ':')).encode()).hexdigest()
        return key, target

    def _directory(self, ref):
        return self.root / 'tasks' / ref['plan_task_id']

    def _path(self, ref, key):
        return self._directory(ref) / 'plan/review-revisions' / f'{key}.json'

    def _source(self, ref):
        snapshot = self.review._read_review(ref, 0)
        if snapshot['code'] or snapshot['outcome'] != 'ready':
            raise PlanError(snapshot['message'], code=snapshot['code'] or 'invalid_request')
        plan = self.review._read_plan(plan_path(self._directory(ref), ref['plan_version']))
        if content_hash(plan) != ref['plan_hash']:
            raise PlanError("原计划内容已变化", code="content_changed")
        if plan.status not in {'draft', 'proposed', 'approved'}:
            raise PlanError("当前计划状态不能修订", code="plan_stale")
        if plan.goal_id is not None and (
            not isinstance(plan.goal_id, str) or not plan.goal_id.strip()
            or len(plan.goal_id) > 128 or plan.goal_id != plan.goal_id.strip()
        ):
            raise PlanError("原计划的目标绑定无效", code="invalid_plan")
        return plan

    def _save(self, operation):
        path = self._path(operation['target']['source'], operation['key'])
        self.review._check_path(path.parent.parent, directory=True)
        path.parent.mkdir(exist_ok=True)
        self.review._check_path(path.parent, directory=True)
        if path.exists() or path.is_symlink():
            self.review._check_path(path)
        operation['updated_at'] = datetime.now(timezone.utc).isoformat()
        fd, name = tempfile.mkstemp(prefix='.revision-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(operation, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
            for directory in (path.parent, path.parent.parent):
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            Path(name).unlink(missing_ok=True)

    def _load(self, ref, key, target):
        path = self._path(ref, key)
        if not path.parent.exists() and not path.parent.is_symlink():
            return None
        self.review._check_path(path.parent, directory=True)
        if not path.exists() and not path.is_symlink():
            return None
        self.review._check_path(path)
        with path.open('rb') as handle:
            raw = handle.read(65537)
        try:
            operation = json.loads(raw) if len(raw) <= 65536 else None
            if (not isinstance(operation, dict) or type(operation.get('schema_version')) is not int
                    or operation['schema_version'] != 1 or operation.get('key') != key
                    or operation.get('target') != target or not isinstance(operation.get('task_id'), str)
                    or not TASK_ID_RE.fullmatch(operation['task_id'])
                    or operation.get('stage') not in {'reserved', 'starting', 'started'}
                    or not isinstance(operation.get('prepare_failed', False), bool)):
                raise ValueError('invalid revision operation')
            if operation['stage'] != 'reserved' and (
                not isinstance(operation.get('run_id'), str) or not operation['run_id']
                or type(operation.get('owner_pid')) is not int or operation['owner_pid'] <= 0
                or not isinstance(operation.get('owner_host'), str) or not operation['owner_host']
            ):
                raise ValueError('invalid revision owner')
            resume = operation.get('resume')
            if resume is not None and (
                not isinstance(resume, dict) or set(resume) != {'segment', 'run_id', 'owner_pid', 'owner_host', 'failed'}
                or type(resume['segment']) is not int or resume['segment'] < 1
                or not isinstance(resume['run_id'], str) or not resume['run_id']
                or type(resume['owner_pid']) is not int or resume['owner_pid'] <= 0
                or not isinstance(resume['owner_host'], str) or not resume['owner_host']
                or not isinstance(resume['failed'], bool)
            ):
                raise ValueError('invalid resume preparation')
            return operation
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise PlanError("修订记录损坏，请核对原任务", code="needs_reconciliation") from exc

    def _task(self, operation):
        directory = self.store.task_dir(operation['task_id'])
        if not directory.exists():
            if operation['stage'] != 'reserved':
                raise PlanError("原修订任务缺失", code="needs_reconciliation")
            return None
        self.review._check_path(directory / 'meta.json')
        task = self.store.get(operation['task_id'])
        ref = operation['target']['source']
        if (task.extra.get('creation') != {'key': operation['key'], 'target': operation['target']}
                or task.task_id != operation['task_id'] or task.session_id != self.session_id
                or task.kind != 'run' or task.mode != 'plan' or task.parent_id != ref['plan_task_id']
                or task.plan_id != ref['plan_id'] or task.plan_version != ref['plan_version']
                or (self.runtime is not None and Path(task.workspace).resolve() != Path(self.runtime.workspace))):
            raise PlanError("修订任务与来源凭据不一致", code="needs_reconciliation")
        if task.segments and task.segments[0].run_id != operation.get('run_id'):
            raise PlanError("修订任务初始运行不一致", code="needs_reconciliation")
        if not task.segments and operation['stage'] == 'started':
            raise PlanError("修订运行段缺失", code="needs_reconciliation")
        resume = operation.get('resume')
        if resume is not None and (len(task.segments) < resume['segment'] or (
            len(task.segments) > resume['segment'] and task.segments[resume['segment']].run_id != resume['run_id']
        )):
            raise PlanError("续做凭据与运行段不一致", code="needs_reconciliation")
        return task

    def _receipt(self, ref, target, key, operation=None, task=None, *, started=False):
        segment = task.segments[-1] if task is not None and task.segments else None
        outcome = 'absent' if operation is None else 'pending'
        if segment is not None:
            outcome = 'running' if task.status == 'running' else 'paused'
            if task.status in {'completed', 'failed', 'cancelled'} or segment.stop_reason == 'revision_not_submitted':
                outcome = 'incomplete'
        output = self._published(operation) if operation else None
        if output is not None:
            outcome = 'submitted'
        code = 'revision_not_submitted' if outcome == 'incomplete' else None
        message = '运行已结束，但未提交修订计划' if code else ('没有修订请求' if operation is None else '已读取原修订任务')
        return {'outcome': outcome, 'code': code, 'message': message,
                'target': ref, 'revision_id': key, 'feedback': target['feedback'], 'result_target': output,
                'task': None if task is None else {'task_id': task.task_id, 'status': task.status,
                         'segment': segment.segment if segment else 0, 'run_id': segment.run_id if segment else None,
                         'run_started': started}}

    def _published(self, operation):
        ref, feedback = operation['target']['source'], operation['target']['feedback']
        path = plan_path(self._directory(ref), ref['plan_version'] + 1)
        if not path.exists() and not path.is_symlink():
            return None
        plan = self.review._read_plan(path)
        origin = {'id': operation['key'], 'base_version': ref['plan_version'],
                  'base_hash': ref['plan_hash'], 'feedback': feedback}
        if plan.plan_id != ref['plan_id'] or plan.source.get('revision') != origin:
            raise PlanError("已有其他修订版本，请重新审阅", code="superseded")
        return {**ref, 'plan_version': plan.version, 'plan_hash': content_hash(plan)}

    def context(self, run_task: RunTask) -> RevisionContext:
        try:
            return self._context(run_task)
        except (_ReviewFailure, OSError, TaskError, UnicodeError, RecursionError) as exc:
            raise PlanError(str(exc), code=getattr(exc, 'code', 'storage_error')) from exc

    def _context(self, run_task):
        record = self.store.get(run_task.task_id)
        creation = record.extra.get('creation') or {}
        if not isinstance(creation, dict):
            raise PlanError("修订任务来源凭据损坏", code="needs_reconciliation")
        target = creation.get('target') or {}
        if not isinstance(target, dict) or set(target) != {'operation', 'source', 'feedback'}:
            raise PlanError("修订任务缺少来源凭据", code="needs_reconciliation")
        ref = target['source']
        key, expected = self._identity(ref, target['feedback'])
        if target != expected or creation != {'key': key, 'target': target}:
            raise PlanError("修订来源凭据不一致", code="needs_reconciliation")
        with plan_lock(self._directory(ref), require_process_lock=True):
            operation = self._load(ref, key, target)
            if operation is None or operation['task_id'] != run_task.task_id:
                raise PlanError("修订操作记录缺失或不一致", code="needs_reconciliation")
            task = self._task(operation)
            if (task.status != 'running' or not task.segments or task.segments[-1].run_id != run_task.run_id
                    or task.segments[-1].segment != run_task.segment or task.segments[-1].status != 'running'):
                raise PlanError("修订运行段不匹配", code="needs_reconciliation")
            if self._published(operation) is not None:
                raise PlanError("修订产物已经提交，不应再次请求模型", code="already_submitted")
            plan = self._source(ref)
            self._check_task_plan(task, plan)
            self._check_other_revisions(ref, key)
            self._check_execution(ref)
            return RevisionContext(plan, target['feedback'], key, self._directory(ref))

    @staticmethod
    def _check_task_plan(task, plan):
        if task is not None and (task.objective != plan.objective or task.goal_id != plan.goal_id):
            raise PlanError("修订任务范围与原计划不一致", code="needs_reconciliation")

    def _failure(self, ref, feedback, error):
        return {'outcome': 'unavailable', 'code': getattr(error, 'code', 'storage_error'),
                'message': str(error), 'target': ref if _valid_ref(ref) else None, 'revision_id': None,
                'feedback': feedback.strip() if isinstance(feedback, str) and len(feedback.strip()) <= 4000 else '',
                'task': None, 'result_target': None}

    def _check_other_revisions(self, ref, key=None):
        directory = self._directory(ref) / 'plan/review-revisions'
        if not directory.exists() and not directory.is_symlink():
            return
        self.review._check_path(directory, directory=True)
        for path in directory.glob('*.json'):
            if path.stem == key:
                continue
            self.review._check_path(path)
            with path.open('rb') as handle:
                raw = handle.read(65537)
            try:
                entry = json.loads(raw) if len(raw) <= 65536 else None
                target = entry['target']
                other_key, expected = self._identity(target['source'], target['feedback'])
                if target != expected or path != self._path(ref, other_key):
                    raise ValueError('revision path mismatch')
                other = self._load(ref, other_key, expected)
            except (ValueError, KeyError, TypeError, RecursionError) as exc:
                raise PlanError("同族修订记录无法核对", code="needs_reconciliation") from exc
            task = self._task(other)
            if task is None or task.status in {'created', 'queued', 'running'}:
                raise PlanError("该计划已有活动修订，请先查询或停止", code="revision_busy")

    def guard_review(self, ref):
        """批准入口在同一 Plan 锁下复核修订占用。"""
        try:
            self._guard_review(ref)
        except (_ReviewFailure, OSError, TaskError, UnicodeError, RecursionError) as exc:
            raise PlanError(str(exc), code=getattr(exc, 'code', 'storage_error')) from exc

    def _guard_review(self, ref):
        if not _valid_ref(ref) or ref['session_id'] != self.session_id:
            raise PlanError("审阅身份无效", code="invalid_request")
        self.review._check_path(self._directory(ref), directory=True)
        with plan_lock(self._directory(ref), require_process_lock=True):
            self._source(ref)
            self._check_other_revisions(ref)

    def _check_execution(self, ref):
        for directory in self.store.tasks_root.iterdir():
            if not TASK_ID_RE.fullmatch(directory.name):
                continue
            path = directory / 'meta.json'
            if not path.exists() and not path.is_symlink() and (directory / 'plan').is_dir():
                continue
            self.review._check_path(path)
            task = self.store.get(directory.name)
            if task.task_id != directory.name or task.session_id != self.session_id:
                raise PlanError("已有任务身份无法核对", code="needs_reconciliation")
            if (task.mode in {'act', 'goal'} and task.plan_id == ref['plan_id']
                    and task.status in {'created', 'queued', 'running'}):
                raise PlanError("计划已有待执行或运行中任务", code="plan_busy")

    @staticmethod
    def _owner_dead(owner):
        dead = False
        if owner['owner_host'] == socket.gethostname():
            try:
                os.kill(owner['owner_pid'], 0)
            except ProcessLookupError:
                dead = True
            except (OSError, OverflowError):
                pass
        return dead

    def _recover_initial(self, operation):
        if not self._owner_dead(operation) and not operation.get('prepare_failed'):
            raise PlanError("启动者仍存活或无法确认，不能重复准备", code="needs_reconciliation")
        self.store.recover_preparation(operation['task_id'])
        operation.update(stage='reserved', run_id=None, owner_pid=None, owner_host=None, prepare_failed=False)
        self._save(operation)

    def _recover_resume(self, operation, task):
        resume = operation['resume']
        if len(task.segments) == resume['segment']:
            if not resume['failed'] and not self._owner_dead(resume):
                raise PlanError("续做启动者仍存活或无法确认", code="needs_reconciliation")
            self.store.recover_preparation(task.task_id, expected_segments=resume['segment'])
        operation.pop('resume')
        self._save(operation)

    def read(self, ref, feedback):
        try:
            key, target = self._identity(ref, feedback)
            operation = self._load(ref, key, target)
            if operation is None:
                self._source(ref)
            return self._receipt(ref, target, key, operation, self._task(operation) if operation else None)
        except (PlanError, _ReviewFailure, OSError, TaskError, UnicodeError, RecursionError) as exc:
            return self._failure(ref, feedback, exc)

    def history(self, ref, cursor=None):
        """只读发现同族反馈；记录中的来源不随当前版本改变。"""
        result = {'target': ref if _valid_ref(ref) else None, 'records': [], 'next_cursor': None,
                  'code': None, 'message': '没有已发送的修订反馈'}
        try:
            if (not _valid_ref(ref) or ref['session_id'] != self.session_id
                    or cursor is not None and (not isinstance(cursor, str) or not re.fullmatch(r'[0-9a-f]{64}', cursor))):
                raise PlanError('修订记录需要准确计划身份和有效游标', code='invalid_request')
            self.review._check_path(self._directory(ref), directory=True)
            directory = self._directory(ref) / 'plan/review-revisions'
            if not directory.exists() and not directory.is_symlink():
                return result
            self.review._check_path(directory, directory=True)
            paths = sorted(p for p in directory.glob('*.json') if cursor is None or p.stem > cursor)
            records = []
            for path in paths[:10]:
                self.review._check_path(path)
                with path.open('rb') as handle:
                    raw = handle.read(65537)
                try:
                    entry = json.loads(raw) if len(raw) <= 65536 else None
                    target = entry['target']
                    source, feedback = target['source'], target['feedback']
                    key, expected = self._identity(source, feedback)
                    if (target != expected or path != self._path(ref, key)
                            or any(source[field] != ref[field] for field in ('session_id', 'plan_task_id', 'plan_id'))):
                        raise ValueError('revision family mismatch')
                    operation = self._load(source, key, expected)
                    if operation is None:
                        raise ValueError('revision disappeared')
                except (ValueError, KeyError, TypeError, RecursionError) as exc:
                    raise PlanError('修订记录损坏或不属于该计划，请核对', code='needs_reconciliation') from exc
                records.append(self.read(source, feedback))
            result.update(records=records, next_cursor=paths[9].stem if len(paths) > 10 else None,
                          message='已读取修订记录' if records else '没有已发送的修订反馈')
        except (PlanError, _ReviewFailure, OSError, TaskError, UnicodeError, RecursionError) as exc:
            result.update(code=getattr(exc, 'code', 'storage_error'), message=str(exc))
        return result

    def request(self, ref, feedback, request_id, *, busy=False):
        try:
            return self._request(ref, feedback, request_id, busy=busy)
        except (PlanError, _ReviewFailure, OSError, TaskError, TaskRuntimeError, UnicodeError, RecursionError) as exc:
            return RevisionDecision(self._failure(ref, feedback, exc))

    def resume(self, ref, feedback, request_id, *, expected_segment, busy=False):
        try:
            if type(expected_segment) is not int or expected_segment < 1:
                raise PlanError("续做需要已观察的运行段编号", code="invalid_request")
            return self._request(ref, feedback, request_id, busy=busy, resume_segment=expected_segment)
        except (PlanError, _ReviewFailure, OSError, TaskError, TaskRuntimeError, UnicodeError, RecursionError) as exc:
            return RevisionDecision(self._failure(ref, feedback, exc))

    def _request(self, ref, feedback, request_id, *, busy, resume_segment=None):
        key, target = self._identity(ref, feedback)
        if (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 128
                or any(ord(c) < 32 for c in request_id)):
            raise PlanError("修订请求 ID 无效", code="invalid_request")
        self.review._check_path(self._directory(ref), directory=True)
        with plan_lock(self._directory(ref), require_process_lock=True):
            operation = self._load(ref, key, target)
            task = self._task(operation) if operation else None
            if task is not None and task.segments:
                receipt = self._receipt(ref, target, key, operation, task)
                if (resume_segment is None or receipt['outcome'] == 'submitted'
                        or task.segments[-1].segment != resume_segment):
                    return RevisionDecision(receipt)
                if operation.get('resume') is None and task.status not in {'paused', 'interrupted'}:
                    return RevisionDecision(receipt)
            elif resume_segment is not None:
                raise PlanError("没有可续做的修订运行段", code="invalid_request")
            if busy:
                raise PlanError("当前运行尚未结束", code="busy")
            plan = self._source(ref)
            self._check_task_plan(task, plan)
            self._check_other_revisions(ref, key)
            self._check_execution(ref)
            if self.runtime is None or not self.runtime.enabled:
                raise PlanError("任务运行时未启用", code="tasks_disabled")
            if self.runtime.store.root.resolve() != self.root.resolve() or self.root.name != self.session_id:
                raise PlanError("任务运行时不属于当前会话", code="target_mismatch")
            if operation is not None and operation.get('resume') is not None:
                self._recover_resume(operation, task)
                task = self._task(operation)
                if task.status not in {'paused', 'interrupted'}:
                    return RevisionDecision(self._receipt(ref, target, key, operation, task))
            if operation is None:
                operation = {'schema_version': 1, 'key': key, 'target': target, 'task_id': self.store.reserve_task_id(),
                             'stage': 'reserved', 'run_id': None, 'owner_pid': None, 'owner_host': None}
                self._save(operation)
            if operation['stage'] == 'starting' and (task is None or not task.segments):
                self._recover_initial(operation)
                task = self._task(operation)
            if task is None:
                task = self.store.create(kind='run', mode='plan', objective=plan.objective,
                                         workspace=self.runtime.workspace, parent_id=plan.task_id,
                                         plan_id=plan.plan_id, plan_version=plan.version, goal_id=plan.goal_id,
                                         reserved_id=operation['task_id'], creation={'key': key, 'target': target})
            if resume_segment is None:
                operation.update(stage='starting', run_id=request_id, owner_pid=os.getpid(), owner_host=socket.gethostname())
                self._save(operation)
            else:
                operation['resume'] = {'segment': resume_segment, 'run_id': request_id,
                                       'owner_pid': os.getpid(), 'owner_host': socket.gethostname(), 'failed': False}
                self._save(operation)
            try:
                run_task = self.runtime.prepare_run(request_id, {'kind': 'run', 'mode': 'plan', 'task_id': task.task_id})
            except (OSError, TaskError, TaskRuntimeError):
                if resume_segment is None and not self._task(operation).segments:
                    operation['prepare_failed'] = True
                    self._save(operation)
                elif resume_segment is not None and len(self._task(operation).segments) == resume_segment:
                    operation['resume']['failed'] = True
                    self._save(operation)
                raise
            operation['stage'] = 'started'
            operation.pop('resume', None)
            receipt = self._receipt(ref, target, key, operation, run_task.record, started=True)
            try:
                self._save(operation)
            except (OSError, _ReviewFailure):
                receipt.update(code='storage_error', message='修订运行已建立，回执待恢复')
            return RevisionDecision(receipt, run_task)
