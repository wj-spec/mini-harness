"""目录选中的会话经实际 CLI 复核后打开，不自动运行。"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys

import pytest

from app.config import Config
from app.session.store import SessionStore
from core.contracts import Message
from tests.test_stdio import Harness, agent_for


async def selected_row(workspace, selected):
    current = SessionStore.new(workspace, 'fake', purpose='test')
    h = Harness(agent_for(workspace, []), Config(workspace=workspace), current)
    try:
        await h.until('ready')
        h.send('command', 'sessions', command='sessions', args={})
        page = await h.until('session_catalog', 'sessions')
        return next(row for row in page['records'] if row['session_id'] == selected.id)
    finally:
        await h.close()


def open_selected(workspace, row):
    return subprocess.run([sys.executable, '-m', 'app', '--stdio', '--fake', '--workspace', str(workspace),
        '--session', row['session_id'], '--session-identity', row['identity']],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, 'VIBE_TASK_RUNTIME': '0', 'VIBE_FAKE_DEMO': '', 'VIBE_WORKER': '',
             'VIBE_MCP_CONFIG': '', 'VIBE_MCP_SERVERS': ''},
        input=json.dumps({'v': 1, 'type': 'shutdown', 'id': 'close'}) + '\n',
        capture_output=True, text=True, timeout=15)


@pytest.mark.parametrize('empty', [False, True])
def test_selected_session_opens_with_its_identity_and_never_starts_a_run(tmp_path, empty):
    selected = SessionStore.new(tmp_path, 'fake', purpose='test')
    if not empty:
        selected.append_message(Message('user', '准确的旧会话'))
        selected.append_message(Message('assistant', '上次已保存的结果'))
    row = asyncio.run(selected_row(tmp_path, selected))
    before = selected.events_path.read_bytes()
    result = open_selected(tmp_path, row)
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    ready = next(event for event in events if event['type'] == 'ready')
    assert ready['session_id'] == selected.id and ready['session_identity'] == row['identity']
    assert ready['session_purpose'] == 'test'
    assert not any(event['type'] in {'tool_call', 'approval_request', 'done'} for event in events)
    assert before == selected.events_path.read_bytes()


@pytest.mark.parametrize('change', ['deleted', 'replaced', 'purpose'])
def test_open_rejects_a_session_that_changed_since_its_preview(tmp_path, change):
    selected = SessionStore.new(tmp_path, 'fake', purpose='test')
    selected.append_message(Message('user', '原会话，不能换成别的'))
    row = asyncio.run(selected_row(tmp_path, selected))
    if change == 'purpose':
        metadata = json.loads(selected.meta_path.read_text())
        metadata['purpose'] = 'user'
        selected.meta_path.write_text(json.dumps(metadata))
    else:
        moved = selected.root.with_name(selected.id + '-original')
        selected.root.rename(moved)
        if change == 'replaced':
            shutil.copytree(moved, selected.root)
    before = {str(path): path.read_bytes() for path in (tmp_path / '.vibe/sessions').rglob('*') if path.is_file()}
    result = open_selected(tmp_path, row)
    assert result.returncode != 0 and '所选会话' in result.stderr
    assert not any(json.loads(line).get('type') == 'ready' for line in result.stdout.splitlines())
    assert before == {str(path): path.read_bytes() for path in (tmp_path / '.vibe/sessions').rglob('*') if path.is_file()}
