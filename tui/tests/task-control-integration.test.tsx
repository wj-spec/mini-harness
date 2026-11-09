import assert from 'node:assert/strict';
import {execFileSync, spawn} from 'node:child_process';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as pause} from 'node:timers/promises';
import {test} from 'node:test';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {App} from '../src/app.js';
import {createBackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const python = path.join(root, '.venv/bin/python3');
async function until(check: () => boolean | Promise<boolean>) {
  const end = Date.now() + 15_000;
  while (Date.now() < end) {if (await check()) return; await pause(25);}
  throw new Error('task control timeout');
}

test('actual task selection approves only its worker, detaches without stopping, then stops the selected run', async () => {
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-task-control-'));
  const env = {VIBE_TASK_RUNTIME: '0', VIBE_FAKE_DEMO: '', VIBE_FAKE_DELAY_SECONDS: '0', VIBE_FAKE_STREAM_ERRORS: '0',
    VIBE_WORKER_SOCKET_DIR: path.join(workspace, 'sockets')};
  const saved = Object.fromEntries(Object.keys(env).map(key => [key, process.env[key]])); Object.assign(process.env, env);
  const seed = JSON.parse(execFileSync(python, ['-c', `
import json,sys,subprocess
from pathlib import Path
from app.session.store import SessionStore
from app.runtime.tasks import TaskStore
from core.contracts import Message
w=Path(sys.argv[1]);subprocess.run(['git','init','-q',str(w)],check=True)
(w/'.gitignore').write_text('.vibe/'+chr(10)+'sockets/'+chr(10))
subprocess.run(['git','-C',str(w),'add','.gitignore'],check=True)
subprocess.run(['git','-C',str(w),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture'],check=True)
s=SessionStore.new(w,'fake',purpose='test');s.append_message(Message('user','原会话'))
t=TaskStore(s.root).create(kind='run',mode='act',objective='后台审批与停止',workspace=str(w))
print(json.dumps({'session':s.id,'task':t.task_id,'path':str(s.root)}))
`, workspace], {cwd: root, encoding: 'utf8'}));
  const worker = spawn(python, ['-m', 'app', '--worker', '--fake', '--autonomy', 'ask', '--workspace', workspace, '--session', seed.session, '--task-id', seed.task],
    {cwd: root, env: {...process.env, VIBE_TASK_RUNTIME: '1', VIBE_FAKE_DEMO: '1', VIBE_FAKE_DELAY_SECONDS: '4'}, stdio: ['ignore', 'pipe', 'pipe']});
  let diagnostics = ''; worker.stderr.on('data', value => {diagnostics += value;});
  const client = createBackendClient({repoRoot: root, args: ['--fake', '--workspace', workspace, '--session', seed.session]});
  const events: ServerEvent[] = []; client.subscribe(event => events.push(event));
  const view = render(<App client={client} windowSize={{columns: 100, rows: 30}} publishSidebarSignal={() => {}}/>);
  const key = async (value: string) => {view.stdin.write(value); await pause(40);};
  const click = async (label: string) => {
    try {await until(() => Boolean(view.lastFrame()?.includes(label)));} catch (error) {throw new Error(`${String(error)}: ${label}\n${view.lastFrame()}\n${diagnostics}`);}
    await pause(30); const lines = view.lastFrame()!.split('\n'); const row = lines.findIndex(line => line.includes(label));
    const col = stringWidth(lines[row]!.slice(0, lines[row]!.indexOf(label))) + 2;
    await key(`\x1b[<0;${col};${row + 1}M\x1b[<0;${col};${row + 1}m`);
  };
  try {
    const directory = path.join(seed.path, 'tasks', seed.task);
    try {
      await until(async () => {try {return (await readFile(path.join(directory, 'approvals.jsonl'), 'utf8')).split('\n').some(line => line && JSON.parse(line).type === 'requested');} catch {return false;}});
    } catch (error) {throw new Error(`${String(error)}; worker exit=${worker.exitCode}\n${diagnostics}`);}
    client.start(); await until(() => events.some(e => e.type === 'status' && e.phase === 'idle'));
    const restored = (await readFile(path.join(seed.path, 'events.jsonl'), 'utf8')).split('\n').filter(Boolean).map(line => JSON.parse(line));
    assert.equal(restored.some(event => event.type === 'recovered_tool'), false, 'opening the directory must not mark a live worker tool as crashed');
    await key('原会话中文草稿'); await key('\x1b[D'); await click('[ 任务 ]'); await click('[ 接入任务 ]');
    await click('[ 允许本次写入 ]'); await click('[ 返回原会话 ]');
    assert.equal(worker.exitCode, null, diagnostics + await readFile(path.join(directory, 'worker-events.jsonl'), 'utf8'));
    await click('[ 返回 ]'); await key('中'); assert.match(view.lastFrame()!, /原会话中文草中稿/);
    await click('[ 任务 ]'); await click('[ 停止此任务 ]');
    await until(() => worker.exitCode !== null); assert.equal(worker.exitCode, 0, diagnostics);
    await until(() => Boolean(view.lastFrame()?.includes('离线只读')));
    const task = JSON.parse(await readFile(path.join(directory, 'meta.json'), 'utf8'));
    assert.equal(task.status, 'paused');
    assert.equal(task.segments.at(-1).stop_reason, 'cancelled');
    const worktree = JSON.parse(await readFile(path.join(directory, 'worktree.json'), 'utf8'));
    assert.equal(await readFile(path.join(worktree.path, '.vibe/fake-demo.txt'), 'utf8'), 'fake demo\n');
    assert.equal(events.some(e => e.type === 'done' || e.type === 'approval_request'), false);
    await click('[ 返回原会话 ]'); await click('[ 返回 ]'); assert.match(view.lastFrame()!, /原会话中文草中稿/);
  } finally {
    await client.shutdown(); view.unmount(); cleanup();
    if (worker.exitCode === null) {worker.kill('SIGTERM'); await new Promise(resolve => worker.once('close', resolve));}
    for (const [key, value] of Object.entries(saved)) {if (value === undefined) delete process.env[key]; else process.env[key] = value;}
    await rm(workspace, {recursive: true, force: true});
  }
});
