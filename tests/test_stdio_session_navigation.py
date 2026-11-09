"""通过 stdio 浏览旧会话，不启动或恢复任务。"""
import asyncio
import json

from app.config import Config
from app.session.store import SessionStore
from core.contracts import Message, Response, ToolCall
from tests.test_stdio import Harness, agent_for


def test_session_catalog_previews_and_pages_only_its_purpose_while_approval_waits(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TASK_RUNTIME', '0')
    records = [SessionStore.new(tmp_path, 'fake', purpose='test') for _ in range(6)]
    for index, session in enumerate(records):
        session.append_message(Message('user', f'旧会话目标 {index}'))
        session.append_message(Message('assistant', f'旧会话结果 {index}'))
    empty = SessionStore.new(tmp_path, 'fake', purpose='test')
    private = SessionStore.new(tmp_path, 'real', purpose='user')
    private.append_message(Message('user', '真实会话的私有内容'))
    current = SessionStore.new(tmp_path, 'fake', purpose='test')
    agent = agent_for(tmp_path, [Response(tool_calls=[ToolCall('write_file', {'path': 'not-written', 'content': 'x'}, 'pending')]), '结束'])

    async def scenario():
        h = Harness(agent, Config(workspace=tmp_path), current)
        try:
            ready = await h.until('ready')
            assert 'session-navigation' in ready['capabilities']
            h.send('run', 'pending', text='当前等待审批')
            approval = await h.until('approval_request', 'pending')
            before = {str(p): p.read_bytes() for p in (tmp_path / '.vibe/sessions').rglob('*') if p.is_file()}
            h.send('command', 'page', command='sessions', args={})
            first = await h.until('session_catalog', 'page')
            assert first['workspace'] == str(tmp_path.resolve()) and first['session_id'] == current.id
            assert first['purpose'] == 'test' and len(first['records']) == 5
            assert first['records'][0]['session_id'] == current.id
            h.send('command', 'older', command='sessions', args={'cursor': first['next_cursor']})
            second = await h.until('session_catalog', 'older')
            rows = first['records'] + second['records']
            assert len(rows) == 8 and second['next_cursor'] is None
            assert {row['session_id'] for row in rows} == {s.id for s in [*records, empty, current]}
            assert all(len(row['identity']) == 64 for row in rows)
            assert next(row for row in rows if row['session_id'] == empty.id)['message_count'] == 0
            assert '旧会话结果 0' in next(row for row in rows if row['session_id'] == records[0].id)['preview']
            assert '真实会话' not in json.dumps(rows, ensure_ascii=False)
            assert before == {str(p): p.read_bytes() for p in (tmp_path / '.vibe/sessions').rglob('*') if p.is_file()}
            assert len(agent.provider.seen) == 1 and not (tmp_path / 'not-written').exists()
            h.send('approval', 'deny', approval_id=approval['approval_id'], decision='deny')
            await h.until('done', 'pending')
        finally:
            await h.close()

    asyncio.run(scenario())


def test_session_catalog_rejects_scope_changes_and_reports_damaged_records(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TASK_RUNTIME', '0')
    current = SessionStore.new(tmp_path, 'fake', purpose='test')
    for index in range(6):
        store = SessionStore.new(tmp_path, 'fake', purpose='test')
        store.append_message(Message('user', f'完好 {index}'))
    broken = SessionStore.new(tmp_path, 'fake', purpose='test')
    broken.events_path.write_text('{broken\n')
    foreign = SessionStore.new(tmp_path, 'fake', purpose='test')
    metadata = json.loads(foreign.meta_path.read_text())
    metadata['workspace'] = str(tmp_path / 'another')
    foreign.meta_path.write_text(json.dumps(metadata))
    agent = agent_for(tmp_path, ['不应调用'])

    async def scenario():
        h = Harness(agent, Config(workspace=tmp_path), current)
        try:
            await h.until('ready')
            h.send('command', 'first', command='sessions', args={})
            first = await h.until('session_catalog', 'first')
            assert first['skipped'] == 2
            for index, args in enumerate([{'purpose': 'user'}, {'cursor': None}, {'cursor': '../x'}, [], {'session_id': foreign.id}]):
                h.send('command', f'bad-{index}', command='sessions', args=args)
                assert (await h.until('error', f'bad-{index}'))['code'] == 'invalid_request'
            user = SessionStore.new(tmp_path, 'real', purpose='user')
            other = Harness(agent_for(tmp_path, []), Config(workspace=tmp_path), user)
            try:
                await other.until('ready')
                other.send('command', 'cross-purpose', command='sessions', args={'cursor': first['next_cursor']})
                assert (await other.until('error', 'cross-purpose'))['code'] == 'invalid_request'
            finally:
                await other.close()
            assert not agent.provider.seen
        finally:
            await h.close()

    asyncio.run(scenario())
