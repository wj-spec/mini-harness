"""P10 第二批：跨会话本地检索与 rounds 账本深链。"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.config import Config
from app.runtime.history_search import history_search
from app.runtime.round_ledger import session_rounds
from app.session.store import SessionStore
from core.contracts import Message
from tests.test_stdio import Harness, agent_for


def _message(session, role: str, content: str) -> None:
    session.append_message(Message(role, content))


def test_history_search_matches_messages_across_sessions_with_purpose_isolation(tmp_path):
    current = SessionStore.new(tmp_path, 'fake', purpose='test')
    older = SessionStore.new(tmp_path, 'fake', purpose='test')
    _message(older, 'user', '旧会话：第一结论')
    newer = SessionStore.new(tmp_path, 'fake', purpose='test')
    _message(newer, 'assistant', '新会话：第二结论')
    private = SessionStore.new(tmp_path, 'real', purpose='user')
    _message(private, 'user', '真实会话私有结论')
    broken = SessionStore.new(tmp_path, 'fake', purpose='test')
    broken.events_path.write_text('{broken\n', encoding='utf-8')

    page = history_search(current, tmp_path, {'query': '结论'})
    assert page['session_id'] == current.id and page['purpose'] == 'test'
    assert page['query'] == '结论' and page['skipped'] >= 1 and page['truncated'] is False
    sessions = {match['session_id'] for match in page['matches']}
    assert sessions == {older.id, newer.id}
    assert private.id not in sessions and broken.id not in sessions and current.id not in sessions
    for match in page['matches']:
        assert len(match['identity']) == 64
        assert '结论' in match['snippet'] and match['role'] in {'user', 'assistant', 'tool'}
        assert isinstance(match['message_index'], int) and match['message_index'] >= 0
    from app.runtime.session_navigation import open_selected_session
    first = page['matches'][0]
    assert open_selected_session(tmp_path, first['session_id'], 'test', first['identity']).id == first['session_id']


def test_history_search_pages_by_identity_cursor_and_rejects_bad_args(tmp_path):
    current = SessionStore.new(tmp_path, 'fake', purpose='test')
    many = SessionStore.new(tmp_path, 'fake', purpose='test')
    for index in range(30):
        _message(many, 'user', f'第 {index} 条 关键词')

    page = history_search(current, tmp_path, {'query': '关键词'})
    assert len(page['matches']) == 20 and isinstance(page['next_cursor'], str)
    second = history_search(current, tmp_path, {'query': '关键词', 'cursor': page['next_cursor']})
    assert len(second['matches']) == 10 and second['next_cursor'] is None
    indexes = [match['message_index'] for match in page['matches']] + [match['message_index'] for match in second['matches']]
    assert sorted(indexes) == list(range(30))

    user_current = SessionStore.new(tmp_path, 'real', purpose='user')
    cross_scope = {'query': '关键词', 'cursor': page['next_cursor']}
    for bad in (
        {'query': ''}, {'query': 'x' * 201}, {'query': '关键词', 'cursor': '@@'},
        {'query': '关键词', 'extra': 1}, [], {'query': 123}, cross_scope,
    ):
        with pytest.raises(ValueError):
            history_search(user_current if bad is cross_scope else current, tmp_path, bad)


def test_session_rounds_lists_ledger_newest_first_and_counts_damaged(tmp_path):
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    session.save_round({'schema_version': 1, 'run_id': 'run-1', 'goal': '第一轮目标', 'task_kind': 'run',
                        'status': 'done', 'stop_reason': '', 'steps': 2, 'usage': {}, 'cost_usd': 0.001, 'verified': True})
    session.save_round({'schema_version': 1, 'run_id': 'run-2', 'goal': '第二轮目标', 'task_kind': 'plan',
                        'status': 'error', 'stop_reason': 'model_error', 'steps': 1, 'usage': {}, 'cost_usd': 0.002, 'verified': False})
    (session.rounds_dir / 'round_099.json').write_text('{broken', encoding='utf-8')

    view = session_rounds(session, limit=10)
    assert view['session_id'] == session.id and view['total'] == 2 and view['skipped'] == 1
    assert [entry['index'] for entry in view['rounds']] == [2, 1]
    newest = view['rounds'][0]
    assert newest['goal'] == '第二轮目标' and newest['status'] == 'error'
    assert newest['verified'] is False and newest['run_id'] == 'run-2' and newest['cost_usd'] == 0.002
    assert newest['goal_truncated'] is False and view['truncated'] is False

    for index in range(3, 18):
        session.save_round({'schema_version': 1, 'run_id': f'run-{index}', 'goal': f'目标 {index}',
                            'status': 'done', 'steps': 0, 'usage': {}, 'cost_usd': 0.0, 'verified': None})
    capped = session_rounds(session, limit=5)
    assert capped['total'] == 17 and capped['truncated'] is True and len(capped['rounds']) == 5
    newest_index = max(int(path.stem.split('_', 1)[1]) for path in session.rounds_dir.glob('round_*.json'))
    assert capped['rounds'][0]['index'] == newest_index


def test_stdio_serves_history_search_and_rounds_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_PERMISSIONS', '')
    workspace = tmp_path
    other = SessionStore.new(workspace, 'fake', purpose='test')
    _message(other, 'user', '共享结论内容')
    current = SessionStore.new(workspace, 'fake', purpose='test')
    _message(current, 'user', '当前会话')
    current.save_round({'schema_version': 1, 'run_id': 'run-current', 'goal': '当前轮次',
                        'status': 'done', 'steps': 1, 'usage': {}, 'cost_usd': 0.0, 'verified': True})
    agent = agent_for(workspace, [])

    async def scenario():
        harness = Harness(agent, Config(workspace=workspace), current)
        ready = await harness.until('ready')
        assert 'history-search' in ready['capabilities'] and 'rounds-ledger' in ready['capabilities']
        harness.send('command', 's1', command='search', args={'query': '共享结论'})
        page = await harness.until('history_search', 's1')
        assert page['workspace'] == str(workspace.resolve())
        assert any(match['session_id'] == other.id for match in page['matches'])
        harness.send('command', 's2', command='search', args={'query': ''})
        assert (await harness.until('error', 's2'))['code'] == 'invalid_request'
        harness.send('command', 'r1', command='rounds', args={})
        ledger = await harness.until('rounds_ledger', 'r1')
        assert ledger['session_id'] == current.id and ledger['total'] == 1
        assert ledger['rounds'][0]['goal'] == '当前轮次' and ledger['rounds'][0]['verified'] is True
        harness.send('command', 'r2', command='rounds', args={'extra': 1})
        assert (await harness.until('error', 'r2'))['code'] == 'invalid_request'
        await harness.close()

    asyncio.run(scenario())
