import asyncio
import json

from app.config import Config
from app.runtime.inspection import InspectionStore
from app.session.store import SessionStore
from core.contracts import Response, ToolCall
from tests.test_stdio import Harness, agent_for


def test_inspection_pages_are_readonly_and_do_not_replace_pending_approval(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TASK_RUNTIME', '0')
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    store = InspectionStore(session.root, tmp_path)
    agent = agent_for(tmp_path, [Response(tool_calls=[ToolCall('write_file', {'path': 'blocked.txt', 'content': 'no'}, 'write')]), '未执行'])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            ready = await harness.until('ready')
            assert 'inspection-history' in ready['capabilities']
            harness.send('run', 'active', text='等待审批')
            approval = await harness.until('approval_request', 'active')
            for index in range(5):
                store.start(f'old-{index}', f'旧任务 {index}').finish({'status': 'done'})
            before = {p.name: p.read_bytes() for p in store.root.iterdir()}
            harness.send('command', 'page-1', command='inspections')
            page = await harness.until('inspection_history', 'page-1')
            assert page['session_id'] == session.root.name and page['workspace'] == str(tmp_path.resolve())
            assert [r['scope']['requestId'] for r in page['records']] == ['old-4', 'old-3', 'old-2']
            harness.send('command', 'page-2', command='inspections', args={'cursor': page['next_cursor']})
            older = await harness.until('inspection_history', 'page-2')
            assert [r['scope']['requestId'] for r in older['records']] == ['old-1', 'old-0', 'active']
            assert older['next_cursor'] is None and older['skipped'] == 0
            assert before == {p.name: p.read_bytes() for p in store.root.iterdir()}
            assert not (tmp_path / 'blocked.txt').exists()
            assert not any(e['type'] == 'done' for e in harness.events)
            harness.send('approval', 'deny', approval_id=approval['approval_id'], decision='deny')
            await harness.until('done', 'active')
            assert not (tmp_path / 'blocked.txt').exists()
        finally:
            await harness.close()
    asyncio.run(scenario())


def test_inspection_history_rejects_foreign_arguments_and_reports_corruption(tmp_path):
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    store = InspectionStore(session.root, tmp_path)
    store.start('broken', 'broken').finish({'status': 'done'})
    file = next(store.root.glob('*.json'))
    file.write_text('{}')
    agent = agent_for(tmp_path, ['ok'])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until('ready')
            for index, args in enumerate([{'cursor': '../../other'}, {'session_id': 'foreign'}, {'task_id': []}, {'cursor': None}, {'task_id': None}]):
                identity = f'bad-{index}'
                harness.send('command', identity, command='inspections', args=args)
                error = await harness.until('error', identity)
                assert error['code'] == 'invalid_request'
            harness.send('command', 'read', command='inspections', args={'task_id': 'original'})
            page = await harness.until('inspection_history', 'read')
            assert page['records'] == [] and page['skipped'] == 1 and page['next_cursor'] is None
            assert agent.provider.seen == [] and file.read_text() == '{}'
        finally:
            await harness.close()
    asyncio.run(scenario())


def test_inspection_read_failure_keeps_the_connection_usable(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('unreadable inspection directory')
    monkeypatch.setattr(InspectionStore, 'page', fail)
    agent = agent_for(tmp_path, ['连接仍可用'])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path))
        try:
            await harness.until('ready')
            harness.send('command', 'history', command='inspections')
            error = await harness.until('error', 'history')
            assert error['code'] == 'inspection_unavailable'
            harness.send('run', 'next', text='只回答')
            done = await harness.until('done', 'next')
            assert done['result']['text'] == '连接仍可用'
        finally:
            await harness.close()
    asyncio.run(scenario())


def test_inspection_page_stays_within_protocol_frame_limit(tmp_path):
    from app.stdio import MAX_FRAME_BYTES
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    store = InspectionStore(session.root, tmp_path)
    for index in range(4):
        record = store.start(f'large-{index}', '界' * 4000)
        for command in range(20):
            record.observe('tool_result', call={'name': 'bash', 'id': str(command), 'args': {'command': '验' * 1000}}, output='证' * 2000, ok=True)
        record.finish({'status': 'done'})

    async def scenario():
        harness = Harness(agent_for(tmp_path, ['ok']), Config(workspace=tmp_path), session)
        try:
            await harness.until('ready')
            harness.send('command', 'large', command='inspections')
            page = await harness.until('inspection_history', 'large')
            assert len(page['records']) == 3 and page['next_cursor']
            assert len(json.dumps(page, ensure_ascii=False).encode()) < MAX_FRAME_BYTES
        finally:
            await harness.close()
    asyncio.run(scenario())
