"""同会话多个 stdio 连接的续做上下文与写入归属。"""
import asyncio
import pytest

from app.config import Config
from app.session.store import SessionStore
from core.contracts import Message, Response, ToolCall
from core.task import TaskState
from tests.test_stdio import Harness
from tests.test_stdio_parity import make_agent


def test_idle_connection_sees_completed_work_before_its_next_model_request(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TASK_RUNTIME', '0')
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    session.append_message(Message('user', '原会话需求'))
    (tmp_path / 'result.txt').write_text('后台读取的唯一结果 7391')
    original = make_agent(tmp_path, ['基于最新结果继续'], session.load_context())
    worker = make_agent(tmp_path, [
        Response(tool_calls=[ToolCall('read_file', {'path': 'result.txt'}, 'read-result')]),
        '后台任务已确认结果 7391',
    ], session.load_context())

    async def scenario():
        main = Harness(original, Config(workspace=tmp_path), session)
        background = Harness(worker, Config(workspace=tmp_path), SessionStore(session.root))
        try:
            await main.until('ready')
            await background.until('ready')
            background.send('run', 'background', text='读取后台结果')
            await background.until('done', 'background')
            assert any(m.content == '后台任务已确认结果 7391' for m in session.load_context())
            main.send('run', 'continue', text='依据刚完成的结果继续')
            await main.until('done', 'continue')
            request = original.provider.seen[-1]
            assert any(m.role == 'assistant' and m.content == '后台任务已确认结果 7391' for m in request)
            assert any(m.role == 'tool' and '后台读取的唯一结果 7391' in m.content for m in request)
        finally:
            await background.close()
            await main.close()

    asyncio.run(scenario())


@pytest.mark.parametrize('attempt', ['run', 'resume', 'task'])
def test_another_connection_cannot_start_while_the_session_waits_for_approval(tmp_path, monkeypatch, attempt):
    monkeypatch.setenv('VIBE_TASK_RUNTIME', '0')
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    worker = make_agent(tmp_path, [Response(tool_calls=[ToolCall(
        'write_file', {'path': 'pending.txt', 'content': 'approved only'}, 'pending-write')]), '已处理'])
    original = make_agent(tmp_path, ['不应调用'])

    async def scenario():
        background = Harness(worker, Config(workspace=tmp_path), session)
        main = Harness(original, Config(workspace=tmp_path), SessionStore(session.root))
        try:
            await background.until('ready')
            await main.until('ready')
            background.send('run', 'pending', text='等待审批的后台任务')
            approval = await background.until('approval_request', 'pending')
            if attempt == 'resume':
                main.send('command', 'competing', command='resume')
            else:
                options = {'task': {'kind': 'run', 'mode': 'act', 'objective': '新的持久任务'}} if attempt == 'task' else {}
                main.send('run', 'competing', text='不能同时开始的新任务', **options)
            async with asyncio.timeout(3):
                while True:
                    reply = await main.outgoing.get()
                    if reply.get('id') == 'competing' and reply['type'] in {'error', 'done'}:
                        break
            assert reply['type'] == 'error' and reply['code'] == 'session_busy', reply
            assert not original.provider.seen
            main.send('command', 'browse', command='tasks', args={})
            await main.until('task_catalog', 'browse')
            background.send('approval', 'deny', approval_id=approval['approval_id'], decision='deny')
            await background.until('done', 'pending')
            main.send('run', 'retry', text='现在继续')
            await main.until('done', 'retry')
            assert len(original.provider.seen) == 1
            assert not (tmp_path / 'pending.txt').exists()
        finally:
            await background.close()
            await main.close()

    asyncio.run(scenario())


def test_refresh_keeps_the_saved_checkpoint_and_latest_task_state(tmp_path, monkeypatch):
    monkeypatch.setenv('VIBE_TASK_RUNTIME', '0')
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    goal = Message('user', '检查接口')
    summary = Message('assistant', '已压缩的接口背景', name='history_summary_l2')
    session.append_message(goal)
    session.append_message(Message('assistant', '压缩前不应重新注入的长原文'))
    session.save_context([goal, summary])
    session.save_state(TaskState(goal='检查接口'))
    original = make_agent(tmp_path, [Response(tool_calls=[ToolCall('update_task', {'note': '原会话续做'}, 'main-update')]), '已继续'],
                          session.load_context(), session.load_state())
    worker = make_agent(tmp_path, [Response(tool_calls=[ToolCall('update_task', {'done': ['后台确认接口'], 'note': '后台新证据'}, 'worker-update')]), '后台完成'],
                        session.load_context(), session.load_state())

    async def scenario():
        main = Harness(original, Config(workspace=tmp_path), session)
        background = Harness(worker, Config(workspace=tmp_path), SessionStore(session.root))
        try:
            await main.until('ready')
            await background.until('ready')
            background.send('run', 'background', text='后台检查')
            await background.until('done', 'background')
            main.send('run', 'continue', text='继续核对')
            await main.until('done', 'continue')
            request = original.provider.seen[0]
            assert summary in request
            assert not any(m.content == '压缩前不应重新注入的长原文' for m in request)
            assert any(m.name == 'task_state' and '后台确认接口' in m.content and '后台新证据' in m.content for m in request)
            assert session.load_state().done == ['后台确认接口']
            assert '原会话续做' in session.load_state().notes
        finally:
            await background.close()
            await main.close()

    asyncio.run(scenario())
