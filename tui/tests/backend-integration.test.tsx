import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdir, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
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

async function waitFor(condition: () => boolean, timeoutMs = 15000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error('timed out waiting for condition');
    await pause(25);
  }
}

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
        stdin.write('y');
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

test('plan overlay approves a proposed plan through the real backend', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-plan-'));
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
    await waitFor(() => events.some(event => event.type === 'command_result' && event.command === 'tasks'));
    const tasksResult = events.find(event => event.type === 'command_result' && event.command === 'tasks') as
      Extract<ServerEvent, {type: 'command_result'}>;
    assert.match(tasksResult.text, /待审批 1/);
    assert.match(tasksResult.text, /priority=4/);
    assert.match(tasksResult.text, new RegExp(taskId));
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
