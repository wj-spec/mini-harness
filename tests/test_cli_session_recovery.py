"""实际 CLI 重开不会把活跃或未知 worker 当成崩溃。"""
import json
import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from app.runtime.supervise import WorkerState, write_worker_state
from app.runtime.tasks import TaskStore
from app.session.store import SessionStore
from core.contracts import Message, ToolCall


@pytest.mark.parametrize('worker_kind,repairs', [('live', 0), ('foreign', 0), ('broken', 0), ('dead', 1)])
def test_reopen_repairs_only_when_the_worker_is_known_to_have_exited(tmp_path, worker_kind, repairs):
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    session.append_message(Message('user', '原任务'))
    session.append_message(Message('assistant', tool_calls=[ToolCall('write_file', {'path': 'x', 'content': 'y'}, id='pending-call')]))
    store = TaskStore(session.root)
    task = store.create(kind='run', objective='原任务', workspace=str(tmp_path))
    store.start_segment(task.task_id, run_id='pending-run')
    directory = store.task_dir(task.task_id)
    if worker_kind == 'broken':
        (directory / 'worker.json').write_text('{broken')
    else:
        write_worker_state(directory, WorkerState(1, task.task_id, 1_000_000_000 if worker_kind == 'dead' else os.getpid(),
            'other-host' if worker_kind == 'foreign' else socket.gethostname(), 'running',
            '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00'))
    result = subprocess.run([sys.executable, '-m', 'app', '--stdio', '--fake', '--workspace', str(tmp_path), '--session', session.id],
        cwd=Path(__file__).resolve().parents[1], env={**os.environ, 'VIBE_WORKER': '0', 'VIBE_TASK_RUNTIME': '0',
            'VIBE_FAKE_DEMO': '', 'VIBE_MCP_CONFIG': '', 'VIBE_MCP_SERVERS': ''},
        input=json.dumps({'v': 1, 'type': 'shutdown', 'id': 'close'}) + '\n', capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert any(json.loads(line).get('type') == 'ready' for line in result.stdout.splitlines())
    events = [json.loads(line) for line in session.events_path.read_text().splitlines()]
    assert sum(event['type'] == 'recovered_tool' for event in events) == repairs
    assert len([event for event in events if event['type'] == 'message']) == 2 + repairs


@pytest.mark.parametrize('ending', ['finish', 'kill'])
@pytest.mark.parametrize('task_runtime', ['0', '1'])
def test_live_foreground_reopen_does_not_repair_or_overwrite_its_pending_tool(tmp_path, ending, task_runtime):
    session = SessionStore.new(tmp_path, 'fake', purpose='test')

    async def scenario():
        processes = []

        async def start(demo):
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'app', '--stdio', '--fake', '--autonomy', 'ask',
                '--workspace', str(tmp_path), '--session', session.id,
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, 'VIBE_WORKER': '0', 'VIBE_TASK_RUNTIME': task_runtime,
                     'VIBE_FAKE_DEMO': demo, 'VIBE_FAKE_DELAY_SECONDS': '0',
                     'VIBE_MCP_CONFIG': '', 'VIBE_MCP_SERVERS': ''},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            processes.append(process)
            await until(process, 'ready')
            return process

        async def until(process, kind, request_id=None):
            async with asyncio.timeout(10):
                while True:
                    line = await process.stdout.readline()
                    assert line, ('unexpected backend exit', process.returncode)
                    frame = json.loads(line)
                    if frame['type'] == kind and (request_id is None or frame.get('id') == request_id):
                        return frame

        async def send(process, kind, request_id, **data):
            process.stdin.write((json.dumps({'v': 1, 'type': kind, 'id': request_id, **data}) + '\n').encode())
            await process.stdin.drain()

        try:
            first = await start('1')
            options = {'task': {'kind': 'run', 'mode': 'act', 'objective': '前台持久任务'}} if task_runtime == '1' else {}
            await send(first, 'run', 'first', text='前台审批写入', **options)
            approval = await until(first, 'approval_request', 'first')
            second = await start('')
            events = [json.loads(line) for line in session.events_path.read_text().splitlines()]
            assert not any(event['type'] == 'recovered_tool' for event in events)
            if task_runtime == '1':
                assert TaskStore(session.root).list()[0].status == 'running'
            await send(second, 'run', 'competing', text='不能覆盖前台任务')
            assert (await until(second, 'error', 'competing'))['code'] == 'session_busy'
            if ending == 'finish':
                await send(first, 'approval', 'write', approval_id=approval['approval_id'], decision='once')
                command = await until(first, 'approval_request', 'first')
                await send(first, 'approval', 'deny-command', approval_id=command['approval_id'], decision='deny')
                await until(first, 'done', 'first')
                assert (tmp_path / '.vibe/fake-demo.txt').read_text() == 'fake demo\n'
            else:
                first.kill()
                await first.wait()
                assert not (tmp_path / '.vibe/fake-demo.txt').exists()
            await send(second, 'run', 'retry', text='根据已保存结果继续')
            await until(second, 'done', 'retry')
            context = session.load_context()
            assert any(message.role == 'tool' and message.tool_call_id == 'fake-demo-w1' for message in context)
            assert any(message.role == 'user' and message.content == '根据已保存结果继续' for message in context)
            assert not any(message.role == 'user' and message.content == '不能覆盖前台任务' for message in session.load_messages())
            events = [json.loads(line) for line in session.events_path.read_text().splitlines()]
            assert sum(event['type'] == 'recovered_tool' for event in events) == (1 if ending == 'kill' else 0)
        finally:
            for process in processes:
                if process.returncode is None:
                    process.stdin.close()
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()

    asyncio.run(scenario())
