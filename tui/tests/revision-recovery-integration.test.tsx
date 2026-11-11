import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {chmod, mkdtemp, readFile, readdir, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as pause} from 'node:timers/promises';
import {test} from 'node:test';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import {App} from '../src/app.js';
import {createBackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
async function waitFor(condition: () => boolean, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error('timed out waiting for recovery');
    await pause(25);
  }
}

// 在进程传输边界丢弃成功回执；实际后端与模型脚本保持原样。
async function lostReceiptRelay(root: string) {
  const launcher = path.join(root, 'run_vibe.sh');
  await writeFile(launcher, `#!/usr/bin/env python3
import json, signal, subprocess, sys, threading
from pathlib import Path
child = subprocess.Popen(['./run_vibe.sh', *sys.argv[1:]], cwd=${JSON.stringify(repoRoot)}, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
def forward():
    try:
        for line in sys.stdin.buffer:
            child.stdin.write(line)
            child.stdin.flush()
        child.stdin.close()
    except (BrokenPipeError, OSError):
        pass
threading.Thread(target=forward, daemon=True).start()
signal.signal(signal.SIGTERM, lambda *_: child.terminate())
try:
    for line in child.stdout:
        frame = json.loads(line)
        if frame.get('type') == 'plan_revision' and frame.get('outcome') == 'submitted':
            Path(__file__).with_name('lost-receipt.json').write_text(json.dumps(frame))
            child.kill()
            child.wait(timeout=5)
            sys.exit(77)
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
finally:
    if child.poll() is None:
        child.kill()
    child.wait(timeout=5)
`);
  await chmod(launcher, 0o700);
}

test('Ink recovers a published revision after its success receipt is lost and the backend exits', async t => {
  if (!existsSync(path.join(repoRoot, '.venv/bin/python3'))) {t.skip('Python backend unavailable'); return;}
  const previous = {runtime: process.env.VIBE_TASK_RUNTIME, demo: process.env.VIBE_FAKE_DEMO, delay: process.env.VIBE_FAKE_DELAY_SECONDS};
  process.env.VIBE_TASK_RUNTIME = '1'; process.env.VIBE_FAKE_DEMO = 'revision'; process.env.VIBE_FAKE_DELAY_SECONDS = '0';
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-receipt-loss-'));
  const relay = await mkdtemp(path.join(tmpdir(), 'vibe-receipt-relay-'));
  await lostReceiptRelay(relay);
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot: relay, args: ['--fake', '--workspace', workspace, '--autonomy', 'ask']});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client} windowSize={{columns: 80, rows: 24}}/>);
  const key = async (text: string) => {view.stdin.write(text); await pause(35);};
  let reopened: ReturnType<typeof createBackendClient> | undefined;
  try {
    client.start(); await waitFor(() => events.some(event => event.type === 'status' && event.phase === 'idle'));
    const ready = events.find(event => event.type === 'ready');
    assert.ok(ready?.type === 'ready' && ready.session_path);
    const sessionRoot = ready.session_path;
    await key('\r'); await waitFor(() => view.lastFrame()?.includes('计划 v1') ?? false);
    const source = events.find(event => event.type === 'plan_review' && event.target);
    assert.ok(source?.type === 'plan_review' && source.target);
    const planDir = path.join(ready.session_path, 'tasks', source.target.plan_task_id, 'plan');
    const original = await readFile(path.join(planDir, 'plan.v1.json'), 'utf8');
    await key('\u0012'); await key('r'); await key('\r');
    await key('将演示内容改为 revised demo'); await key('\r'); await key('\r');
    await waitFor(() => events.some(event => event.type === 'closed'));
    assert.equal(events.some(event => event.type === 'plan_revision' && event.outcome === 'submitted'), false);
    const lost = JSON.parse(await readFile(path.join(relay, 'lost-receipt.json'), 'utf8'));
    assert.equal(lost.outcome, 'submitted'); assert.equal(lost.result_target.plan_version, 2);
    const published = await readFile(path.join(planDir, 'plan.v2.json'), 'utf8');
    assert.match(published, /revised demo/);
    await client.shutdown(); view.unmount();
    const countCalls = async () => (await readFile(path.join(sessionRoot, 'trace.jsonl'), 'utf8')).split('\n').filter(line => line && JSON.parse(line).type === 'llm_start').length;
    assert.equal(await countCalls(), 2);
    const restored: ServerEvent[] = [];
    reopened = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace, '--session', ready.session_id]});
    reopened.subscribe(event => restored.push(event));
    const next = render(<App client={reopened} windowSize={{columns: 80, rows: 24}}/>);
    const nextKey = async (text: string) => {next.stdin.write(text); await pause(35);};
    reopened.start(); await waitFor(() => next.lastFrame()?.includes('计划 v2') ?? false);
    await nextKey('\u001b');
    await nextKey('\u0015'); await nextKey('重开后的中文草稿'); await nextKey('\u0012'); await nextKey('h'); await nextKey('\r');
    await waitFor(() => restored.some(event => event.type === 'plan_revision_history'));
    const history = restored.find(event => event.type === 'plan_revision_history');
    assert.ok(history?.type === 'plan_revision_history'); assert.equal(history.records.length, 1);
    const record = history.records[0]!;
    assert.deepEqual(record.target, source.target); assert.deepEqual(record.result_target, lost.result_target);
    assert.equal(record.task?.task_id, lost.task.task_id); assert.equal(record.task?.segment, 1);
    assert.equal(record.task?.run_started, false); assert.equal(record.outcome, 'submitted');
    assert.match(next.lastFrame()!, /将演示内容改为 revised demo/);
    await nextKey('\t'); await nextKey('\r');
    assert.doesNotMatch(next.lastFrame()!, /续做/);
    await nextKey('\t'); await nextKey('\u001b[C'); await nextKey('\r');
    await waitFor(() => restored.some(event => event.type === 'plan_review' && event.target?.plan_version === 2));
    await nextKey('\u0012'); await nextKey('b'); await nextKey('\r');
    assert.match(next.lastFrame()!, /计划 v2/); assert.match(next.lastFrame()!, /重开后的中文草稿/);
    assert.equal(await countCalls(), 2);
    assert.equal(await readFile(path.join(planDir, 'plan.v1.json'), 'utf8'), original);
    assert.equal(await readFile(path.join(planDir, 'plan.v2.json'), 'utf8'), published);
    assert.equal(existsSync(path.join(planDir, 'plan.v3.json')), false);
    const tasks = (await readdir(path.join(ready.session_path, 'tasks'))).filter(name => name.startsWith('t-'));
    assert.equal(tasks.length, 2);
    const task = JSON.parse(await readFile(path.join(ready.session_path, 'tasks', lost.task.task_id, 'meta.json'), 'utf8'));
    assert.equal(task.segments.length, 1);
    assert.equal(existsSync(path.join(workspace, '.vibe/plan-demo.txt')), false);
    assert.equal(restored.some(event => event.type === 'error' || event.type === 'approval_request'), false);
  } finally {
    await reopened?.shutdown(); await client.shutdown(); cleanup();
    await rm(workspace, {recursive: true, force: true}); await rm(relay, {recursive: true, force: true});
    for (const [key, value] of [['VIBE_TASK_RUNTIME', previous.runtime], ['VIBE_FAKE_DEMO', previous.demo], ['VIBE_FAKE_DELAY_SECONDS', previous.delay]] as const) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
});

test('Ink rejects a restored revision when another backend publishes a new version before resume', async t => {
  if (!existsSync(path.join(repoRoot, '.venv/bin/python3'))) {t.skip('Python backend unavailable'); return;}
  const previous = {runtime: process.env.VIBE_TASK_RUNTIME, demo: process.env.VIBE_FAKE_DEMO, delay: process.env.VIBE_FAKE_DELAY_SECONDS};
  process.env.VIBE_TASK_RUNTIME = '1'; process.env.VIBE_FAKE_DEMO = 'revision'; process.env.VIBE_FAKE_DELAY_SECONDS = '1';
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-revision-competition-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client} windowSize={{columns: 80, rows: 24}}/>);
  const key = async (text: string) => {view.stdin.write(text); await pause(35);};
  let reopened: ReturnType<typeof createBackendClient> | undefined;
  let other: ReturnType<typeof createBackendClient> | undefined;
  try {
    client.start(); await waitFor(() => events.some(event => event.type === 'status' && event.phase === 'idle'));
    const ready = events.find(event => event.type === 'ready');
    assert.ok(ready?.type === 'ready' && ready.session_path);
    const sessionRoot = ready.session_path;
    await key('\r'); await waitFor(() => view.lastFrame()?.includes('计划 v1') ?? false);
    const source = events.find(event => event.type === 'plan_review' && event.target);
    assert.ok(source?.type === 'plan_review' && source.target);
    const planDir = path.join(sessionRoot, 'tasks', source.target.plan_task_id, 'plan');
    const original = await readFile(path.join(planDir, 'plan.v1.json'), 'utf8');
    await key('\u0012'); await key('r'); await key('\r'); await key('原反馈：增加空文件检查'); await key('\r'); await key('\r');
    await waitFor(() => events.some(event => event.type === 'plan_revision' && event.task?.run_started));
    await pause(80); await key('\u0003');
    await waitFor(() => events.some(event => event.type === 'done' && event.result.status === 'cancelled'));
    const initial = events.find(event => event.type === 'plan_revision' && event.task?.run_started);
    assert.ok(initial?.type === 'plan_revision' && initial.task);
    await client.shutdown(); view.unmount();
    const countCalls = async () => (await readFile(path.join(sessionRoot, 'trace.jsonl'), 'utf8')).split('\n').filter(line => line && JSON.parse(line).type === 'llm_start').length;
    assert.equal(await countCalls(), 2);
    process.env.VIBE_FAKE_DEMO = 'revision-resume'; process.env.VIBE_FAKE_DELAY_SECONDS = '0';
    const restored: ServerEvent[] = [];
    reopened = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace, '--session', ready.session_id]});
    reopened.subscribe(event => restored.push(event));
    const next = render(<App client={reopened} windowSize={{columns: 80, rows: 24}}/>);
    const nextKey = async (text: string) => {next.stdin.write(text); await pause(35);};
    reopened.start(); await waitFor(() => next.lastFrame()?.includes('计划 v1') ?? false);
    await nextKey('\u001b');
    await nextKey('竞争时保留的普通草稿'); await nextKey('\u0012'); await nextKey('h'); await nextKey('\r');
    await waitFor(() => restored.some(event => event.type === 'plan_revision_history'));
    await nextKey('\t'); await nextKey('\r');
    assert.match(next.lastFrame()!, /续做/); assert.match(next.lastFrame()!, /原反馈：增加空文件检查/);
    assert.equal(await countCalls(), 2);

    const competing: ServerEvent[] = [];
    other = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace, '--session', ready.session_id]});
    other.subscribe(event => competing.push(event)); other.start();
    await waitFor(() => competing.some(event => event.type === 'ready'));
    const request = other.command('plan', {action: 'request-changes', target: source.target, feedback: '另一个窗口：调整演示内容'});
    await waitFor(() => competing.some(event => event.type === 'done' && event.id === request));
    const submitted = competing.find(event => event.type === 'plan_revision' && event.outcome === 'submitted');
    assert.ok(submitted?.type === 'plan_revision' && submitted.result_target && submitted.task);
    assert.notEqual(submitted.task.task_id, initial.task.task_id);
    const published = await readFile(path.join(planDir, 'plan.v2.json'), 'utf8');
    assert.equal(await countCalls(), 3);

    await nextKey('\t'); await nextKey('\u001b[C'); await nextKey('\r');
    await waitFor(() => restored.some(event => event.type === 'plan_revision' && event.code === 'superseded'));
    assert.match(next.lastFrame()!, /原反馈：增加空文件检查/); assert.match(next.lastFrame()!, /竞争时保留的普通草稿/);
    assert.doesNotMatch(next.lastFrame()!, /续做/);
    assert.equal(await countCalls(), 3);
    assert.equal(restored.some(event => event.type === 'plan_revision' && event.task?.run_started), false);
    await nextKey('\u001b'); await nextKey('\u0012'); await nextKey('q'); await nextKey('\r');
    await waitFor(() => restored.some(event => event.type === 'plan_review' && event.code === 'superseded'));
    await nextKey('v'); await nextKey('\r');
    await waitFor(() => restored.some(event => event.type === 'plan_review' && event.target?.plan_version === 2));
    await nextKey('\u0012'); await nextKey('h'); await nextKey('\r');
    await waitFor(() => restored.filter(event => event.type === 'plan_revision_history').length === 2);
    const history = restored.filter(event => event.type === 'plan_revision_history').at(-1);
    assert.ok(history?.type === 'plan_revision_history'); assert.equal(history.records.length, 2);
    assert.equal(history.target?.plan_version, 2);
    const old = history.records.find(record => record.feedback === '原反馈：增加空文件检查');
    assert.deepEqual(old?.target, source.target); assert.equal(old?.code, 'superseded');
    const newer = history.records.find(record => record.feedback === '另一个窗口：调整演示内容');
    assert.deepEqual(newer?.target, source.target); assert.deepEqual(newer?.result_target, submitted.result_target);
    assert.equal(await readFile(path.join(planDir, 'plan.v1.json'), 'utf8'), original);
    assert.equal(await readFile(path.join(planDir, 'plan.v2.json'), 'utf8'), published);
    assert.equal(existsSync(path.join(planDir, 'plan.v3.json')), false);
    const tasks = (await readdir(path.join(sessionRoot, 'tasks'))).filter(name => name.startsWith('t-'));
    assert.equal(tasks.length, 3);
    for (const taskId of [initial.task.task_id, submitted.task.task_id]) {
      const task = JSON.parse(await readFile(path.join(sessionRoot, 'tasks', taskId, 'meta.json'), 'utf8'));
      assert.equal(task.segments.length, 1);
    }
    assert.equal(await countCalls(), 3);
    assert.equal([...restored, ...competing].some(event => event.type === 'error' || event.type === 'approval_request'), false);
  } finally {
    await other?.shutdown(); await reopened?.shutdown(); await client.shutdown(); cleanup(); await rm(workspace, {recursive: true, force: true});
    for (const [key, value] of [['VIBE_TASK_RUNTIME', previous.runtime], ['VIBE_FAKE_DEMO', previous.demo], ['VIBE_FAKE_DELAY_SECONDS', previous.delay]] as const) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
});
