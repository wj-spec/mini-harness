import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {mkdir, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as pause} from 'node:timers/promises';
import {test} from 'node:test';
import React from 'react';
import stringWidth from 'string-width';
import {cleanup, render} from 'ink-testing-library';
import {App} from '../src/app.js';
import {createBackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';
import {readSidebarSignal, writeSidebarSignal, type SidebarSignalInput} from '../src/sidebar/signal.js';
import {SidebarApp} from '../src/sidebar/app.js';
import {requestPlanPage} from '../src/sidebar/requests.js';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

test('reopened workbench pages through actual saved runs and opens the original command evidence without model calls', async () => {
  const previous = {runtime: process.env.VIBE_TASK_RUNTIME, demo: process.env.VIBE_FAKE_DEMO};
  process.env.VIBE_TASK_RUNTIME = '0'; process.env.VIBE_FAKE_DEMO = '1';
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-inspection-history-'));
  const first = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace, '--autonomy', 'ask']});
  const events: ServerEvent[] = [];
  first.subscribe(event => {events.push(event); if (event.type === 'approval_request') first.approve(event.approval_id, 'once');});
  let restored: ReturnType<typeof createBackendClient> | undefined;
  let view: ReturnType<typeof render> | undefined;
  try {
    first.start(); await waitFor(() => events.some(event => event.type === 'status' && event.phase === 'idle'));
    const ready = events.find(event => event.type === 'ready'); assert.ok(ready?.type === 'ready' && ready.session_path);
    const original = first.run('创建演示文件并验证');
    await waitFor(() => events.some(event => event.type === 'done' && event.id === original));
    for (let i = 0; i < 4; i++) {
      const id = first.run(`后续任务 ${i}`); await waitFor(() => events.some(event => event.type === 'done' && event.id === id));
    }
    await first.shutdown();
    const tracePath = path.join(ready.session_path, 'trace.jsonl');
    const calls = async () => (await readFile(tracePath, 'utf8')).split('\n').filter(line => line && JSON.parse(line).type === 'llm_start').length;
    const before = await calls(); process.env.VIBE_FAKE_DEMO = '';
    restored = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace, '--session', ready.session_id]});
    const resumedEvents: ServerEvent[] = []; restored.subscribe(event => resumedEvents.push(event));
    const signals: SidebarSignalInput[] = [];
    view = render(<App client={restored} windowSize={{columns: 100, rows: 40}}
      launchSidebar={() => ({outcome: 'started', message: 'test host'})} publishSidebarSignal={(_workspace, value) => signals.push(value)}/>);
    restored.start(); await waitFor(() => view!.lastFrame()!.includes('会话已恢复，尚未运行'));
    const key = async (text: string) => {view!.stdin.write(text); await pause(35);};
    const click = async (label: string, last = false) => {
      await pause(30); const rows = view!.lastFrame()!.split('\n');
      const hits = rows.map((line, index) => ({line, index})).filter(row => row.line.includes(label));
      const row = last ? hits.at(-1) : hits[0]; assert.ok(row, view!.lastFrame());
      const col = stringWidth(row.line.slice(0, row.line.indexOf(label))) + 2;
      await key(`\x1b[<0;${col};${row.index + 1}M\x1b[<0;${col};${row.index + 1}m`);
    };
    await key('恢复后的中文草稿'); await key('\x1b[D'); await click('历史结果');
    await waitFor(() => view!.lastFrame()!.includes('[ 更早 ]')); await click('更早');
    await waitFor(() => view!.lastFrame()!.includes('创建演示文件并验证'));
    await click('[ 验证 ]', true);
    await waitFor(() => signals.at(-1)?.navigation?.mode === 'result');
    assert.equal(signals.at(-1)?.run?.requestId, original);
    assert.equal(signals.at(-1)?.verification?.commands[0]?.execution?.exit_code, 0);
    assert.equal(signals.at(-1)?.verification?.verified, true);
    await key('中'); assert.match(view.lastFrame()!, /恢复后的中文草中稿/);
    assert.equal(await calls(), before);
    assert.equal(resumedEvents.some(event => event.type === 'done' || event.type === 'approval_request'), false);
    assert.equal(await readFile(path.join(workspace, '.vibe/fake-demo.txt'), 'utf8'), 'fake demo\n');
  } finally {
    await first.shutdown(); await restored?.shutdown(); view?.unmount(); cleanup();
    await rm(workspace, {recursive: true, force: true});
    if (previous.runtime === undefined) delete process.env.VIBE_TASK_RUNTIME; else process.env.VIBE_TASK_RUNTIME = previous.runtime;
    if (previous.demo === undefined) delete process.env.VIBE_FAKE_DEMO; else process.env.VIBE_FAKE_DEMO = previous.demo;
  }
});

test('sidebar transport reads every page of an actual Unicode plan without running the model', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python)) {t.skip('python backend unavailable'); return;}
  const previous = {runtime: process.env.VIBE_TASK_RUNTIME, demo: process.env.VIBE_FAKE_DEMO};
  process.env.VIBE_TASK_RUNTIME = '1'; process.env.VIBE_FAKE_DEMO = '';
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-sidebar-pages-'));
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace, '--autonomy', 'ask']});
  const events: ServerEvent[] = []; client.subscribe(event => events.push(event));
  let published: SidebarSignalInput | undefined;
  const view = render(<App client={client} publishSidebarSignal={(root, signal) => {published = signal; writeSidebarSignal(root, signal);}}/>);
  try {
    client.start(); await waitFor(() => events.some(event => event.type === 'status' && event.phase === 'idle'));
    const ready = events.find(event => event.type === 'ready'); assert.ok(ready?.type === 'ready' && ready.session_path);
    execFileSync(python, ['-c', `
import sys
from app.runtime.tasks import TaskStore
from app.runtime.plan import Plan, propose_plan
store = TaskStore(sys.argv[1])
task = store.create(kind='run', mode='plan', objective='长计划分页', workspace=sys.argv[2])
plan = Plan.from_dict({'task_id': task.task_id, 'plan_id': 'pl-pages', 'objective': task.objective,
    'steps': [{'step_id': 's1', 'title': '只读检查', 'kind': 'read'}],
    'constraints': [f'边界-{i:04d}: 中文🧭' + '细节' * 20 for i in range(900)]})
propose_plan(store.task_dir(task.task_id), plan)
`, ready.session_path, workspace], {cwd: repoRoot});
    view.stdin.write('/plan'); await pause(30); view.stdin.write('\r');
    await waitFor(() => Boolean(published?.plan?.target));
    const target = published!.plan!.target!; const owner = published!.owner!;
    let cursor = 0; const pages: string[] = [];
    for (let count = 0; count < 10; count++) {
      const page = await requestPlanPage(ready.workspace, owner, ready.session_id, target, cursor, new AbortController().signal);
      assert.equal(page.outcome, 'page', page.message); assert.deepEqual(page.target, target);
      pages.push(page.body);
      if (page.nextCursor === null) break;
      assert.ok(page.nextCursor > cursor); cursor = page.nextCursor;
    }
    assert.ok(pages.length >= 3);
    const body = pages.join('');
    assert.match(body, /^# 长计划分页/); assert.match(body, /## 审阅反馈\n- （暂无）\n$/);
    for (let i = 0; i < 900; i++) assert.equal(body.split(`边界-${String(i).padStart(4, '0')}:`).length - 1, 1);
    assert.equal(events.some(event => ['approval_request', 'tool_call', 'done'].includes(event.type)), false);
    assert.equal(events.some(event => event.type === 'context_snapshot' && event.snapshot.phase === 'request'), false);
  } finally {
    await client.shutdown(); view.unmount(); cleanup(); await rm(workspace, {recursive: true, force: true});
    if (previous.runtime === undefined) delete process.env.VIBE_TASK_RUNTIME; else process.env.VIBE_TASK_RUNTIME = previous.runtime;
    if (previous.demo === undefined) delete process.env.VIBE_FAKE_DEMO; else process.env.VIBE_FAKE_DEMO = previous.demo;
  }
});

async function waitFor(condition: () => boolean, timeoutMs = 15000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error('timed out waiting for condition');
    await pause(25);
  }
}

test('actual mode controls preserve one-off execution and restore only the next default', async t => {
  if (!existsSync(path.join(repoRoot, '.venv', 'bin', 'python3'))) {t.skip('python backend unavailable'); return;}
  const previous = Object.fromEntries(['VIBE_TASK_RUNTIME', 'VIBE_FAKE_DEMO', 'VIBE_FAKE_READ', 'VIBE_FAKE_DELAY_SECONDS'].map(name => [name, process.env[name]]));
  Object.assign(process.env, {VIBE_TASK_RUNTIME: '1', VIBE_FAKE_DEMO: '', VIBE_FAKE_READ: '', VIBE_FAKE_DELAY_SECONDS: '2'});
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-mode-control-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client} windowSize={{columns: 80, rows: 24}}/>);
  const key = async (value: string) => {view.stdin.write(value); await pause(35);};
  const enter = async (value: string) => {await key(value); await key('\r');};
  let reopened: ReturnType<typeof createBackendClient> | undefined;
  try {
    client.start(); await waitFor(() => events.some(e => e.type === 'status' && e.phase === 'idle'));
    const ready = events.find(e => e.type === 'ready');
    assert.ok(ready?.type === 'ready' && ready.session_path);
    assert.ok(ready.capabilities.includes('plan-mode-control'));
    await enter('/plan on');
    await waitFor(() => view.lastFrame()?.includes('默认模式已确认') ?? false);
    await enter('/act 修复标题');
    await waitFor(() => /本次：执行.*下次：只读规划/.test(view.lastFrame() ?? ''));
    const run = events.find(e => e.type === 'task_state');
    assert.ok(run?.type === 'task_state' && run.task.mode === 'act');
    await enter('/plan off');
    await waitFor(() => events.some(e => e.type === 'plan_mode' && e.mode.pending && e.mode.wanted === false));
    assert.match(view.lastFrame() ?? '', /本次：执行.*下次：执行.*待生效/);
    await key('中文草稿');
    await waitFor(() => events.some(e => e.type === 'done' && e.id === run.id));
    assert.match(view.lastFrame() ?? '', /中文草稿/);
    assert.equal(JSON.parse(await readFile(path.join(ready.session_path, 'plan-mode.json'), 'utf8')).active, false);
    const task = JSON.parse(await readFile(path.join(ready.session_path, 'tasks', run.task.task_id, 'meta.json'), 'utf8'));
    assert.equal(task.mode, 'act'); assert.equal(task.segments.length, 1);
    await key('\u0015'); await enter('/plan on');
    await waitFor(() => {const mode = events.findLast(e => e.type === 'plan_mode'); return mode?.type === 'plan_mode' && mode.mode.active && !mode.mode.pending;});
    assert.equal(JSON.parse(await readFile(path.join(ready.session_path, 'plan-mode.json'), 'utf8')).active, true);
    await client.shutdown(); view.unmount();
    const restored: ServerEvent[] = [];
    reopened = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace, '--session', ready.session_id]});
    reopened.subscribe(event => restored.push(event));
    const nextView = render(<App client={reopened} windowSize={{columns: 80, rows: 24}}/>);
    reopened.start(); await waitFor(() => restored.some(e => e.type === 'status' && e.phase === 'idle'));
    assert.match(nextView.lastFrame() ?? '', /会话已恢复，尚未运行/);
    assert.match(nextView.lastFrame() ?? '', /下次：只读规划/);
    assert.equal(restored.some(e => e.type === 'task_state' || e.type === 'done'), false);
    nextView.stdin.write('/chat 仅解释'); await pause(35); nextView.stdin.write('\r');
    await waitFor(() => /本次：仅对话.*下次：只读规划/.test(nextView.lastFrame() ?? ''));
    await waitFor(() => restored.some(e => e.type === 'done'));
    const chat = restored.find(e => e.type === 'task_state');
    assert.ok(chat?.type === 'task_state' && chat.task.mode === 'chat');
    await reopened.shutdown();
    const trace = (await readFile(path.join(ready.session_path, 'trace.jsonl'), 'utf8')).split('\n').filter(Boolean).map(line => JSON.parse(line));
    assert.equal(trace.filter(item => item.type === 'llm_start').length, 2);
    assert.equal([...events, ...restored].some(e => e.type === 'error'), false);
  } finally {
    await reopened?.shutdown(); await client.shutdown(); cleanup(); await rm(workspace, {recursive: true, force: true});
    for (const [name, value] of Object.entries(previous)) {if (value === undefined) delete process.env[name]; else process.env[name] = value;}
  }
});

for (const cancel of [false, true]) test(`Ink feedback confirmation ${cancel ? 'cancels then explicitly resumes' : 'runs'} the Python revision and returns the exact new review card`, async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python)) {t.skip('python backend unavailable'); return;}
  const previous = {runtime: process.env.VIBE_TASK_RUNTIME, demo: process.env.VIBE_FAKE_DEMO, delay: process.env.VIBE_FAKE_DELAY_SECONDS};
  process.env.VIBE_TASK_RUNTIME = '1'; process.env.VIBE_FAKE_DEMO = 'plan';
  process.env.VIBE_FAKE_DELAY_SECONDS = cancel ? '0.8' : '0';
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-revision-card-'));
  const events: ServerEvent[] = [];
  const signals: SidebarSignalInput[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  try {
    client.start(); await waitFor(() => events.some(event => event.type === 'ready'));
    const ready = events.find(event => event.type === 'ready');
    assert.ok(ready?.type === 'ready' && ready.session_path);
    execFileSync(python, ['-c', `
import sys
from app.runtime.tasks import TaskStore
from app.runtime.plan import Plan,propose_plan
store=TaskStore(sys.argv[1])
task=store.create(kind='run',mode='plan',objective='Original card',workspace=sys.argv[2])
propose_plan(store.task_dir(task.task_id),Plan.from_dict({'task_id':task.task_id,'plan_id':'plan-card-it',
    'objective':task.objective,'steps':[{'step_id':'s1','title':'Read','kind':'read'}]}))
`, ready.session_path, workspace], {cwd: repoRoot});
    const view = render(<App client={client} windowSize={{columns: 80, rows: 24}} publishSidebarSignal={(workspace, value) => {signals.push(value); writeSidebarSignal(workspace, value);}}/>);
    const key = async (text: string) => {view.stdin.write(text); await pause(35);};
    await pause(40); await key('\u0015'); await key('/plan'); await key('\r');
    await waitFor(() => view.lastFrame()?.includes('计划 v1') ?? false);
    const source = events.find(event => event.type === 'plan_review');
    assert.ok(source?.type === 'plan_review' && source.target);
    const sourcePath = path.join(ready.session_path, 'tasks', source.target.plan_task_id, 'plan', 'plan.v1.json');
    const original = await readFile(sourcePath, 'utf8');
    await key('普通中文草稿'); await key('\u0012'); await key('r'); await key('\r');
    await key('补充验证命令'); await key('\r'); await key('\r');
    if (cancel) {
      await waitFor(() => events.some(event => event.type === 'plan_revision' && event.task?.run_started));
      await pause(80); await key('\u0003');
      await waitFor(() => events.some(event => event.type === 'done' && event.result.status === 'cancelled'));
      assert.equal(events.some(event => event.type === 'plan_revision' && event.outcome === 'submitted'), false);
      await key('\u0012'); await key('r'); await key('\r');
      assert.match(view.lastFrame()!, /补充验证命令/); assert.match(view.lastFrame()!, /续做/);
      await key('\t'); await key('\u001b[C'); await key('\r');
    }
    try {await waitFor(() => view.lastFrame()?.includes('计划 v2') ?? false);}
    catch (error) {
      t.diagnostic(JSON.stringify(events.filter(event => ['plan_review', 'plan_revision', 'error', 'done'].includes(event.type))));
      t.diagnostic(view.lastFrame() ?? 'no frame'); throw error;
    }
    assert.match(view.lastFrame()!, /普通中文草稿/);
    const receipt = events.find(event => event.type === 'plan_revision' && event.outcome === 'submitted');
    assert.ok(receipt?.type === 'plan_revision' && receipt.result_target);
    assert.equal(receipt.result_target.plan_id, source.target.plan_id);
    assert.equal(receipt.feedback, '补充验证命令');
    await waitFor(() => signals.at(-1)?.plan?.relation === 'revision-result' && signals.at(-1)?.plan?.objective !== '修订产物 · 等待读取');
    assert.deepEqual(signals.at(-1)?.plan?.target, receipt.result_target);
    assert.equal(signals.at(-1)?.run?.requestId, receipt.task?.run_id);
    assert.equal(signals.at(-1)?.run?.taskId, receipt.task?.task_id);
    assert.equal(signals.at(-1)?.run?.segment, cancel ? 2 : 1);
    assert.equal(signals.at(-1)?.plan?.acceptance, null);
    assert.equal(readSidebarSignal(ready.workspace, signals.at(-1)!.owner)?.plan?.relation, 'revision-result');
    assert.equal(await readFile(sourcePath, 'utf8'), original);
    const revised = JSON.parse(await readFile(sourcePath.replace('plan.v1', 'plan.v2'), 'utf8'));
    assert.equal(revised.status, 'proposed'); assert.deepEqual(revised.approval, {});
    const starts = events.filter(event => event.type === 'plan_revision' && event.task?.run_started);
    assert.equal(starts.length, cancel ? 2 : 1);
    if (cancel) {
      assert.ok(starts[0]?.type === 'plan_revision' && starts[1]?.type === 'plan_revision');
      assert.equal(starts[0].task?.task_id, starts[1].task?.task_id);
      assert.equal(starts[1].task?.segment, 2);
    }
    await client.shutdown();
    const trace = await readFile(path.join(ready.session_path, 'trace.jsonl'), 'utf8');
    assert.equal(trace.split('\n').filter(line => line && JSON.parse(line).type === 'llm_start').length, cancel ? 2 : 1);
  } finally {
    await client.shutdown(); cleanup(); await rm(workspace, {recursive: true, force: true});
    if (previous.runtime === undefined) delete process.env.VIBE_TASK_RUNTIME; else process.env.VIBE_TASK_RUNTIME = previous.runtime;
    if (previous.demo === undefined) delete process.env.VIBE_FAKE_DEMO; else process.env.VIBE_FAKE_DEMO = previous.demo;
    if (previous.delay === undefined) delete process.env.VIBE_FAKE_DELAY_SECONDS; else process.env.VIBE_FAKE_DELAY_SECONDS = previous.delay;
  }
});

for (const allow of [false, true]) test(`public revision demo ${allow ? 'executes the reviewed new version' : 'preserves the file after denial'}`, async () => {
  const previous = {runtime: process.env.VIBE_TASK_RUNTIME, demo: process.env.VIBE_FAKE_DEMO};
  process.env.VIBE_TASK_RUNTIME = '1'; process.env.VIBE_FAKE_DEMO = 'revision';
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-revision-demo-'));
  await mkdir(path.join(workspace, '.vibe'));
  const file = path.join(workspace, '.vibe', 'plan-demo.txt');
  await writeFile(file, 'original');
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace, '--autonomy', 'ask']});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client} windowSize={{columns: 80, rows: 24}}/>);
  const key = async (text: string) => {view.stdin.write(text); await pause(35);};
  try {
    client.start(); await waitFor(() => events.some(event => event.type === 'status' && event.phase === 'idle'));
    assert.match(view.lastFrame()!, /\/plan.*plan-demo.txt/);
    await key('\r'); await waitFor(() => view.lastFrame()?.includes('计划 v1') ?? false);
    const source = events.find(event => event.type === 'plan_review');
    const ready = events.find(event => event.type === 'ready');
    assert.ok(source?.type === 'plan_review' && source.target && ready?.type === 'ready' && ready.session_path);
    const sourcePath = path.join(ready.session_path, 'tasks', source.target.plan_task_id, 'plan', 'plan.v1.json');
    const original = await readFile(sourcePath, 'utf8');
    assert.equal(events.some(event => event.type === 'approval_request'), false);
    await key('普通中文草稿'); await key('\u0012'); await key('r'); await key('\r');
    await key('将演示内容改为 revised demo'); await key('\r'); await key('\r');
    await waitFor(() => view.lastFrame()?.includes('计划 v2') ?? false);
    assert.match(view.lastFrame()!, /普通中文草稿/);
    assert.equal(await readFile(sourcePath, 'utf8'), original);
    const revised = JSON.parse(await readFile(sourcePath.replace('plan.v1', 'plan.v2'), 'utf8'));
    assert.equal(revised.plan_id, source.target.plan_id);
    assert.equal(revised.status, 'proposed'); assert.deepEqual(revised.approval, {});
    assert.match(revised.acceptance_criteria[0].text, /revised demo/);
    assert.equal(await readFile(file, 'utf8'), 'original');
    assert.equal(events.some(event => event.type === 'approval_request'), false);
    await key('\u0012'); await key('e'); await key('\r');
    await waitFor(() => events.some(event => event.type === 'approval_request'));
    await key('\t'); if (!allow) await key('\u001b[C'); await key('\r');
    if (allow) {
      await waitFor(() => events.filter(event => event.type === 'approval_request').length === 2);
      await key('\t'); await key('\r');
    }
    const launch = events.find(event => event.type === 'plan_review' && event.outcome === 'launched');
    assert.ok(launch?.type === 'plan_review' && launch.target);
    assert.equal(launch.target.plan_version, 2);
    await waitFor(() => events.some(event => event.type === 'done' && event.id === launch.id));
    const done = events.find(event => event.type === 'done' && event.id === launch.id);
    assert.ok(done?.type === 'done');
    assert.equal(done.result.verified, allow);
    assert.equal(await readFile(file, 'utf8'), allow ? 'revised demo\n' : 'original');
    assert.match(view.lastFrame()!, /普通中文草稿/);
  } finally {
    await client.shutdown(); cleanup(); await rm(workspace, {recursive: true, force: true});
    if (previous.runtime === undefined) delete process.env.VIBE_TASK_RUNTIME; else process.env.VIBE_TASK_RUNTIME = previous.runtime;
    if (previous.demo === undefined) delete process.env.VIBE_FAKE_DEMO; else process.env.VIBE_FAKE_DEMO = previous.demo;
  }
});

for (const conflict of [false, true]) test(`Ink reopens a cancelled revision and ${conflict ? 'rejects a changed source without another model call' : 'explicitly resumes its recovered feedback'}`, async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python)) {t.skip('python backend unavailable'); return;}
  const previous = {runtime: process.env.VIBE_TASK_RUNTIME, demo: process.env.VIBE_FAKE_DEMO, delay: process.env.VIBE_FAKE_DELAY_SECONDS};
  process.env.VIBE_TASK_RUNTIME = '1'; process.env.VIBE_FAKE_DEMO = 'plan'; process.env.VIBE_FAKE_DELAY_SECONDS = '1';
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-reopen-feedback-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  let reopened: ReturnType<typeof createBackendClient> | undefined;
  try {
    client.start(); await waitFor(() => events.some(event => event.type === 'ready'));
    const ready = events.find(event => event.type === 'ready');
    assert.ok(ready?.type === 'ready' && ready.session_path);
    execFileSync(python, ['-c', `
import sys
from app.runtime.tasks import TaskStore
from app.runtime.plan import Plan,propose_plan
store=TaskStore(sys.argv[1])
task=store.create(kind='run',mode='plan',objective='重开后修订',workspace=sys.argv[2])
propose_plan(store.task_dir(task.task_id),Plan.from_dict({'task_id':task.task_id,'plan_id':'plan-reopen-feedback',
    'objective':task.objective,'steps':[{'step_id':'s1','title':'Read','kind':'read'}]}))
`, ready.session_path, workspace], {cwd: repoRoot});
    const view = render(<App client={client} windowSize={{columns: 80, rows: 24}}/>);
    const key = async (text: string) => {view.stdin.write(text); await pause(35);};
    await pause(40); await key('\u0015'); await key('/plan'); await key('\r');
    await waitFor(() => view.lastFrame()?.includes('计划 v1') ?? false);
    await key('\u0012'); await key('r'); await key('\r'); await key('补充验证命令'); await key('\r'); await key('\r');
    await waitFor(() => events.some(event => event.type === 'plan_revision' && event.task?.run_started));
    await pause(80); await key('\u0003');
    await waitFor(() => events.some(event => event.type === 'done' && event.result.status === 'cancelled'));
    const initial = events.find(event => event.type === 'plan_revision' && event.task?.run_started);
    assert.ok(initial?.type === 'plan_revision' && initial.task && initial.target);
    await client.shutdown(); view.unmount();
    const tracePath = path.join(ready.session_path, 'trace.jsonl');
    const countCalls = async () => (await readFile(tracePath, 'utf8')).split('\n').filter(line => line && JSON.parse(line).type === 'llm_start').length;
    const calls = await countCalls(); assert.equal(calls, 1);
    const restored: ServerEvent[] = [];
    process.env.VIBE_FAKE_DEMO = 'revision-resume';
    reopened = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace, '--session', ready.session_id]});
    reopened.subscribe(event => restored.push(event));
    const next = render(<App client={reopened} windowSize={{columns: 80, rows: 24}}/>);
    const nextKey = async (text: string) => {next.stdin.write(text); await pause(35);};
    reopened.start(); await waitFor(() => next.lastFrame()?.includes('计划 v1') ?? false);
    await nextKey('\u0015'); await nextKey('重开后的普通草稿'); await nextKey('\u0012'); await nextKey('h'); await nextKey('\r');
    await waitFor(() => restored.some(event => event.type === 'plan_revision_history'));
    assert.match(next.lastFrame()!, /补充验证命令/); assert.equal(await countCalls(), calls);
    await nextKey('\t'); await nextKey('\r');
    assert.match(next.lastFrame()!, /续做/); assert.equal(await countCalls(), calls);
    const sourcePath = path.join(ready.session_path, 'tasks', initial.target.plan_task_id, 'plan', 'plan.v1.json');
    if (conflict) {
      const source = JSON.parse(await readFile(sourcePath, 'utf8'));
      source.objective = '外部更新后的计划';
      await writeFile(sourcePath, JSON.stringify(source));
    }
    await nextKey('\t'); await nextKey('\u001b[C'); await nextKey('\r');
    if (conflict) {
      await waitFor(() => restored.some(event => event.type === 'plan_revision' && event.code === 'content_changed'));
      assert.equal(await countCalls(), calls);
      assert.equal(restored.some(event => event.type === 'plan_revision' && event.task?.run_started), false);
      const task = JSON.parse(await readFile(path.join(ready.session_path, 'tasks', initial.task.task_id, 'meta.json'), 'utf8'));
      assert.equal(task.segments.length, 1);
      assert.equal(existsSync(sourcePath.replace('plan.v1', 'plan.v2')), false);
      assert.match(next.lastFrame()!, /补充验证命令/); assert.match(next.lastFrame()!, /重开后的普通草稿/);
      assert.equal(restored.some(event => event.type === 'error'), false);
      return;
    }
    await waitFor(() => next.lastFrame()?.includes('计划 v2') ?? false);
    const resumed = restored.find(event => event.type === 'plan_revision' && event.task?.run_started);
    assert.ok(resumed?.type === 'plan_revision' && resumed.task);
    assert.equal(resumed.task.task_id, initial.task.task_id); assert.equal(resumed.task.segment, 2);
    assert.deepEqual(resumed.target, initial.target); assert.equal(resumed.feedback, '补充验证命令');
    assert.match(next.lastFrame()!, /重开后的普通草稿/); assert.equal(await countCalls(), calls + 1);
    const revised = JSON.parse(await readFile(path.join(ready.session_path, 'tasks', initial.target.plan_task_id, 'plan', 'plan.v2.json'), 'utf8'));
    assert.match(revised.acceptance_criteria[0].text, /revised demo/);
    assert.equal(restored.some(event => event.type === 'error'), false);
  } finally {
    await reopened?.shutdown(); await client.shutdown(); cleanup(); await rm(workspace, {recursive: true, force: true});
    for (const [key, value] of [['VIBE_TASK_RUNTIME', previous.runtime], ['VIBE_FAKE_DEMO', previous.demo], ['VIBE_FAKE_DELAY_SECONDS', previous.delay]] as const) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
});

test('revision transport publishes a new version and reopening only queries it', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python)) {t.skip('python backend unavailable'); return;}
  const previous = {runtime: process.env.VIBE_TASK_RUNTIME, demo: process.env.VIBE_FAKE_DEMO};
  process.env.VIBE_TASK_RUNTIME = '1';
  process.env.VIBE_FAKE_DEMO = 'plan';
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-revision-it-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  let reopened: ReturnType<typeof createBackendClient> | undefined;
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const ready = events.find(event => event.type === 'ready');
    assert.ok(ready?.type === 'ready' && ready.session_path && ready.capabilities.includes('plan-revision'));
    execFileSync(python, ['-c', `
import sys
from app.runtime.tasks import TaskStore
from app.runtime.plan import Plan,propose_plan
store=TaskStore(sys.argv[1])
task=store.create(kind='run',mode='plan',objective='Original plan',workspace=sys.argv[2])
propose_plan(store.task_dir(task.task_id),Plan.from_dict({'task_id':task.task_id,'plan_id':'plan-revision-it',
    'objective':task.objective,'steps':[{'step_id':'s1','title':'Read','kind':'read'}]}))
`, ready.session_path, workspace], {cwd: repoRoot});
    const q = client.command('plan', {action: 'review'});
    await waitFor(() => events.some(e => e.type === 'plan_review' && e.id === q));
    const source = events.find(e => e.type === 'plan_review' && e.id === q);
    assert.ok(source?.type === 'plan_review' && source.target);
    const id = client.command('plan', {action: 'request-changes', target: source.target, feedback: 'Add verification'});
    await waitFor(() => events.some(e => e.type === 'done' && e.id === id));
    const submitted = events.find(e => e.type === 'plan_revision' && e.id === id && e.outcome === 'submitted');
    assert.ok(submitted?.type === 'plan_revision' && submitted.result_target && submitted.task);
    assert.equal(submitted.result_target.plan_id, source.target.plan_id);
    assert.equal(submitted.result_target.plan_version, 2);
    assert.equal(submitted.task.segment, 1);
    await client.shutdown();
    const tracePath = path.join(ready.session_path, 'trace.jsonl');
    const modelCount = (text: string) => text.split('\n').filter(line => line && JSON.parse(line).type === 'llm_start').length;
    const calls = modelCount(await readFile(tracePath, 'utf8'));
    assert.equal(calls, 1);
    const restored: ServerEvent[] = [];
    reopened = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace, '--session', ready.session_id]});
    reopened.subscribe(event => restored.push(event));
    reopened.start();
    await waitFor(() => restored.some(e => e.type === 'ready'));
    const query = reopened.command('plan', {action: 'query-revision', target: source.target, feedback: 'Add verification'});
    await waitFor(() => restored.some(e => e.type === 'plan_revision' && e.id === query));
    const receipt = restored.find(e => e.type === 'plan_revision' && e.id === query);
    assert.ok(receipt?.type === 'plan_revision' && receipt.task);
    assert.equal(receipt.outcome, 'submitted');
    assert.equal(receipt.task.task_id, submitted.task.task_id);
    assert.equal(receipt.task.run_started, false);
    assert.equal(modelCount(await readFile(tracePath, 'utf8')), calls);
    assert.equal([...events, ...restored].some(e => e.type === 'error'), false);
  } finally {
    await reopened?.shutdown(); await client.shutdown();
    for (const [key, value] of [['VIBE_TASK_RUNTIME', previous.runtime], ['VIBE_FAKE_DEMO', previous.demo]] as const) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
    await rm(workspace, {recursive: true, force: true});
  }
});

for (const allow of [false, true]) test(`public plan demo ${allow ? 'verifies after approval' : 'preserves the file after denial'}`, async () => {
  const previous = {demo: process.env.VIBE_FAKE_DEMO, runtime: process.env.VIBE_TASK_RUNTIME};
  process.env.VIBE_FAKE_DEMO = 'plan';
  process.env.VIBE_TASK_RUNTIME = '1';
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-plan-demo-'));
  await mkdir(path.join(workspace, '.vibe'));
  const file = path.join(workspace, '.vibe', 'plan-demo.txt');
  await writeFile(file, 'original');
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace, '--autonomy', 'ask']});
  client.subscribe(event => events.push(event));
  let published: SidebarSignalInput | undefined;
  const view = render(<App client={client} windowSize={{columns: 80, rows: 24}}
    publishSidebarSignal={(root, signal) => {published = signal; writeSidebarSignal(root, signal);}}/>);
  const key = async (value: string) => {view.stdin.write(value); await pause(50);};
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'status' && event.phase === 'idle'));
    assert.match(String(view.lastFrame()), /\/plan.*plan-demo.txt/);
    assert.ok(!events.some(event => event.type === 'done' || event.type === 'approval_request'));
    await key('\r');
    await waitFor(() => events.some(event => event.type === 'plan_review' && event.outcome === 'ready'));
    await key('演示中文草稿ad'); await key('\u0012'); await key('e'); await key('\r');
    await waitFor(() => events.some(event => event.type === 'approval_request'));
    assert.equal(await readFile(file, 'utf8'), 'original');
    const launch = events.find(event => event.type === 'plan_review' && event.outcome === 'launched');
    assert.ok(launch?.type === 'plan_review' && launch.outcome === 'launched');
    await key('\u0012'); await key(allow ? 'y' : 'n'); await key('\r');
    if (allow) {
      await waitFor(() => events.filter(event => event.type === 'approval_request').length === 2);
      await key('\u0012'); await key('y'); await key('\r');
    }
    await waitFor(() => events.some(event => event.type === 'done' && event.id === launch.id));
    const done = events.find(event => event.type === 'done' && event.id === launch.id);
    assert.ok(done?.type === 'done');
    assert.equal(done.result.status, allow ? 'done' : 'stopped');
    if (allow) {
      assert.equal(done.result.verified, true);
      assert.match(String(view.lastFrame()), /计划验收.*已验证/);
      assert.match(String(view.lastFrame()), /exit=0/);
    } else {
      assert.equal(done.result.stop_reason, 'plan_incomplete');
      assert.doesNotMatch(String(view.lastFrame()), /status=done|完成判定：通过|verified=True/);
    }
    assert.equal(await readFile(file, 'utf8'), allow ? 'plan demo\n' : 'original');
    assert.equal(events.filter(event => event.type === 'approval_request').length, allow ? 2 : 1);
    assert.match(String(view.lastFrame()), /演示中文草稿ad/);
    await waitFor(() => published?.run?.status === 'finished');
    assert.ok(published?.owner);
    const connected = events.find(event => event.type === 'ready');
    assert.ok(connected?.type === 'ready');
    const sidebar = readSidebarSignal(connected.workspace, published.owner);
    assert.ok(sidebar, `invalid sidebar envelope: ${JSON.stringify(published)}`);
    assert.deepEqual(sidebar?.plan?.target, launch.target);
    assert.equal(sidebar?.plan?.relation, 'execution');
    assert.equal(sidebar?.plan?.acceptance, done.result.verified ?? null);
    assert.equal(sidebar?.plan?.doneSteps, allow ? 1 : 0, 'execution ledger supplies the completed step count');
    assert.equal(sidebar?.run?.taskId, launch.launch.task_id);
    assert.equal(sidebar?.run?.requestId, launch.id);
    assert.equal(sidebar?.run?.traceRunId, done.result.run_id);
    assert.notEqual(sidebar?.run?.requestId, sidebar?.run?.traceRunId);
    assert.deepEqual(sidebar?.files, allow ? ['.vibe/plan-demo.txt'] : []);
    assert.equal(sidebar.verification?.requestId, launch.id);
    assert.equal(sidebar.verification?.verified, done.result.verified ?? null);
    if (allow) {
      assert.equal(sidebar.verification?.commands.length, 1);
      assert.equal(sidebar.verification?.commands[0]?.verification, true);
      assert.deepEqual(sidebar.verification?.commands[0]?.execution, {status: 'succeeded', exit_code: 0});
    } else {
      assert.deepEqual(sidebar.verification?.commands, []);
      assert.equal(sidebar.verification?.stopReason, 'plan_incomplete');
    }
    const callsBeforeRead = events.filter(event => event.type === 'context_snapshot' && event.snapshot.phase === 'request').length;
    const sidebarView = render(<SidebarApp workspace={connected.workspace} owner={published.owner} refreshMs={0} signalPollMs={10}
      size={{columns: 60, rows: 18}} readStatus={() => ({ok: true, status: {root: connected.workspace, branch: 'main', head: null, ahead: 0, behind: 0, files: []}})}/>);
    try {
      await waitFor(() => String(sidebarView.lastFrame()).includes('计划 v1'));
      sidebarView.stdin.write('p');
      await waitFor(() => String(sidebarView.lastFrame()).includes('目标与成功标准'));
      assert.match(String(sidebarView.lastFrame()), new RegExp(launch.target.plan_hash.slice(0, 16)));
      sidebarView.stdin.write('G'); await pause(50);
      assert.match(String(sidebarView.lastFrame()), /仅写入/);
      assert.equal(events.filter(event => event.type === 'context_snapshot' && event.snapshot.phase === 'request').length, callsBeforeRead);
      assert.equal(events.filter(event => event.type === 'approval_request').length, allow ? 2 : 1);
      assert.match(String(view.lastFrame()), /演示中文草稿ad/);
    } finally {sidebarView.unmount();}
  } finally {
    await client.shutdown(); cleanup(); await rm(workspace, {recursive: true, force: true});
    if (previous.demo === undefined) delete process.env.VIBE_FAKE_DEMO; else process.env.VIBE_FAKE_DEMO = previous.demo;
    if (previous.runtime === undefined) delete process.env.VIBE_TASK_RUNTIME; else process.env.VIBE_TASK_RUNTIME = previous.runtime;
  }
});

test('Ink submits a plan, confirms its exact card and denies a real tool write', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python)) {t.skip('python backend unavailable'); return;}
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-plan-journey-'));
  const fixture = path.join(workspace, 'backend.py');
  await writeFile(path.join(workspace, 'guarded.txt'), 'original');
  await writeFile(fixture, `
import os, sys
sys.path.insert(0, ${JSON.stringify(repoRoot)})
os.environ['VIBE_TASK_RUNTIME'] = '1'
os.environ.pop('VIBE_FAKE_DEMO', None)
from core.contracts import Response, ToolCall
from app.providers.fake import FakeProvider
import app.__main__ as entry
provider = FakeProvider([
    Response(tool_calls=[ToolCall('submit_plan', {
        'objective': '确认后拒绝写入',
        'steps': [{'step_id':'s1', 'title':'修改文件', 'kind':'edit', 'files':['guarded.txt']}]
    }, id='plan-submit')]),
    Response(tool_calls=[ToolCall('start_plan_step', {'step_id':'s1'}, id='step-start')]),
    Response(tool_calls=[ToolCall('write_file', {'path':'guarded.txt', 'content':'changed'}, id='write-test')]),
    '写入已拒绝，文件保持原样',
])
entry.build_provider = lambda *args, **kwargs: provider
entry.main()
`);
  const quote = (value: string) => `'${value.replaceAll("'", "'\\''")}'`;
  await writeFile(path.join(workspace, 'run_vibe.sh'), `#!/bin/sh\nexec ${quote(python)} ${quote(fixture)} "$@"\n`, {mode: 0o755});
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot: workspace, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client} windowSize={{columns: 80, rows: 24}}/>);
  const key = async (value: string) => {view.stdin.write(value); await pause(50);};
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'status' && event.phase === 'idle'));
    await pause(50);
    await key('/plan 请生成修改文件的计划'); await key('\r');
    await waitFor(() => events.some(event => event.type === 'plan_review' && event.outcome === 'ready'));
    const query = events.find(event => event.type === 'plan_review' && event.outcome === 'ready');
    assert.ok(query?.type === 'plan_review' && query.target);
    await waitFor(() => String(view.lastFrame()).includes('Ctrl+R审阅'));
    await key('中文草稿ad'); await key('\u0012'); await key('e');
    assert.ok(!events.some(event => event.type === 'approval_request'));
    await key('\r');
    await waitFor(() => events.some(event => event.type === 'approval_request'));
    const launch = events.find(event => event.type === 'plan_review' && event.outcome === 'launched');
    assert.ok(launch?.type === 'plan_review' && launch.outcome === 'launched');
    assert.deepEqual(launch.target, query.target);
    await waitFor(() => String(view.lastFrame()).includes('草稿焦点'));
    await key('n');
    assert.ok(!events.some(event => event.type === 'approval_resolved'));
    await key('\u0012'); await key('n'); await key('\r');
    await waitFor(() => events.some(event => event.type === 'done' && event.id === launch.id));
    assert.equal(await readFile(path.join(workspace, 'guarded.txt'), 'utf8'), 'original');
    assert.ok(events.some(event => event.type === 'approval_resolved' && event.decision === 'deny'));
    assert.equal(events.filter(event => event.type === 'plan_review' && event.outcome === 'launched' && event.launch.run_started).length, 1);
    assert.match(String(view.lastFrame()), /中文草稿adn/);
    const ready = events.find(event => event.type === 'ready');
    assert.ok(ready?.type === 'ready' && ready.session_path);
    const planFile = path.join(ready.session_path, 'tasks', query.target.plan_task_id, 'plan', 'plan.v1.json');
    assert.equal(JSON.parse(await readFile(planFile, 'utf8')).status, 'approved');
    const task = JSON.parse(await readFile(path.join(ready.session_path, 'tasks', launch.launch.task_id, 'meta.json'), 'utf8'));
    assert.equal(task.creation.target.plan_hash, query.target.plan_hash);
    assert.equal(task.segments.length, 1);
    assert.equal(task.segments[0].run_id, launch.id);
  } finally {
    await client.shutdown(); cleanup(); await rm(workspace, {recursive: true, force: true});
  }
});

test('Ink plan review binds one real backend run and returns it on replay', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python)) {t.skip('python backend unavailable'); return;}
  const previousRuntime = process.env.VIBE_TASK_RUNTIME;
  process.env.VIBE_TASK_RUNTIME = '1';
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-review-it-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client} windowSize={{columns: 80, rows: 24}}/>);
  let reopened: ReturnType<typeof createBackendClient> | undefined;
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const ready = events.find(event => event.type === 'ready');
    assert.ok(ready?.type === 'ready' && ready.session_path);
    assert.ok(ready.capabilities.includes('plan-review'));
    execFileSync(python, ['-c', `
import sys
from app.runtime.tasks import TaskStore
from app.runtime.plan import Plan,propose_plan
store=TaskStore(sys.argv[1])
task=store.create(kind='run',mode='plan',objective='审阅集成测试',workspace=sys.argv[2])
plan=Plan.from_dict({'task_id':task.task_id,'plan_id':'plan-it','objective':task.objective,
                    'steps':[{'step_id':'s1','title':'阅读','kind':'read'}]})
propose_plan(store.task_dir(task.task_id),plan)
`, ready.session_path, workspace], {cwd: repoRoot});
    const key = async (value: string) => {view.stdin.write(value); await pause(50);};
    await key('/plan'); await key('\r');
    await waitFor(() => events.some(event => event.type === 'plan_review'));
    const query = events.find(event => event.type === 'plan_review');
    assert.ok(query?.type === 'plan_review' && query.outcome === 'ready');
    await waitFor(() => String(view.lastFrame()).includes('Ctrl+R审阅'));
    await key('跨层草稿'); await key('\u0012'); await key('e');
    assert.ok(!events.some(event => event.type === 'plan_review' && event.outcome === 'launched'));
    await key('\r');
    await waitFor(() => events.some(event => event.type === 'plan_review' && event.outcome === 'launched'));
    const started = events.find(event => event.type === 'plan_review' && event.outcome === 'launched');
    assert.ok(started?.type === 'plan_review' && started.id);
    const executeId = started.id;
    await waitFor(() => events.some(event => event.type === 'done' && event.id === executeId));
    const launch = events.find(event => event.type === 'plan_review' && event.id === executeId);
    assert.ok(launch?.type === 'plan_review' && launch.outcome === 'launched');
    assert.equal(launch.launch.run_started, true);
    assert.equal(launch.launch.run_id, executeId);
    await waitFor(() => String(view.lastFrame()).includes('跨层草稿'));
    const retryId = client.command('plan', {action: 'approve-execute', target: query.target});
    await waitFor(() => events.some(event => event.type === 'plan_review' && event.id === retryId));
    const retry = events.find(event => event.type === 'plan_review' && event.id === retryId);
    assert.ok(retry?.type === 'plan_review' && retry.outcome === 'launched');
    assert.equal(retry.launch.run_started, false);
    assert.equal(retry.launch.run_id, executeId);
    assert.equal(retry.launch.task_id, launch.launch.task_id);
    assert.equal(events.filter(event => event.type === 'done').length, 1);
    const lateEvents: ServerEvent[] = [];
    const unsubscribe = client.subscribe(event => lateEvents.push(event));
    await pause(25);
    unsubscribe();
    assert.ok(!lateEvents.some(event => event.type === 'context_snapshot' && event.snapshot.phase === 'loaded'));
    assert.ok(!events.some(event => event.type === 'error'), JSON.stringify(events.filter(event => event.type === 'error')));
    await client.shutdown();
    cleanup();
    const restoredEvents: ServerEvent[] = [];
    reopened = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace, '--session', ready.session_id]});
    reopened.subscribe(event => restoredEvents.push(event));
    reopened.start();
    await waitFor(() => restoredEvents.some(event => event.type === 'ready'));
    const reopenedId = reopened.command('plan', {action: 'approve-execute', target: query.target});
    await waitFor(() => restoredEvents.some(event => event.type === 'plan_review' && event.id === reopenedId));
    const restored = restoredEvents.find(event => event.type === 'plan_review' && event.id === reopenedId);
    assert.ok(restored?.type === 'plan_review' && restored.outcome === 'launched');
    assert.equal(restored.launch.run_id, executeId);
    assert.equal(restored.launch.task_id, launch.launch.task_id);
    assert.equal(restored.launch.run_started, false);
    assert.ok(!restoredEvents.some(event => event.type === 'done' || event.type === 'text_delta'));
  } finally {
    await client.shutdown();
    await reopened?.shutdown();
    cleanup();
    if (previousRuntime === undefined) delete process.env.VIBE_TASK_RUNTIME;
    else process.env.VIBE_TASK_RUNTIME = previousRuntime;
    await rm(workspace, {recursive: true, force: true});
  }
});

test('real Python backend answers plan/task commands through the TUI client', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-it-'));
  await mkdir(workspace, {recursive: true});
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    await waitFor(() => (client.capabilities?.() ?? []).length > 0);
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write('/plan list');
    await pause(50);
    stdin.write('\r');
    await pause(800);
    const frame = String(view.lastFrame());
    assert.match(frame, /当前会话没有计划产物。/, frame.slice(-800));
    assert.doesNotMatch(frame, /用 \/plan <任务> 生成/);
    stdin.write('/cost');
    await pause(50);
    stdin.write('\r');
    await pause(500);
    assert.match(String(view.lastFrame()), /session /);
  } finally {
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});

test('fake demo run requests approvals and completes through the real backend', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const previous = process.env.VIBE_FAKE_DEMO;
  process.env.VIBE_FAKE_DEMO = '1';
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-approval-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const stdin = view.stdin as unknown as {write(value: string): void};
    // 等 App 完成 ready 初始化（composer 已启用、fake demo 提示已预填）再回车。
    await waitFor(() => String(view.lastFrame()).includes('.vibe/fake-demo.txt'));
    await pause(100);
    stdin.write('\r');
    let answered = 0;
    let resendAt = Date.now() + 4000;
    const deadline = Date.now() + 40000;
    while (true) {
      if (events.some(event => event.type === 'done')) break;
      const approvals = events.filter(event => event.type === 'approval_request').length;
      if (approvals > answered) {
        await waitFor(() => String(view.lastFrame()).includes('草稿焦点'));
        stdin.write('y');
        await pause(50);
        assert.equal(events.filter(event => event.type === 'approval_resolved').length, answered);
        stdin.write('\u0012');
        await waitFor(() => String(view.lastFrame()).includes('审批焦点'));
        stdin.write('y');
        await waitFor(() => String(view.lastFrame()).includes('已选择'));
        assert.equal(events.filter(event => event.type === 'approval_resolved').length, answered);
        stdin.write('\r');
        answered += 1;
        await pause(100);
        continue;
      }
      const started = events.some(event => event.type === 'status' && event.phase === 'running')
        || events.some(event => event.type === 'text_delta')
        || approvals > 0;
      if (!started && Date.now() > resendAt) {
        stdin.write('\r');
        resendAt = Date.now() + 4000;
      }
      if (Date.now() > deadline) throw new Error(`run did not finish; approvals=${answered}`);
      await pause(50);
    }
    const done = events.find(event => event.type === 'done') as Extract<ServerEvent, {type: 'done'}>;
    assert.equal(done.result.status, 'done');
    assert.ok(answered >= 2, `expected >=2 approvals, got ${answered}`);
    const demo = await readFile(path.join(workspace, '.vibe', 'fake-demo.txt'), 'utf8');
    assert.match(demo, /fake demo/);
  } finally {
    if (previous === undefined) delete process.env.VIBE_FAKE_DEMO;
    else process.env.VIBE_FAKE_DEMO = previous;
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});

test('legacy plan overlay approves with a backend without the review capability', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-plan-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={{...client, capabilities: () => []}}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const ready = events.find(event => event.type === 'ready') as Extract<ServerEvent, {type: 'ready'}>;
    const sessionPath = ready.session_path ?? '';
    assert.ok(sessionPath, 'ready should include session_path');
    const taskId = 't-20261001-000000-abcdef';
    const planDir = path.join(sessionPath, 'tasks', taskId, 'plan');
    await mkdir(planDir, {recursive: true});
    const planFile = path.join(planDir, 'plan.v1.json');
    await writeFile(planFile, JSON.stringify({
      schema_version: 1, plan_id: 'pl-tui-1', task_id: taskId, version: 1,
      status: 'proposed', objective: 'TUI 审批计划',
      steps: [{step_id: 's1', title: '读 README', kind: 'read'}],
    }, null, 2));
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write('/plan');
    await pause(80);
    stdin.write('\r');
    await waitFor(() => String(view.lastFrame()).includes('TUI 审批计划'));
    stdin.write('y');
    const deadline = Date.now() + 15000;
    let status = '';
    while (Date.now() < deadline) {
      try {
        status = String(JSON.parse(await readFile(planFile, 'utf8')).status ?? '');
      } catch {status = '';}
      if (status === 'approved') break;
      await pause(50);
    }
    assert.equal(status, 'approved');
    assert.ok(events.some(event => event.type === 'command_result' && event.command === 'plan'));
  } finally {
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});

test('explore command forwards its prompt through the real backend', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-explore-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write('/explore 读取 README 并总结');
    await pause(80);
    stdin.write('\r');
    await waitFor(() => events.some(event => event.type === 'command_result' && event.command === 'explore'));
    const result = events.find(event => event.type === 'command_result' && event.command === 'explore') as
      Extract<ServerEvent, {type: 'command_result'}>;
    assert.match(result.text, /子 Agent ok/);
    assert.ok(events.some(event => event.type === 'subagent_event'));
  } finally {
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});

test('notifications kind filter is forwarded through the real backend', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-notify-'));
  await mkdir(path.join(workspace, '.vibe'), {recursive: true});
  await writeFile(path.join(workspace, '.vibe', 'notifications.jsonl'), [
    JSON.stringify({schema_version: 1, kind: 'worker_started', ts: '2026-11-07T00:00:00+00:00', task_id: 't-1'}),
    JSON.stringify({schema_version: 1, kind: 'plan_acceptance', ts: '2026-11-07T00:00:01+00:00',
      task_id: 't-2', status: true}),
  ].join('\n') + '\n');
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write('/notifications plan_acceptance');
    await pause(80);
    stdin.write('\r');
    await waitFor(() => events.some(event => event.type === 'command_result' && event.command === 'notifications'));
    const result = events.find(event => event.type === 'command_result' && event.command === 'notifications') as
      Extract<ServerEvent, {type: 'command_result'}>;
    assert.match(result.text, /plan_acceptance/);
    assert.doesNotMatch(result.text, /worker_started/);
    assert.match(result.text, /最近通知（1）/);
  } finally {
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});

test('plan step command forwards step_id through the real backend', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-step-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const ready = events.find(event => event.type === 'ready') as Extract<ServerEvent, {type: 'ready'}>;
    const sessionPath = ready.session_path ?? '';
    assert.ok(sessionPath, 'ready should include session_path');
    const taskId = 't-20261001-000000-abcdef';
    const planDir = path.join(sessionPath, 'tasks', taskId, 'plan');
    await mkdir(planDir, {recursive: true});
    await writeFile(path.join(planDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-tui-step', task_id: taskId, version: 1,
      status: 'approved', objective: '步骤详情计划',
      steps: [{step_id: 's1', title: '读 README', kind: 'read', files: ['README.md']}],
    }, null, 2));
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write('/plan step s1');
    await pause(80);
    stdin.write('\r');
    await waitFor(() => events.some(event => event.type === 'command_result' && event.command === 'plan'));
    const result = events.find(event => event.type === 'command_result' && event.command === 'plan') as
      Extract<ServerEvent, {type: 'command_result'}>;
    assert.match(result.text, /s1/);
    assert.match(result.text, /读 README/);
    assert.match(result.text, /files/);
  } finally {
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});

test('task and approvals commands surface seeded pending approvals through the real backend', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-approval-list-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const ready = events.find(event => event.type === 'ready') as Extract<ServerEvent, {type: 'ready'}>;
    const sessionPath = ready.session_path ?? '';
    assert.ok(sessionPath, 'ready should include session_path');
    const sessionId = path.basename(sessionPath);
    const taskId = 't-20261001-000000-abcdef';
    const taskDir = path.join(sessionPath, 'tasks', taskId);
    await mkdir(taskDir, {recursive: true});
    const stamp = new Date().toISOString();
    await writeFile(path.join(taskDir, 'meta.json'), JSON.stringify({
      schema_version: 1, task_id: taskId, session_id: sessionId, kind: 'goal',
      status: 'paused', objective: '待审批任务', mode: 'goal', workspace,
      parent_id: null, goal_id: null, plan_id: null, plan_version: null, priority: 4,
      budget: {max_run_tokens: 0, max_goal_tokens: 0, max_wall_seconds: 0},
      approval_policy: {autonomy: 'ask', scope: 'own', capabilities: []},
      segments: [], result_ref: null, created_at: stamp, updated_at: stamp,
    }, null, 2));
    await writeFile(path.join(taskDir, 'approvals.jsonl'), JSON.stringify({
      schema_version: 1, type: 'requested', approval_id: 'a-test-1',
      task_id: taskId, tool: 'bash', tier: 'exec', requester: 'parent',
      decisions: ['once', 'deny'], created_at: stamp,
    }) + '\n');
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write('/task');
    await pause(80);
    stdin.write('\r');
    await waitFor(() => events.some(event => event.type === 'task_catalog'));
    const catalog = events.find(event => event.type === 'task_catalog')!;
    assert.ok(catalog.type === 'task_catalog');
    assert.equal(catalog.records[0]!.pending_approvals, 1);
    assert.equal('priority' in catalog.records[0]! && catalog.records[0]!.priority, 4);
    assert.equal(catalog.records[0]!.task_id, taskId);
    const legacyId = client.command('tasks');
    await waitFor(() => events.some(event => event.type === 'command_result' && event.id === legacyId));
    const tasksResult = events.find(event => event.type === 'command_result' && event.id === legacyId)!;
    assert.ok(tasksResult.type === 'command_result');
    assert.match(tasksResult.text, /待审批 1/); assert.match(tasksResult.text, /priority=4/);
    stdin.write('\x1b'); await pause(40);
    stdin.write('/approvals');
    await pause(80);
    stdin.write('\r');
    await waitFor(() => events.some(event => event.type === 'command_result' && event.command === 'approvals'));
    const approvalsResult = events.find(event => event.type === 'command_result' && event.command === 'approvals') as
      Extract<ServerEvent, {type: 'command_result'}>;
    assert.match(approvalsResult.text, /a-test-1/);
    assert.match(approvalsResult.text, /bash/);
  } finally {
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});

test('evidence command surfaces acceptance tri-state through the real backend', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-evidence-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const ready = events.find(event => event.type === 'ready') as Extract<ServerEvent, {type: 'ready'}>;
    const sessionPath = ready.session_path ?? '';
    assert.ok(sessionPath, 'ready should include session_path');
    const roundsDir = path.join(sessionPath, 'rounds');
    await mkdir(roundsDir, {recursive: true});
    await writeFile(path.join(roundsDir, 'round_001.json'), JSON.stringify({
      schema_version: 1, round_index: 1, run_id: 'r-ev', goal: '证据任务',
      task_kind: 'run', status: 'done', stop_reason: '', steps: 1,
      usage: {prompt_tokens: 1, completion_tokens: 1, total_tokens: 2},
      cost_usd: 0, started_at: '2026-11-07T00:00:00+00:00', ended_at: '2026-11-07T00:00:01+00:00',
      verified: true,
      completion: {allowed: true, codes: [], messages: []},
      acceptance: {status: false, codes: ['acceptance_failed'],
        plan_id: 'pl-ev', plan_version: 1,
        commands: [{id: 'v1', ok: false, attempts: 2, reason: 'exit=1'}]},
      task_state: {},
    }, null, 2));
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write('/evidence');
    await pause(80);
    stdin.write('\r');
    await waitFor(() => events.some(event => event.type === 'command_result' && event.command === 'evidence'));
    const result = events.find(event => event.type === 'command_result' && event.command === 'evidence') as
      Extract<ServerEvent, {type: 'command_result'}>;
    assert.match(result.text, /最近一轮证据/);
    assert.match(result.text, /验收结果：失败/);
  } finally {
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});

test('trace command renders session observability through the real backend', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-trace-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const ready = events.find(event => event.type === 'ready') as Extract<ServerEvent, {type: 'ready'}>;
    const sessionPath = ready.session_path ?? '';
    assert.ok(sessionPath, 'ready should include session_path');
    await writeFile(path.join(sessionPath, 'trace.jsonl'), [
      JSON.stringify({type: 'run_start', ts: 1}),
      JSON.stringify({type: 'llm_end', ts: 2, prompt_tokens: 10, completion_tokens: 5, cost_usd: 0.001}),
      JSON.stringify({type: 'tool_end', ts: 3, tool: 'bash', ok: false}),
      JSON.stringify({type: 'approval', ts: 4, tool: 'bash', allowed: false}),
      JSON.stringify({type: 'run_end', ts: 5, status: 'stopped', stop_reason: 'max_run_seconds'}),
    ].join('\n') + '\n');
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write('/trace');
    await pause(80);
    stdin.write('\r');
    await waitFor(() => events.some(event => event.type === 'command_result' && event.command === 'trace'));
    const result = events.find(event => event.type === 'command_result' && event.command === 'trace') as
      Extract<ServerEvent, {type: 'command_result'}>;
    assert.match(result.text, /trace 摘要/);
    assert.match(result.text, /max_run_seconds=1/);
    assert.match(result.text, /tokens 15/);
  } finally {
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});

test('/trace with hours filters through the real backend', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-trace-hours-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const ready = events.find(event => event.type === 'ready') as Extract<ServerEvent, {type: 'ready'}>;
    const sessionPath = ready.session_path ?? '';
    assert.ok(sessionPath, 'ready should include session_path');
    const now = Date.now() / 1000;
    await writeFile(path.join(sessionPath, 'trace.jsonl'), [
      JSON.stringify({type: 'llm_end', ts: now - 7200, prompt_tokens: 100, completion_tokens: 50}),
      JSON.stringify({type: 'llm_end', ts: now - 60, prompt_tokens: 10, completion_tokens: 5}),
      JSON.stringify({type: 'run_end', ts: now - 30, status: 'done', stop_reason: ''}),
    ].join('\n') + '\n');
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write('/trace 1');
    await pause(80);
    stdin.write('\r');
    await waitFor(() => events.some(event => event.type === 'command_result' && event.command === 'trace'));
    const result = events.find(event => event.type === 'command_result' && event.command === 'trace') as
      Extract<ServerEvent, {type: 'command_result'}>;
    assert.match(result.text, /tokens 15/);
    assert.doesNotMatch(result.text, /tokens 150/);
  } finally {
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});


test('/priority reprioritizes a created task through the real backend', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-priority-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const ready = events.find(event => event.type === 'ready') as Extract<ServerEvent, {type: 'ready'}>;
    const sessionPath = ready.session_path ?? '';
    assert.ok(sessionPath, 'ready should include session_path');
    const script = [
      'import sys',
      'from app.runtime.tasks import TaskStore',
      'task = TaskStore(sys.argv[1]).create(kind="run", objective="队列任务", workspace=sys.argv[2], mode="act")',
      'print(task.task_id)',
    ].join('\n');
    const taskId = execFileSync(python, ['-c', script, sessionPath, workspace],
      {cwd: repoRoot, encoding: 'utf8'}).trim();
    assert.ok(taskId.startsWith('t-'), taskId);
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write(`/priority ${taskId} 7`);
    await pause(80);
    stdin.write('\r');
    await waitFor(() => events.some(event => event.type === 'command_result' && event.command === 'priority'));
    const result = events.find(event => event.type === 'command_result' && event.command === 'priority') as
      Extract<ServerEvent, {type: 'command_result'}>;
    assert.match(result.text, /优先级为 7/);
    const meta = JSON.parse(await readFile(path.join(sessionPath, 'tasks', taskId, 'meta.json'), 'utf8')) as
      {priority: number};
    assert.equal(meta.priority, 7);
  } finally {
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});


test('/plan kind edits a step through the real backend', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-plan-kind-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const ready = events.find(event => event.type === 'ready') as Extract<ServerEvent, {type: 'ready'}>;
    const sessionPath = ready.session_path ?? '';
    assert.ok(sessionPath, 'ready should include session_path');
    const taskId = 't-20261103-120000-abcdef';
    const planDir = path.join(sessionPath, 'tasks', taskId, 'plan');
    await mkdir(planDir, {recursive: true});
    await writeFile(path.join(planDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-kind-it', task_id: taskId, version: 1,
      status: 'proposed', objective: '类型编辑', acceptance_criteria: [],
      steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write('/plan kind s1 verify');
    await pause(80);
    stdin.write('\r');
    await waitFor(() => events.some(event => event.type === 'command_result' && event.command === 'plan'));
    const result = events.find(event => event.type === 'command_result' && event.command === 'plan') as
      Extract<ServerEvent, {type: 'command_result'}>;
    assert.match(result.text, /\[verify\/read\]/);
    const revised = JSON.parse(await readFile(path.join(planDir, 'plan.v2.json'), 'utf8')) as
      {steps: {kind: string; title: string}[]};
    assert.equal(revised.steps[0]?.kind, 'verify');
    assert.equal(revised.steps[0]?.title, '读');
  } finally {
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});


test('/plan card renders approval card through the real backend', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-plan-card-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const ready = events.find(event => event.type === 'ready') as Extract<ServerEvent, {type: 'ready'}>;
    const sessionPath = ready.session_path ?? '';
    assert.ok(sessionPath, 'ready should include session_path');
    const taskId = 't-20261103-120000-abcdef';
    const planDir = path.join(sessionPath, 'tasks', taskId, 'plan');
    await mkdir(planDir, {recursive: true});
    await writeFile(path.join(planDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-card-it', task_id: taskId, version: 1,
      status: 'proposed', objective: '审批卡', acceptance_criteria: [],
      constraints: ['只读'],
      verification_plan: [{id: 'v1', covers: [], command: 'true', source: 'plan_approval'}],
      steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write('/plan card');
    await pause(80);
    stdin.write('\r');
    await waitFor(() => events.some(event => event.type === 'command_result' && event.command === 'plan'));
    const result = events.find(event => event.type === 'command_result' && event.command === 'plan') as
      Extract<ServerEvent, {type: 'command_result'}>;
    assert.match(result.text, /计划审批卡/);
    assert.match(result.text, /约束：只读/);
    assert.match(result.text, /cmd=true/);
    assert.match(result.text, /executable=False/);
  } finally {
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});


test('/queue lists pending plan tasks through the real backend', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-queue-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const ready = events.find(event => event.type === 'ready') as Extract<ServerEvent, {type: 'ready'}>;
    const sessionPath = ready.session_path ?? '';
    assert.ok(sessionPath, 'ready should include session_path');
    const script = [
      'import sys',
      'from app.runtime.tasks import TaskStore',
      'task = TaskStore(sys.argv[1]).create(kind="run", objective="排队任务", workspace=sys.argv[2],',
      '    mode="act", plan_id="pl-q", plan_version=1, priority=3)',
      'print(task.task_id)',
    ].join('\n');
    const taskId = execFileSync(python, ['-c', script, sessionPath, workspace],
      {cwd: repoRoot, encoding: 'utf8'}).trim();
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write('/queue');
    await pause(80);
    stdin.write('\r');
    await waitFor(() => events.some(event => event.type === 'command_result' && event.command === 'queue'));
    const result = events.find(event => event.type === 'command_result' && event.command === 'queue') as
      Extract<ServerEvent, {type: 'command_result'}>;
    assert.match(result.text, /计划队列（1）/);
    assert.match(result.text, new RegExp(taskId));
    assert.match(result.text, /priority 3/);
  } finally {
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});
