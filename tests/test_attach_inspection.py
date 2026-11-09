import io
import json
import asyncio
import tempfile
from pathlib import Path

import pytest

from app.runtime.inspection import InspectionStore
from app.runtime.plan import Plan, content_hash, propose_plan
from app.runtime.tasks import TaskStore
from app.session.store import SessionStore


def seed(tmp_path):
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    tasks = TaskStore(session.root)
    task = tasks.create(kind='run', objective='原任务', workspace=str(tmp_path), mode='chat')
    other = tasks.create(kind='run', objective='另一任务', workspace=str(tmp_path), mode='chat')
    planning = tasks.create(kind='run', objective='计划来源', workspace=str(tmp_path), mode='plan')
    plan = Plan.from_dict({'task_id': planning.task_id, 'plan_id': 'pl-inspect', 'objective': '准确计划',
                          'steps': [{'step_id': 's1', 'title': '读取代码', 'kind': 'read'}],
                          'constraints': ['中文约束' * 30 for _ in range(300)]})
    propose_plan(tasks.task_dir(planning.task_id), plan)
    store = InspectionStore(session.root, tmp_path)
    for index in range(4):
        run = store.start(f'own-{index}', task.objective, task={'task_id': task.task_id, 'segment': index + 1, 'status': 'completed'})
        run.bind_plan(plan, 'execution')
        run.finish({'status': 'done', 'verified': None})
    store.start('foreign', '另一任务', task={'task_id': other.task_id, 'segment': 1, 'status': 'completed'}).finish({'status': 'done'})
    target = {'session_id': session.id, 'plan_task_id': planning.task_id, 'plan_id': plan.plan_id,
              'plan_version': plan.version, 'plan_hash': content_hash(plan)}
    return session, task, other, target


def request(identity='query', command='inspections', **args):
    return {'v': 1, 'type': 'command', 'id': identity, 'command': command, 'args': args}


def test_attach_queries_only_the_bound_task_and_reads_its_exact_plan_without_writes(tmp_path):
    from app.runtime.attach_inspection import AttachInspection
    session, task, other, target = seed(tmp_path)
    query = AttachInspection(session.root, task.task_id)
    before = {str(p): p.read_bytes() for p in session.root.rglob('*') if p.is_file()}
    denied = query.handle(request('unseen', 'plan', action='review', target=target))
    assert denied['type'] == 'error' and denied['code'] == 'inspection_scope_mismatch'
    page = query.handle(request(task_id=task.task_id))
    assert page['task_id'] == task.task_id and len(page['records']) == 3
    assert all(r['scope']['taskId'] == task.task_id for r in page['records'])
    older = query.handle(request('older', cursor=page['next_cursor']))
    assert [r['scope']['requestId'] for r in older['records']] == ['own-0']
    assert older['next_cursor'] is None
    body = query.handle(request('plan', 'plan', action='review', target=target))
    assert body['type'] == 'plan_review' and body['target'] == target and body['available_actions'] == []
    assert body['next_cursor'] and '准确计划' in body['body_page']
    next_page = query.handle(request('plan-next', 'plan', action='review', target=target, cursor=body['next_cursor']))
    assert next_page['type'] == 'plan_review' and next_page['code'] is None
    assert query.handle(request('foreign', task_id=other.task_id))['code'] == 'inspection_scope_mismatch'
    assert query.handle(request('bad', session_id='another'))['code'] == 'invalid_request'
    assert before == {str(p): p.read_bytes() for p in session.root.rglob('*') if p.is_file()}


def test_attach_inspection_failure_is_a_query_error_and_bad_frames_cannot_escape_scope(tmp_path, monkeypatch):
    from app.runtime.attach_inspection import AttachInspection
    session, task, _, _ = seed(tmp_path)
    query = AttachInspection(session.root, task.task_id)
    for args in ({'cursor': '../outside'}, {'task_id': None}, {'task_id': []}):
        assert query.handle(request(**args))['type'] == 'error'
    assert query.handle({**request(), 'extra': True})['code'] == 'invalid_request'
    def fail(*args, **kwargs):
        raise OSError('unreadable records')
    monkeypatch.setattr(InspectionStore, 'page', fail)
    assert query.handle(request())['code'] == 'inspection_unavailable'


def test_offline_attach_reads_saved_results_and_refuses_new_runs(tmp_path, monkeypatch, capsys):
    from app.runtime.worker import attach_worker
    session, task, _, target = seed(tmp_path)
    before = {str(p): p.read_bytes() for p in session.root.rglob('*') if p.is_file()}
    frames = [request(), request('body', 'plan', action='review', target=target),
              {'v': 1, 'type': 'run', 'id': 'run', 'text': '不能启动'}, {'v': 1, 'type': 'shutdown', 'id': 'close'}]
    monkeypatch.setattr('sys.stdin', io.StringIO(''.join(json.dumps(f) + '\n' for f in frames)))
    assert attach_worker(session.root, task.task_id, mode='control') == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    ready = next(e for e in events if e['type'] == 'ready')
    assert 'attach-offline' in ready['capabilities'] and 'inspection-history' in ready['capabilities']
    snapshot = next(e for e in events if e['type'] == 'task_snapshot')
    assert snapshot['task']['task_id'] == task.task_id
    assert any(e['type'] == 'inspection_history' and len(e['records']) == 3 for e in events)
    assert any(e['type'] == 'plan_review' and e['target'] == target for e in events)
    assert any(e['type'] == 'error' and e['id'] == 'run' and e['code'] == 'attach_readonly' for e in events)
    assert before == {str(p): p.read_bytes() for p in session.root.rglob('*') if p.is_file()}


def test_observer_inspection_replies_are_private_and_never_enter_execution_queue(tmp_path):
    from app.runtime.worker import Worker
    from tests.test_worker_attach_control import _connect
    session, task, other, target = seed(tmp_path)
    worker = Worker(session_root=session.root, task_id=task.task_id, workspace=tmp_path, child_args=[], child_env={})
    before = {str(p): p.read_bytes() for p in session.root.rglob('*') if p.is_file()}

    async def scenario(path):
        server = await asyncio.start_unix_server(worker._client, path=path)
        writers = []
        try:
            caps = ['task_snapshot', 'inspection-history', 'plan-review']
            first, writer, _ = await _connect(path, 'observe', capabilities=caps)
            second, watcher, _ = await _connect(path, 'observe', capabilities=caps)
            writers.extend([writer, watcher])
            for frame, kind in [(request(), 'inspection_history'),
                                (request('body', 'plan', action='review', target=target), 'plan_review'),
                                (request('foreign', task_id=other.task_id), 'error'),
                                ({'v': 1, 'type': 'run', 'id': 'run', 'text': '不能运行'}, 'error')]:
                writer.write((json.dumps(frame) + '\n').encode())
                await writer.drain()
                reply = json.loads(await asyncio.wait_for(first.readline(), 2))
                assert reply['type'] == kind
                if kind == 'inspection_history':
                    assert all(r['scope']['taskId'] == task.task_id for r in reply['records'])
                elif kind == 'plan_review':
                    assert reply['target'] == target and reply['available_actions'] == []
                else:
                    assert reply['code'] == ('observer_readonly' if frame['id'] == 'run' else 'inspection_scope_mismatch')
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(second.readline(), 0.1)
            assert worker.queue.empty()
        finally:
            for writer in writers:
                writer.close()
                await writer.wait_closed()
            server.close()
            await server.wait_closed()

    with tempfile.TemporaryDirectory(prefix='vibe-query-') as folder:
        asyncio.run(scenario(str(Path(folder) / 'socket')))
    assert before == {str(p): p.read_bytes() for p in session.root.rglob('*') if p.is_file()}
