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
import {App} from '../src/app.js';
import {createBackendClient, type BackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const python = path.join(root, '.venv/bin/python3');
async function until(predicate: () => boolean | Promise<boolean>, timeout = 15_000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {if (await predicate()) return; await pause(25);}
  throw new Error('attach integration timed out');
}

test('actual worker attach bootstraps without replay, detaches safely and keeps exact results after exit or reopening', async () => {
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-attach-read-'));
  const env = {VIBE_TASK_RUNTIME: '1', VIBE_FAKE_DEMO: '', VIBE_FAKE_DELAY_SECONDS: '4', VIBE_FAKE_STREAM_ERRORS: '0',
    VIBE_ALLOW_NON_GIT: '1', VIBE_ATTACH_REPLAY_MAX_LINES: '0', VIBE_WORKER_SOCKET_DIR: path.join(workspace, 'sockets')};
  const saved = Object.fromEntries(Object.keys(env).map(key => [key, process.env[key]])); Object.assign(process.env, env);
  const seed = JSON.parse(execFileSync(python, ['-c',
    "from tests.test_attach_inspection import seed; from pathlib import Path; import sys,json; s,t,o,p=seed(Path(sys.argv[1])); print(json.dumps({'session':s.id,'path':str(s.root),'task':t.task_id,'target':p}))",
    workspace], {cwd: root, encoding: 'utf8'}));
  const worker = spawn(python, ['-m', 'app', '--worker', '--fake', '--workspace', workspace, '--session', seed.session, '--task-id', seed.task],
    {cwd: root, env: process.env, stdio: ['ignore', 'pipe', 'pipe']});
  let diagnostics = ''; worker.stderr.on('data', chunk => {diagnostics += chunk;});
  let client: BackendClient | undefined; let view: ReturnType<typeof render> | undefined;
  const connect = () => {
    const events: ServerEvent[] = [];
    client = createBackendClient({repoRoot: root, args: ['--fake', '--workspace', workspace], attach: {session: seed.session, taskId: seed.task}});
    client.subscribe(event => events.push(event)); client.start(); return events;
  };
  const history = async (events: ServerEvent[]) => {
    const id = client!.command('inspections', {task_id: seed.task});
    await until(() => events.some(event => event.id === id && event.type === 'inspection_history'));
    const result = events.find(event => event.id === id) as Extract<ServerEvent, {type: 'inspection_history'}>;
    assert.equal(result.task_id, seed.task); assert.ok(result.records.every(record => record.scope.taskId === seed.task));
    return result;
  };
  try {
    const taskPath = path.join(seed.path, 'tasks', seed.task, 'meta.json');
    await until(async () => JSON.parse(await readFile(taskPath, 'utf8')).status === 'running');
    let events = connect(); await until(() => events.some(event => event.type === 'task_snapshot'));
    assert.ok(client!.capabilities?.().includes('inspection-history'));
    assert.equal(client!.capabilities?.().includes('attach-offline'), false);
    await history(events);
    const bodyId = client!.command('plan', {action: 'review', target: seed.target, cursor: 0});
    await until(() => events.some(event => event.id === bodyId));
    const body = events.find(event => event.id === bodyId) as Extract<ServerEvent, {type: 'plan_review'}>;
    assert.equal(body.type, 'plan_review'); assert.deepEqual(body.target, seed.target); assert.deepEqual(body.available_actions, []);
    assert.match(body.body_page, /准确计划/);
    await client!.shutdown(); assert.equal(worker.exitCode, null, diagnostics);
    events = connect();
    view = render(<App client={client!} attach windowSize={{columns: 100, rows: 30}}
      launchSidebar={() => ({outcome: 'started', message: 'test host'})} publishSidebarSignal={() => {}}/>);
    await until(() => events.some(event => event.type === 'task_snapshot'));
    view.stdin.write('中文草稿'); await pause(40); view.stdin.write('\x1b[D');
    await until(() => client!.capabilities?.().includes('attach-offline') === true);
    await until(() => worker.exitCode !== null); assert.equal(worker.exitCode, 0, diagnostics);
    await pause(60); assert.match(view.lastFrame()!, /离线只读/);
    view.stdin.write('\r'); await pause(40); view.stdin.write('中'); await pause(40);
    assert.match(view.lastFrame()!, /中文草中稿/);
    assert.throws(() => client!.run('不得重新执行'), /离线/);
    await history(events);
    const before = await readFile(taskPath, 'utf8');
    const trace = await readFile(path.join(seed.path, 'trace.jsonl'), 'utf8');
    assert.equal(trace.split('\n').filter(line => line && JSON.parse(line).type === 'llm_start').length, 1);
    await client!.shutdown(); view.unmount(); view = undefined; cleanup();
    events = connect(); await until(() => events.some(event => event.type === 'task_snapshot'));
    assert.ok(client!.capabilities?.().includes('attach-offline')); await history(events);
    assert.equal(await readFile(taskPath, 'utf8'), before);
    assert.equal(await readFile(path.join(seed.path, 'trace.jsonl'), 'utf8'), trace);
    assert.equal(events.some(event => event.type === 'approval_request' || event.type === 'done'), false);
  } finally {
    await client?.shutdown(); view?.unmount(); cleanup();
    if (worker.exitCode === null) {worker.kill('SIGTERM'); await new Promise(resolve => worker.once('close', resolve));}
    for (const [key, value] of Object.entries(saved)) {if (value === undefined) delete process.env[key]; else process.env[key] = value;}
    await rm(workspace, {recursive: true, force: true});
  }
});
