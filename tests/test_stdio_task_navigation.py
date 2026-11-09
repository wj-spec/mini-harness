"""通过 stdio 公开边界核对只读任务导航。"""
import asyncio

from app.config import Config
from app.runtime.tasks import TaskStore
from app.session.store import SessionStore
from core.contracts import Response, ToolCall
from tests.test_stdio import Harness, agent_for


def test_task_catalog_is_paginated_without_consuming_a_live_approval(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TASK_RUNTIME', '0')
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    tasks = TaskStore(session.root)
    records = [tasks.create(kind='run', objective=f'保留的任务 {index}', workspace=str(tmp_path)) for index in range(7)]
    agent = agent_for(tmp_path, [Response(tool_calls=[ToolCall('write_file', {'path': 'blocked.txt', 'content': 'no'}, 'w')]), '未执行'])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            ready = await harness.until('ready')
            assert 'task-navigation' in ready['capabilities']
            harness.send('run', 'current', text='等待我审批')
            approval = await harness.until('approval_request', 'current')
            before = {str(p): p.read_bytes() for p in tasks.tasks_root.rglob('*') if p.is_file()}
            harness.send('command', 'first-page', command='tasks', args={})
            first = await harness.until('task_catalog', 'first-page')
            assert first['workspace'] == str(tmp_path.resolve()) and first['session_id'] == session.id
            assert [row['task_id'] for row in first['records']] == [record.task_id for record in records[::-1][:5]]
            assert first['records'][0]['objective'] == '保留的任务 6'
            assert first['records'][0]['worker'] is None and first['records'][0]['pending_approvals'] == 0
            harness.send('command', 'second-page', command='tasks', args={'cursor': first['next_cursor']})
            second = await harness.until('task_catalog', 'second-page')
            assert [row['task_id'] for row in second['records']] == [records[1].task_id, records[0].task_id]
            assert second['next_cursor'] is None and second['skipped'] == 0
            assert before == {str(p): p.read_bytes() for p in tasks.tasks_root.rglob('*') if p.is_file()}
            assert len(agent.provider.seen) == 1 and not (tmp_path / 'blocked.txt').exists()
            assert not any(event['type'] == 'done' for event in harness.events)
            harness.send('approval', 'deny', approval_id=approval['approval_id'], decision='deny')
            await harness.until('done', 'current')
            assert not (tmp_path / 'blocked.txt').exists()
        finally:
            await harness.close()
    asyncio.run(scenario())


def test_task_catalog_rejects_foreign_scope_and_reports_damaged_records(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv('VIBE_TASK_RUNTIME', '0')
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    store = TaskStore(session.root)
    good = store.create(kind='run', objective='原会话任务', workspace=str(tmp_path))
    foreign = store.create(kind='run', objective='别的会话', workspace=str(tmp_path), session_id='another-session')
    bad = store.create(kind='run', objective='坏任务', workspace=str(tmp_path))
    (store.task_dir(bad.task_id) / 'meta.json').write_text('{}')
    (store.task_dir(good.task_id) / 'worker.json').write_text('{bad worker')
    agent = agent_for(tmp_path, ['不应调用'])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until('ready')
            before = {str(p): p.read_bytes() for p in store.tasks_root.rglob('*') if p.is_file()}
            for index, args in enumerate([{'cursor': None}, {'cursor': '../../task'}, {'session_id': 'another-session'}, {'cursor': 'e30'}, []]):
                harness.send('command', f'bad-{index}', command='tasks', args=args)
                error = await harness.until('error', f'bad-{index}')
                assert error['code'] == 'invalid_request'
            harness.send('command', 'page', command='tasks', args={})
            result = await harness.until('task_catalog', 'page')
            assert result['skipped'] == 2
            assert [row['task_id'] for row in result['records']] == [good.task_id]
            assert result['records'][0]['worker_error'] and result['records'][0]['worker'] is None
            assert before == {str(p): p.read_bytes() for p in store.tasks_root.rglob('*') if p.is_file()}
            assert not agent.provider.seen
        finally:
            await harness.close()
    asyncio.run(scenario())


def test_task_catalog_reports_foreign_worker_and_unreliable_approval_count(tmp_path, monkeypatch):
    import os
    from datetime import datetime, timezone
    from app.runtime.approvals import ApprovalInbox
    from app.runtime.supervise import WorkerState, write_worker_state
    monkeypatch.setenv('VIBE_TASK_RUNTIME', '0')
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    store = TaskStore(session.root)
    record = store.create(kind='run', objective='异机任务', workspace=str(tmp_path))
    stamp = datetime.now(timezone.utc).isoformat()
    write_worker_state(store.task_dir(record.task_id), WorkerState(1, record.task_id, os.getpid(), 'another-host', 'waiting_approval', stamp, stamp))
    inbox = ApprovalInbox(session.root); inbox.submit(record.task_id, tool='write_file')
    agent = agent_for(tmp_path, ['不应运行'])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until('ready')
            harness.send('command', 'first', command='tasks', args={})
            first = await harness.until('task_catalog', 'first')
            assert first['records'][0]['pending_approvals'] == 1
            assert first['records'][0]['worker'] == {'status': 'waiting_approval', 'foreign_host': True, 'pid_alive': None, 'stale': None}
            with inbox.path_for(record.task_id).open('a') as stream:
                stream.write('{broken approval\n')
            harness.send('command', 'second', command='tasks', args={})
            second = await harness.until('task_catalog', 'second')
            assert second['records'][0]['pending_approvals'] is None
            assert second['problems'] and not agent.provider.seen
        finally:
            await harness.close()
    asyncio.run(scenario())


def test_task_catalog_distinguishes_live_control_offline_history_and_unavailable_tasks(tmp_path, monkeypatch):
    import os
    import socket
    from datetime import datetime, timezone
    from app.runtime.inspection import InspectionStore
    from app.runtime.supervise import WorkerState, write_worker_state
    monkeypatch.setenv('VIBE_TASK_RUNTIME', '0')
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    store = TaskStore(session.root)
    live = store.create(kind='run', objective='本机运行', workspace=str(tmp_path))
    store.start_segment(live.task_id, run_id='live-run')
    offline = store.create(kind='run', objective='已保存结果', workspace=str(tmp_path))
    absent = store.create(kind='run', objective='尚未启动', workspace=str(tmp_path))
    foreign = store.create(kind='run', objective='异机运行', workspace=str(tmp_path))
    stamp = datetime.now(timezone.utc).isoformat()
    for record, host in [(live, socket.gethostname()), (foreign, 'other-host')]:
        write_worker_state(store.task_dir(record.task_id), WorkerState(1, record.task_id, os.getpid(), host, 'running', stamp, stamp))
    InspectionStore(session.root, tmp_path).start('old-run', '已保存结果', task={'task_id': offline.task_id, 'segment': 1, 'status': 'completed'}).finish({'status': 'done'})
    agent = agent_for(tmp_path, ['不应运行'])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until('ready')
            harness.send('command', 'controls', command='tasks', args={})
            page = await harness.until('task_catalog', 'controls')
            rows = {row['task_id']: row for row in page['records']}
            assert rows[live.task_id]['controls'] == {'attach': 'live', 'attach_reason': None, 'stop': True, 'stop_reason': None}
            assert rows[offline.task_id]['controls']['attach'] == 'offline'
            assert rows[offline.task_id]['controls']['stop'] is False
            assert rows[absent.task_id]['controls']['attach'] is None
            assert rows[absent.task_id]['controls']['attach_reason']
            assert rows[foreign.task_id]['controls']['attach'] is None
            assert rows[foreign.task_id]['controls']['stop'] is False
            assert not agent.provider.seen
        finally:
            await harness.close()
    asyncio.run(scenario())
