import assert from 'node:assert/strict';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import {App} from '../src/app.js';
import type {BackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';
import {initialState, type ViewState} from '../src/view-state.js';
import stringWidth from 'string-width';
import {contextSnapshot, loadedContextSnapshot} from './context-fixture.js';

class MockClient {
  listeners = new Set<(event: ServerEvent) => void>();
  runs: string[] = [];
  cancellations: string[] = [];
  steers: {target: string; text: string}[] = [];
  approvals: [string, 'once' | 'session' | 'workspace' | 'deny'][] = [];
  commands: string[] = [];
  commandArgs: (Record<string, unknown> | undefined)[] = [];
  shutdowns = 0;
  sendError = false;

  subscribe(listener: (event: ServerEvent) => void) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
  run(text: string) {this.runs.push(text); return `run-${this.runs.length}`;}
  cancel(id: string) {if (this.sendError) throw new Error('backend disconnected'); this.cancellations.push(id); return `cancel-${this.cancellations.length}`;}
  steer(target: string, text: string) {if (this.sendError) throw new Error('backend disconnected'); this.steers.push({target, text}); return `steer-${this.steers.length}`;}
  approve(id: string, decision: 'once' | 'session' | 'workspace' | 'deny') {if (this.sendError) throw new Error('backend disconnected'); this.approvals.push([id, decision]);}
  command(command: string, args?: Record<string, unknown>) {if (this.sendError) throw new Error('backend disconnected'); this.commands.push(command); this.commandArgs.push(args); return `command-${this.commands.length}`;}
  async shutdown() {this.shutdowns += 1;}
  emit(event: Record<string, unknown>) {
    for (const listener of this.listeners) listener({v: 1, id: null, ...event} as ServerEvent);
  }
}

afterEach(cleanup);

async function mount(copyText?: (text: string) => string | null, initialState?: ViewState, windowSize?: {columns: number; rows: number}, profilePath?: string, userProfilePath?: string) {
  const client = new MockClient();
  const view = render(<App client={client as unknown as BackendClient} copyText={copyText} initialState={initialState} windowSize={windowSize} profilePath={profilePath} userProfilePath={userProfilePath}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  return {client, ...view};
}

async function enter(stdin: {write(value: string): void}, text: string) {
  stdin.write(text);
  await pause(15);
  stdin.write('\r');
  await pause(25);
}

function completed(id: string, status: 'done' | 'cancelled' = 'done') {
  return {type: 'done', id, result: {text: '', steps: 1, status, usage: {}, cost_usd: 0, run_id: id, stop_reason: ''}};
}

for (const [columns, rows] of [[80, 24], [120, 24], [120, 40]]) {
  test(`session load shows base occupancy without running a task at ${columns}x${rows}`, async () => {
    const client = new MockClient();
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: columns!, rows: rows!}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 's', capabilities: []});
    const snapshot = loadedContextSnapshot();
    client.emit({type: 'context_snapshot', snapshot});
    client.emit({type: 'metrics', used_tokens: 0, limit_tokens: 1000, estimated: true,
      context_tokens: snapshot.total_tokens, context_window: snapshot.context_window, done: 0, planned: 0, cost_usd: 0});
    client.emit({type: 'status', phase: 'idle'});
    await pause(25);
    const frame = lastFrame() ?? '';
    assert.match(frame, /上下文.*估算.*50\/900/);
    assert.match(frame, /S20 M0 T30 H0/);
    assert.doesNotMatch(frame, /尚无用量记录/);
    assert.ok(frame.split('\n').every(line => stringWidth(line) <= columns!));
    assert.ok(frame.split('\n').length <= rows!);
    assert.deepEqual(client.runs, []);
    assert.deepEqual(client.commands, []);
    await enter(stdin, '/context');
    assert.match(lastFrame() ?? '', /基础上下文已装载/);
    assert.match(lastFrame() ?? '', /尚未请求模型/);
    assert.doesNotMatch(lastFrame() ?? '', /请求前|步骤 0/);
    stdin.write('\u001b');
    await pause(20);
    await enter(stdin, 'work');
    client.emit({type: 'context_snapshot', id: 'run-1', snapshot: contextSnapshot()});
    await pause(20);
    stdin.write('\u000b');
    await pause(20);
    assert.match(lastFrame() ?? '', /快照 1\/1/);
    assert.match(lastFrame() ?? '', /步骤 1/);
    assert.doesNotMatch(lastFrame() ?? '', /基础上下文已装载/);
    assert.deepEqual(client.runs, ['work']);
  });
}

test('short sidebar keeps partition occupancy visible during a run with task, tools and metrics', async () => {
  const client = new MockClient();
  const snapshot = {...contextSnapshot(), input_capacity: 960000, context_window: 1000000};
  const state = {...initialState, phase: 'running' as const, contextSnapshots: [snapshot],
    metrics: {used_tokens: 8000, limit_tokens: 10000, estimated: false, context_tokens: 100, context_window: 1000000, done: 0, planned: 0, cost_usd: 0},
    entries: [{id: 0, kind: 'task' as const, text: '检查工作区'}, {id: 1, kind: 'tool' as const, text: '读取文件', call: {id: 'c', name: 'read_file', args: {}}, ok: true}]};
  const {lastFrame} = render(<App client={client as unknown as BackendClient} initialState={state} windowSize={{columns: 120, rows: 24}}/>);
  await pause(20);
  const frame = lastFrame() ?? '';
  assert.match(frame, /上下文估算 100\/960k/);
  assert.match(frame, /S20 M0 T30 H50/);
  assert.match(frame, /已用构成/);
  assert.match(frame, /━/);
  assert.ok(frame.split('\n').every(line => stringWidth(line) <= 120));
  assert.ok(frame.split('\n').length <= 24);
});

for (const [columns, rows] of [[80, 24], [120, 40]]) {
  test(`/context opens local partition replay in the ${columns} column layout`, async () => {
    const client = new MockClient();
    const first = contextSnapshot();
    const second = {...first, snapshot_id: 'snapshot-2', request_id: 'request-2', step: 2, phase: 'after_compaction' as const};
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: columns!, rows: rows!}}
      initialState={{...initialState, phase: 'idle', contextSnapshots: [first, second]}}/>);
    await pause(25);
    await enter(stdin, '/context');
    assert.match(lastFrame() ?? '', /上下文分区/);
    assert.match(lastFrame() ?? '', /快照 2\/2/);
    assert.match(lastFrame() ?? '', /压缩后/);
    assert.match(lastFrame() ?? '', /估算/);
    assert.match(lastFrame() ?? '', /history 历史/);
    assert.deepEqual(client.runs, []);
    assert.deepEqual(client.commands, []);
    stdin.write('\u001b[D');
    await pause(25);
    assert.match(lastFrame() ?? '', /快照 1\/2/);
    assert.match(lastFrame() ?? '', /步骤 1/);
    stdin.write('\u001b[F');
    await pause(25);
    assert.match(lastFrame() ?? '', /README.md/);
    assert.ok((lastFrame() ?? '').split('\n').every(line => stringWidth(line) <= columns!));
    assert.ok((lastFrame() ?? '').split('\n').length <= rows!);
    stdin.write('l');
    await pause(25);
    assert.match(lastFrame() ?? '', /快照 2\/2/);
    stdin.write('\u001b');
    await pause(25);
    assert.doesNotMatch(lastFrame() ?? '', /上下文分区 · \d/);
  });
}

test('context detail is available during a run and keeps an older snapshot stable while new events arrive', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'work');
  client.emit({type: 'context_snapshot', id: 'run-1', snapshot: contextSnapshot()});
  stdin.write('\u000b');
  await pause(25);
  assert.match(lastFrame() ?? '', /上下文分区/);
  client.emit({type: 'context_snapshot', id: 'run-1', snapshot: {...contextSnapshot(), snapshot_id: 'snapshot-2', step: 2}});
  await pause(25);
  assert.match(lastFrame() ?? '', /快照 2\/2/);
  stdin.write('\u001b[D');
  await pause(25);
  client.emit({type: 'context_snapshot', id: 'run-1', snapshot: {...contextSnapshot(), snapshot_id: 'snapshot-3', step: 3}});
  await pause(25);
  assert.match(lastFrame() ?? '', /快照 1\/3/);
  assert.match(lastFrame() ?? '', /步骤 1/);
  assert.deepEqual(client.runs, ['work']);
});

test('context detail explains missing snapshots with older backends without sending a model command', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, '/context');
  assert.match(lastFrame() ?? '', /尚无分区快照/);
  assert.deepEqual(client.runs, []);
  assert.deepEqual(client.commands, []);
});

test('streams a task and retains two completed rounds without duplicate final text', async () => {
  const {client, stdin, lastFrame, frames, unmount} = await mount();
  await enter(stdin, 'first task');
  assert.deepEqual(client.runs, ['first task']);
  client.emit({type: 'text_delta', id: 'run-1', delta: 'first reply'});
  await pause(20);
  assert.match(lastFrame() ?? '', /first reply/);
  client.emit({type: 'done', id: 'run-1', result: {text: 'first reply', steps: 1, status: 'done', usage: {}, cost_usd: 0, run_id: 'run-1', stop_reason: null}});
  await pause(20);
  const firstDone = lastFrame() ?? '';
  assert.equal(firstDone.split('first reply').length - 1, 1);
  await enter(stdin, 'second task');
  client.emit({type: 'text_delta', id: 'run-2', delta: 'second reply'});
  client.emit({type: 'done', id: 'run-2', result: {text: 'second reply', steps: 1, status: 'done', usage: {}, cost_usd: 0, run_id: 'run-2', stop_reason: null}});
  await pause(30);
  const output = frames.join('\n');
  assert.match(output, /first reply/);
  assert.match(output, /second reply/);
  assert.deepEqual(client.runs, ['first task', 'second task']);
  unmount();
  assert.equal(client.listeners.size, 0);
});

test('shows busy state and backend errors without submitting another task', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'work');
  assert.match(lastFrame() ?? '', /运行中/);
  await enter(stdin, 'second');
  assert.equal(client.runs.length, 1);
  client.emit({type: 'error', code: 'provider_failed', message: 'provider exploded'});
  await pause(20);
  assert.match(lastFrame() ?? '', /provider exploded/);
  client.emit({type: 'closed'});
  await pause(20);
  assert.match(lastFrame() ?? '', /后端已断开/);
});

test('shows complete approval preview and clears a denied request', async () => {
  const {client, stdin, lastFrame, frames} = await mount();
  await enter(stdin, 'write file');
  client.emit({type: 'approval_request', id: 'run-1', approval_id: 'approval-1', call: {id: 'tool-1', name: 'write_file', args: {}}, preview: 'PREVIEW_START\nwrite /tmp/repo/a.txt\nPREVIEW_END'});
  await pause(20);
  assert.match(lastFrame() ?? '', /等待审批/);
  assert.match(frames.join('\n'), /PREVIEW_END/);
  stdin.write('n');
  await pause(20);
  assert.deepEqual(client.approvals, [['approval-1', 'deny']]);
  assert.doesNotMatch(lastFrame() ?? '', /y 允许一次/);
  client.emit({type: 'approval_resolved', id: 'run-1', approval_id: 'approval-1', decision: 'deny'});
  await pause(20);
  assert.doesNotMatch(lastFrame() ?? '', /y 允许一次/);
});

test('Ctrl+C requests cancellation and waits for terminal result', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'long task');
  stdin.write('\u0003');
  await pause(20);
  assert.deepEqual(client.cancellations, ['run-1']);
  assert.match(lastFrame() ?? '', /正在停止/);
  assert.equal(client.shutdowns, 0);
  client.emit({type: 'done', id: 'run-1', result: {text: 'stopped safely', steps: 1, status: 'cancelled', usage: {}, cost_usd: 0, run_id: 'run-1', stop_reason: 'cancelled'}});
  await pause(20);
  assert.match(lastFrame() ?? '', /已取消/);
});

test('keeps commentary before tools and clips argument summaries explicitly', async () => {
  const {client, stdin, lastFrame, frames} = await mount();
  await enter(stdin, 'inspect');
  client.emit({type: 'text_delta', id: 'run-1', delta: 'I will inspect the source.'});
  client.emit({type: 'tool_call', id: 'run-1', call: {id: 'tool-1', name: 'write_file', args: {content: 'x'.repeat(20_000)}}});
  await pause(25);
  const output = frames.join('\n');
  assert.match(output, /I will inspect the source\./);
  assert.doesNotMatch(lastFrame() ?? '', /x{1000}/);
  client.emit({type: 'tool_result', id: 'run-1', call: {id: 'tool-1', name: 'write_file', args: {}}, ok: true, output: 'exit=7'});
  await pause(20);
  assert.match(lastFrame() ?? '', /调用已返回/);
  assert.doesNotMatch(lastFrame() ?? '', /测试通过/);
});

test('explicit resume reaches the backend once and uses the cancellable command id', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, '/resume');
  assert.deepEqual(client.commands, ['resume']);
  assert.equal(client.runs.length, 0);
  stdin.write('\u0003');
  await pause(20);
  assert.deepEqual(client.cancellations, ['command-1']);
  client.emit({type: 'done', id: 'command-1', result: {text: '继续任务已取消', steps: 0, status: 'cancelled', usage: {}, cost_usd: 0, run_id: '', stop_reason: 'cancelled'}});
  await pause(20);
  await enter(stdin, '/unknown');
  assert.match(lastFrame() ?? '', /未知命令/);
  assert.equal(client.runs.length, 0);
});

test('Ctrl+Q gracefully shuts down even during a run', async () => {
  const {client, stdin} = await mount();
  await enter(stdin, 'work');
  stdin.write('\u0011');
  await pause(25);
  assert.equal(client.shutdowns, 1);
});

test('a startup error is labeled before the backend closes', async () => {
  const client = new MockClient();
  const {lastFrame} = render(<App client={client as unknown as BackendClient}/>);
  await pause(20);
  client.emit({type: 'error', code: 'backend_spawn', message: 'missing python'});
  await pause(20);
  assert.match(lastFrame() ?? '', /启动失败/);
  assert.match(lastFrame() ?? '', /missing python/);
});

test('disconnected input explains restart instead of waiting for a nonexistent task', async () => {
  const {client, lastFrame} = await mount();
  client.emit({type: 'error', code: 'backend_exit', message: 'Python 后端退出（exit 2）'});
  client.emit({type: 'closed'});
  await pause(20);
  assert.match(lastFrame() ?? '', /重启|重新启动/);
  assert.doesNotMatch(lastFrame() ?? '', /当前任务结束后|草稿可编辑 · 当前任务结束/);
});

test('restoration keeps submitted text as a draft until initial status arrives', async () => {
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 'saved', capabilities: [], resumed: true});
  await pause(20);
  await enter(stdin, '恢复期间的草稿');
  assert.deepEqual(client.runs, []);
  assert.match(lastFrame() ?? '', /恢复期间的草稿/);
  client.emit({type: 'status', id: 'startup', phase: 'starting'});
  client.emit({type: 'approval_request', id: 'startup', approval_id: 'startup-race', call: {id: 'start-mcp', name: 'mcp_start', args: {}}, preview: '启动服务', decisions: ['once', 'session', 'deny']});
  await pause(20);
  assert.match(lastFrame() ?? '', /等待审批/);
  stdin.write('n');
  await pause(20);
  assert.deepEqual(client.approvals, [['startup-race', 'deny']]);
  client.emit({type: 'approval_resolved', id: 'startup', approval_id: 'startup-race', decision: 'deny'});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  stdin.write('\r');
  await pause(20);
  assert.deepEqual(client.runs, ['恢复期间的草稿']);
});

test('PageUp preserves the reading position when new text arrives and End returns to latest', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'long reply');
  client.emit({type: 'text_delta', id: 'run-1', delta: Array.from({length: 70}, (_, i) => `history-line-${i}`).join('\n')});
  await pause(30);
  assert.match(lastFrame() ?? '', /history-line-69/);
  stdin.write('\u001b[5~');
  await pause(30);
  const before = lastFrame() ?? '';
  assert.match(before, /回看/);
  const anchor = before.match(/history-line-\d+/)?.[0];
  assert.ok(anchor);
  client.emit({type: 'text_delta', id: 'run-1', delta: '\nnew-after-scrolling'});
  await pause(30);
  assert.match(lastFrame() ?? '', new RegExp(anchor));
  assert.doesNotMatch(lastFrame() ?? '', /new-after-scrolling/);
  stdin.write('\u001b[1;5F');
  await pause(30);
  assert.match(lastFrame() ?? '', /new-after-scrolling/);
});

async function scrollFixture() {
  const client = new MockClient();
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 80, rows: 24}} initialState={{...initialState,
    phase: 'running', activeId: 'scroll-run', workspace: '/repo', model: 'fixture',
    entries: [{id: 0, kind: 'assistant', text: Array.from({length: 90}, (_, i) => `scroll-line-${i}`).join('\n')}], nextEntry: 1}}/>);
  await pause(25);
  return {client, ...view};
}

test('mouse wheel scrolls history without touching the draft and batches every wheel tick', async () => {
  const {client, stdin, lastFrame} = await scrollFixture();
  stdin.write('下一条草稿');
  await pause(20);
  assert.match(lastFrame() ?? '', /scroll-line-89/);
  stdin.write('\u001b[<64;10;8M');
  await pause(25);
  assert.doesNotMatch(lastFrame() ?? '', /scroll-line-89/);
  const first = Number((lastFrame() ?? '').match(/scroll-line-(\d+)/)?.[1]);
  assert.ok(Number.isFinite(first));
  for (let tick = 0; tick < 6; tick += 1) stdin.write('\u001b[<64;10;8M');
  await pause(25);
  const earlier = Number((lastFrame() ?? '').match(/scroll-line-(\d+)/)?.[1]);
  assert.ok(earlier <= first - 15, `wheel batch moved only ${first-earlier} lines`);
  assert.match(lastFrame() ?? '', /下一条草稿/);
  client.emit({type: 'text_delta', id: 'scroll-run', delta: '新回复不能抢走回看位置'});
  await pause(25);
  assert.match(lastFrame() ?? '', new RegExp(`scroll-line-${earlier}`));
  assert.doesNotMatch(lastFrame() ?? '', /新回复不能抢走回看位置/);
  stdin.write('\u001b[1;5F');
  await pause(25);
  assert.match(lastFrame() ?? '', /新回复不能抢走回看位置/);
  assert.deepEqual(client.runs, []);
});

test('reading focus uses arrows and Escape restores input without changing its draft', async () => {
  const {stdin, lastFrame} = await scrollFixture();
  stdin.write('草稿');
  await pause(20);
  stdin.write('\u0012');
  await pause(20);
  for (let i=0; i<5; i+=1) stdin.write('\u001b[A');
  await pause(20);
  assert.doesNotMatch(lastFrame() ?? '', /scroll-line-89/);
  assert.match(lastFrame() ?? '', /阅读/);
  assert.match(lastFrame() ?? '', /草稿/);
  stdin.write('\u001b');
  await pause(20);
  stdin.write('继续');
  await pause(20);
  assert.match(lastFrame() ?? '', /草稿继续/);
});

test('mouse reports in pasted text cannot scroll or approve a pending operation', async () => {
  const {client, stdin, lastFrame} = await scrollFixture();
  stdin.write('\u001b[200~\u001b[<64;10;8My\u001b[201~');
  await pause(25);
  assert.match(lastFrame() ?? '', /scroll-line-89/);
  assert.deepEqual(client.approvals, []);
});

test('wheel events outside the conversation do not move its reading position', async () => {
  const {stdin, lastFrame} = await scrollFixture();
  stdin.write('\u001b[<64;10;24M');
  await pause(25);
  assert.match(lastFrame() ?? '', /scroll-line-89/);
});

for (const [columns, rows] of [[80, 24], [120, 40], [48, 18], [32, 12]]) {
  test(`workbench fits ${columns}x${rows} with task input and readable navigation`, async () => {
    const client = new MockClient();
    const {lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns, rows}} initialState={{...initialState, phase: 'idle', workspace: '/项目/mini-harness', model: '示例模型'}}/>);
    await pause(30);
    const lines = (lastFrame() ?? '').split('\n');
    assert.ok(lines.length <= rows, `rendered ${lines.length} rows for ${rows}`);
    assert.ok(lines.every(line => stringWidth(line) <= columns), 'no horizontal overflow');
    assert.match(lastFrame() ?? '', /mini-harness/);
    assert.match(lastFrame() ?? '', /输入|任务|开始/);
    assert.match(lastFrame() ?? '', /Ctrl/);
  });
}

test('tool details expose full output with paging instead of losing the middle', async () => {
  const client = new MockClient();
  const longOutput = ['OUTPUT_START', ...Array.from({length: 70}, (_, i) => `tool-line-${i}`), 'OUTPUT_END'].join('\n');
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 80, rows: 24}} initialState={{...initialState, phase: 'idle', entries: [{id: 1, kind: 'tool', text: 'read_file · 调用已返回', output: longOutput, call: {id: 'tool-1', name: 'read_file', args: {path: 'hello.ts'}}, ok: true}]}}/>);
  await pause(25);
  stdin.write('\u000f');
  await pause(25);
  assert.match(lastFrame() ?? '', /工具详情/);
  assert.match(lastFrame() ?? '', /OUTPUT_START/);
  stdin.write('\u001b[F');
  await pause(25);
  assert.match(lastFrame() ?? '', /OUTPUT_END/);
  stdin.write('\u001b');
  await pause(25);
  assert.doesNotMatch(lastFrame() ?? '', /工具详情/);
});

test('disconnection during cancel or approval stays visible without crashing the UI', async () => {
  const {client, stdin, frames} = await mount();
  await enter(stdin, 'work');
  client.sendError = true;
  stdin.write('\u0003');
  await pause(20);
  assert.match(frames.join('\n'), /取消请求未送达/);
  client.emit({type: 'approval_request', id: 'run-1', approval_id: 'approval-1', call: {id: 'tool-1', name: 'write_file', args: {}}, preview: 'write a.txt'});
  await pause(20);
  stdin.write('y');
  await pause(20);
  assert.match(frames.join('\n'), /审批响应未送达/);
  assert.equal(client.approvals.length, 0);
});

test('Ctrl+C clears an approval prompt and late resolution keeps stopping state', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'work');
  client.emit({type: 'approval_request', id: 'run-1', approval_id: 'approval-1', call: {id: 'tool-1', name: 'write_file', args: {}}, preview: 'write a.txt'});
  await pause(20);
  stdin.write('\u0003');
  await pause(20);
  assert.deepEqual(client.cancellations, ['run-1']);
  assert.doesNotMatch(lastFrame() ?? '', /y 允许一次/);
  client.emit({type: 'approval_resolved', id: 'run-1', approval_id: 'approval-1', decision: 'deny'});
  await pause(20);
  assert.match(lastFrame() ?? '', /正在停止/);
});

test('approval ignores pasted y and keeps full preview pageable at narrow dimensions', async () => {
  const client = new MockClient();
  const preview = ['PREVIEW_START', ...Array.from({length: 50}, (_, i) => `preview-${i}`), 'PREVIEW_END'].join('\n');
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 32, rows: 12}} initialState={{...initialState, phase: 'waiting_approval', activeId: 'run-1', approval: {id: 'approval-1', tool: 'write_file', call: {id: 't1', name: 'write_file', args: {}}, preview}}}/>);
  await pause(30);
  assert.match(lastFrame() ?? '', /y 允许一次 \/ n 拒绝/);
  stdin.write('\u001b[200~y\u001b[201~');
  await pause(25);
  assert.equal(client.approvals.length, 0);
  stdin.write('\u001b[F');
  await pause(25);
  assert.match(lastFrame() ?? '', /PREVIEW_END/);
  const lines = (lastFrame() ?? '').split('\n');
  assert.ok(lines.length <= 12);
  assert.ok(lines.every(line => stringWidth(line) <= 32));
  stdin.write('n');
  await pause(25);
  assert.deepEqual(client.approvals, [['approval-1', 'deny']]);
});

test('busy editor keeps an unsent draft and sends it after the run finishes', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'first');
  stdin.write('next draft');
  await pause(20);
  assert.deepEqual(client.runs, ['first']);
  assert.deepEqual(client.steers, []);
  assert.match(lastFrame() ?? '', /next draft/);
  client.emit({type: 'done', id: 'run-1', result: {text: 'done', steps: 1, status: 'done', usage: {}, cost_usd: 0, run_id: 'run-1', stop_reason: ''}});
  await pause(25);
  stdin.write('\r');
  await pause(25);
  assert.deepEqual(client.runs, ['first', 'next draft']);
});

test('theme stays local and cost displays backend session accounting', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, '/theme light');
  assert.match(lastFrame() ?? '', /主题已切换：light/);
  await enter(stdin, '/cost');
  assert.deepEqual(client.commands, ['cost']);
  client.emit({type: 'command_result', id: 'command-1', command: 'cost', text: '会话累计 tokens 1,234 · runs 2 · $0.002'});
  await pause(20);
  assert.match(lastFrame() ?? '', /1,234/);
  await enter(stdin, 'next task');
  assert.deepEqual(client.runs, ['next task']);
});

test('slash opens a scrollable command menu without overflowing the frame', async () => {
  const {stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  stdin.write('/');
  await pause(25);
  let frame = lastFrame() ?? '';
  assert.match(frame, /› \/help/);
  assert.match(frame, /查看操作帮助/);
  assert.match(frame, /↑↓ 选择 · Tab 填充 · 1\/24/);
  assert.ok(frame.split('\n').length <= 24);
  for (const line of frame.split('\n')) assert.ok(stringWidth(line) <= 80, line);
  stdin.write('\u001b[B');
  await pause(15);
  assert.match(lastFrame() ?? '', /2\/24/);
  stdin.write('\t');
  await pause(15);
  frame = lastFrame() ?? '';
  assert.match(frame, /\/resume {2,}继续已保存的任务/);
});

test('skills lifecycle commands use backend management and display results without running a task', async () => {
  const {client, stdin, lastFrame} = await mount();
  const commands = ['/skill', '/skills refresh', '/skills activate release', '/skill unload release'];
  for (const [index, command] of commands.entries()) {
    await enter(stdin, command);
    client.emit({type: 'command_result', id: `command-${index + 1}`, command: 'skills', text: `技能操作完成 ${index + 1}`});
    await pause(20);
    assert.match(lastFrame() ?? '', new RegExp(`技能操作完成 ${index + 1}`));
  }
  assert.deepEqual(client.commands, Array(4).fill('skills'));
  assert.deepEqual(client.commandArgs, [{action: 'list'}, {action: 'refresh'},
    {action: 'activate', name: 'release'}, {action: 'unload', name: 'release'}]);
  assert.deepEqual(client.runs, []);
});

test('invalid skills syntax shows usage without invoking model or backend', async () => {
  const {client, stdin, lastFrame} = await mount();
  for (const command of ['/skills activate', '/skills list release', '/skills delete release']) {
    await enter(stdin, command);
    assert.match(lastFrame() ?? '', /用法/);
  }
  assert.deepEqual(client.commands, []);
  assert.deepEqual(client.runs, []);
});

test('skills management stays in the draft while a task is running', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'active task');
  await enter(stdin, '/skills activate release');
  assert.deepEqual(client.commands, []);
  assert.deepEqual(client.runs, ['active task']);
  assert.match(lastFrame() ?? '', /\/skills activate release/);
});

test('startup approvals support exact rules and ignore forbidden workspace scope', async () => {
  const {client, stdin, lastFrame} = await mount();
  client.emit({type: 'status', id: 'startup', phase: 'starting'});
  client.emit({type: 'approval_request', id: 'startup', approval_id: 'mcp-start', call: {id: 'call-start', name: 'mcp_start', args: {command: 'local-server'}}, preview: '启动本地 MCP 服务', decisions: ['once', 'session', 'deny']});
  await pause(20);
  assert.match(lastFrame() ?? '', /精确规则/);
  assert.doesNotMatch(lastFrame() ?? '', /p 工作区/);
  stdin.write('p');
  await pause(20);
  assert.deepEqual(client.approvals, []);
  stdin.write('s');
  stdin.write('s');
  await pause(20);
  assert.deepEqual(client.approvals, [['mcp-start', 'session']]);
  client.emit({type: 'approval_resolved', id: 'startup', approval_id: 'mcp-start', decision: 'session'});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, 'work after startup');
  assert.deepEqual(client.runs, ['work after startup']);
});

test('all native approval choices remain discoverable at 32 columns and workspace scope submits once', async () => {
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 32, rows: 12}} initialState={{...initialState, phase: 'waiting_approval', activeId: 'run-1', approval: {id: 'native-approval', tool: 'write_file', call: {id: 't1', name: 'write_file', args: {}}, preview: 'write new.txt', decisions: ['once', 'session', 'workspace', 'deny']}}}/>);
  await pause(20);
  const hint = (lastFrame() ?? '').split('\n').at(-1) ?? '';
  assert.match(hint, /y.*s.*p.*n/);
  stdin.write('p');
  stdin.write('p');
  await pause(20);
  assert.deepEqual(client.approvals, [['native-approval', 'workspace']]);
});

test('restored user history is available for editing and never automatically submitted', async () => {
  const {client, stdin, lastFrame} = await mount();
  client.emit({type: 'history_message', message: {role: 'user', content: '上一次未完成的任务', tool_calls: []}});
  client.emit({type: 'notice', message: '已恢复原会话，输入 /resume 显式继续。'});
  await pause(20);
  assert.deepEqual(client.runs, []);
  assert.deepEqual(client.commands, []);
  stdin.write('\u001b[A');
  await pause(20);
  assert.match(lastFrame() ?? '', /上一次未完成的任务/);
});

test('permissions and diff share the backend gate and render command responses during a task', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, '/permissions reset');
  assert.deepEqual(client.commands, ['permissions_reset']);
  client.emit({type: 'command_result', id: 'command-1', command: 'permissions_reset', text: '已撤销当前工作区授权'});
  await pause(20);
  assert.match(lastFrame() ?? '', /已撤销/);
  await enter(stdin, '/diff');
  assert.deepEqual(client.commands, ['permissions_reset', 'diff']);
  client.emit({type: 'approval_request', id: 'command-2', approval_id: 'diff-preview', call: {id: 'diff-1', name: 'bash', args: {command: 'git diff'}}, preview: '查看工作区差异', decisions: ['once', 'session', 'workspace', 'deny']});
  await pause(20);
  stdin.write('n');
  await pause(20);
  assert.deepEqual(client.approvals, [['diff-preview', 'deny']]);
  client.emit({type: 'command_result', id: 'command-2', command: 'diff', text: '已拒绝，未执行命令'});
  await pause(20);
  assert.match(lastFrame() ?? '', /已拒绝/);
});

test('repeated Enter in one input batch submits only one run', async () => {
  const {client, stdin} = await mount();
  stdin.write('one task');
  await pause(20);
  stdin.write('\r');
  stdin.write('\r');
  await pause(30);
  assert.deepEqual(client.runs, ['one task']);
});

test('Enter during a run submits steering to the active run instead of starting another task', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'first task');
  await enter(stdin, '请先修复失败的测试');
  assert.deepEqual(client.steers, [{target: 'run-1', text: '请先修复失败的测试'}]);
  assert.deepEqual(client.runs, ['first task']);
  assert.match(lastFrame() ?? '', /纠偏已发送，等待后端确认/);
  client.emit({type: 'ack', id: 'steer-1', action: 'steer'});
  await pause(20);
  assert.match(lastFrame() ?? '', /纠偏已提交，将在下一次模型请求生效/);
  client.emit(completed('run-1'));
  await pause(20);
  assert.deepEqual(client.runs, ['first task']);
  assert.match(lastFrame() ?? '', /本轮结束/);
});

test('steering during a backend command is refused instead of sending a command id', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, '/diff');
  assert.deepEqual(client.commands, ['diff']);
  await enter(stdin, '只改 tests');
  assert.deepEqual(client.steers, []);
  assert.match(lastFrame() ?? '', /命令执行中，结束后再发送/);
  assert.match(lastFrame() ?? '', /只改 tests/);
});

test('steering failure shows a notice and is not reported as a run error', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'first task');
  await enter(stdin, '请先修复失败的测试');
  client.emit({type: 'error', id: 'steer-1', code: 'run_mismatch', message: 'target is not accepting steering'});
  await pause(20);
  assert.match(lastFrame() ?? '', /纠偏未送达：run_mismatch/);
  assert.doesNotMatch(lastFrame() ?? '', /运行异常/);
  client.emit(completed('run-1'));
  await pause(20);
  assert.match(lastFrame() ?? '', /本轮结束/);
});

test('late cancel error after the run finished does not mark the run as failed', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'first task');
  stdin.write('\u0003');
  await pause(20);
  assert.deepEqual(client.cancellations, ['run-1']);
  client.emit(completed('run-1', 'cancelled'));
  await pause(20);
  client.emit({type: 'error', id: 'cancel-1', code: 'run_mismatch', message: 'target is not the active run'});
  await pause(20);
  assert.match(lastFrame() ?? '', /停止请求未生效：run_mismatch/);
  assert.doesNotMatch(lastFrame() ?? '', /运行异常/);
  assert.match(lastFrame() ?? '', /已取消/);
});

test('cancelling a run drops queued follow-ups with a visible notice', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'first task');
  await enter(stdin, '/followup 迟到的后续');
  stdin.write('\u0003');
  await pause(20);
  client.emit(completed('run-1', 'cancelled'));
  await pause(20);
  assert.match(lastFrame() ?? '', /已取消，1 条排队消息已丢弃/);
  assert.deepEqual(client.runs, ['first task']);
  await enter(stdin, 'second task');
  await pause(20);
  assert.deepEqual(client.runs, ['first task', 'second task']);
});

test('automatic follow-up start keeps the draft the user is still editing', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'first task');
  await enter(stdin, '/followup 下一轮');
  stdin.write('TODO: 只改 tests');
  await pause(20);
  client.emit(completed('run-1'));
  await pause(25);
  assert.deepEqual(client.runs, ['first task', '下一轮']);
  assert.match(lastFrame() ?? '', /TODO: 只改 tests/);
});

test('/followup during a backend command is refused instead of being queued', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, '/diff');
  assert.deepEqual(client.commands, ['diff']);
  await enter(stdin, '/followup 迟到的后续');
  assert.match(lastFrame() ?? '', /命令执行中，结束后再发送/);
  assert.deepEqual(client.runs, []);
});

test('/followup queues messages while running and starts them in FIFO order after each done', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'first task');
  await enter(stdin, '/followup 第二个任务');
  await enter(stdin, '/followup 第三个任务');
  assert.deepEqual(client.runs, ['first task']);
  assert.match(lastFrame() ?? '', /已排队为后续消息：第二个任务/);
  assert.match(lastFrame() ?? '', /已排队为后续消息：第三个任务/);
  client.emit(completed('run-1'));
  await pause(20);
  assert.deepEqual(client.runs, ['first task', '第二个任务']);
  client.emit(completed('run-2'));
  await pause(20);
  assert.deepEqual(client.runs, ['first task', '第二个任务', '第三个任务']);
  client.emit(completed('run-3'));
  await pause(20);
  assert.deepEqual(client.runs, ['first task', '第二个任务', '第三个任务']);
});

test('/followup submits the message directly when the session is idle', async () => {
  const {client, stdin} = await mount();
  await enter(stdin, '/followup 空闲时直接执行');
  assert.deepEqual(client.runs, ['空闲时直接执行']);
  assert.deepEqual(client.steers, []);
});

test('queued follow-ups never start after the connection closes', async () => {
  const {client, stdin} = await mount();
  await enter(stdin, 'first task');
  await enter(stdin, '/followup 排队消息');
  client.emit({type: 'closed'});
  await pause(20);
  client.emit(completed('run-1'));
  await pause(20);
  assert.deepEqual(client.runs, ['first task']);
});

test('queued follow-ups never start while quitting', async () => {
  const {client, stdin} = await mount();
  await enter(stdin, 'first task');
  await enter(stdin, '/followup 排队消息');
  stdin.write('\u0011');
  await pause(25);
  client.emit(completed('run-1'));
  await pause(20);
  assert.deepEqual(client.runs, ['first task']);
  assert.equal(client.shutdowns, 1);
});

test('footer shows one outcome label for finished, cancelled and stopped runs', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'first task');
  client.emit(completed('run-1'));
  await pause(20);
  const doneFrame = lastFrame() ?? '';
  assert.match(doneFrame, /本轮结束/);
  assert.doesNotMatch(doneFrame, /本轮结束 · 本轮结束/);

  await enter(stdin, 'second task');
  client.emit(completed('run-2', 'cancelled'));
  await pause(20);
  const cancelledFrame = lastFrame() ?? '';
  assert.match(cancelledFrame, /已取消/);
  assert.doesNotMatch(cancelledFrame, /已取消 · 已取消/);

  await enter(stdin, 'third task');
  client.emit({type: 'done', id: 'run-3', result: {text: '', steps: 1, status: 'stopped', usage: {}, cost_usd: 0, run_id: 'run-3', stop_reason: 'token_budget'}});
  await pause(20);
  const stoppedFrame = lastFrame() ?? '';
  assert.match(stoppedFrame, /token_budget/);
  assert.doesNotMatch(stoppedFrame, /已停止 · 已停止/);
});

test('Ctrl+Y copies the latest assistant reply', async () => {
  const copies: string[] = [];
  const {client, stdin, lastFrame} = await mount(text => {copies.push(text); return 'pbcopy';});
  await enter(stdin, 'first task');
  client.emit({type: 'done', id: 'run-1', result: {text: '最终回答：已修复。', steps: 1, status: 'done', usage: {}, cost_usd: 0, run_id: 'run-1', stop_reason: ''}});
  await pause(20);
  stdin.write('\u0019');
  await pause(25);
  assert.deepEqual(copies, ['最终回答：已修复。']);
  assert.match(lastFrame() ?? '', /已复制最近回复：\d+ 字符（pbcopy）/);
});

test('/copy copies the latest assistant reply too', async () => {
  const copies: string[] = [];
  const {client, stdin, lastFrame} = await mount(text => {copies.push(text); return 'osc52';});
  await enter(stdin, 'first task');
  client.emit({type: 'done', id: 'run-1', result: {text: '可复制的回答', steps: 1, status: 'done', usage: {}, cost_usd: 0, run_id: 'run-1', stop_reason: ''}});
  await pause(20);
  await enter(stdin, '/copy');
  assert.deepEqual(copies, ['可复制的回答']);
  assert.match(lastFrame() ?? '', /已复制最近回复/);
});

test('copy without content explains instead of failing silently', async () => {
  const copies: string[] = [];
  const {stdin, lastFrame} = await mount(text => {copies.push(text); return 'pbcopy';});
  await enter(stdin, '/copy');
  assert.deepEqual(copies, []);
  assert.match(lastFrame() ?? '', /没有可复制的 Agent 回复/);
});

test('copying in a tool detail view includes arguments and output', async () => {
  const copies: string[] = [];
  const state: ViewState = {...initialState, phase: 'idle', entries: [
    {id: 0, kind: 'task', text: 'work'},
    {id: 1, kind: 'tool', text: '工具 › read_file', call: {id: 't1', name: 'read_file', args: {path: 'a.txt'}}, output: 'hello', ok: true, pending: false},
  ], nextEntry: 2};
  const {stdin, lastFrame} = await mount(text => {copies.push(text); return 'pbcopy';}, state);
  stdin.write('\u000f');
  await pause(20);
  stdin.write('\u0019');
  await pause(25);
  assert.equal(copies.length, 1);
  assert.match(copies[0] ?? '', /read_file/);
  assert.match(copies[0] ?? '', /"path": "a.txt"/);
  assert.match(copies[0] ?? '', /hello/);
  assert.match(lastFrame() ?? '', /已复制工具详情/);
});

test('mouse drag selects text and copies it on release', async () => {
  const copies: string[] = [];
  const state: ViewState = {...initialState, phase: 'idle', entries: [{id: 0, kind: 'assistant', text: 'hello world'}], nextEntry: 1};
  const {stdin, lastFrame} = await mount(text => {copies.push(text); return 'pbcopy';}, state, {columns: 80, rows: 24});
  stdin.write('\u001b[<0;5;10M');
  await pause(10);
  stdin.write('\u001b[<32;10;10M');
  await pause(10);
  stdin.write('\u001b[<0;10;10m');
  await pause(25);
  assert.deepEqual(copies, ['hello']);
  assert.match(lastFrame() ?? '', /已复制所选文本：5 字符（pbcopy）/);
});

test('shows the stopping request and cancelled stages', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'first task');
  stdin.write('\u0003');
  await pause(20);
  assert.match(lastFrame() ?? '', /停止请求已发送，等待后端确认/);
  client.emit({type: 'ack', id: 'cancel-1', action: 'cancel'});
  await pause(20);
  assert.match(lastFrame() ?? '', /后端已确认停止请求，等待收尾/);
  client.emit(completed('run-1', 'cancelled'));
  await pause(20);
  assert.match(lastFrame() ?? '', /已取消：已发生的修改仍需核对/);
});

test('manages the project background from the workbench', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'profile-app-'));
  const file = path.join(directory, '.vibe', 'project-profile.json');
  try {
    const {stdin, lastFrame} = await mount(undefined, undefined, {columns: 100, rows: 30}, file);
    await enter(stdin, '/project');
    await pause(20);
    assert.match(lastFrame() ?? '', /项目背景为空/);

    await enter(stdin, '/project add Ink 是唯一界面入口');
    await pause(20);
    assert.match(lastFrame() ?? '', /已添加项目背景/);
    const payload = JSON.parse(await readFile(file, 'utf8')) as {entries: {text: string; source: string}[]};
    assert.deepEqual(payload.entries.map(entry => entry.text), ['Ink 是唯一界面入口']);
    assert.equal(payload.entries[0]?.source, 'user');

    await enter(stdin, '/project source 1 AGENTS.md 与 roadmap.md');
    await pause(20);
    const spaced = JSON.parse(await readFile(file, 'utf8')) as {entries: {source: string}[]};
    assert.equal(spaced.entries[0]?.source, 'AGENTS.md 与 roadmap.md');

    await enter(stdin, '/project source 1 docs/roadmap.md');
    await pause(20);
    assert.match(lastFrame() ?? '', /已更新项目背景来源：docs\/roadmap\.md/);
    const sourced = JSON.parse(await readFile(file, 'utf8')) as {entries: {source: string}[]};
    assert.equal(sourced.entries[0]?.source, 'docs/roadmap.md');

    await enter(stdin, '/project source 9 docs/roadmap.md');
    await pause(20);
    assert.match(lastFrame() ?? '', /用法：\/project source <1-1> <来源>/);

    await enter(stdin, '/project source 1');
    await pause(20);
    assert.match(lastFrame() ?? '', /用法：\/project source <1-1> <来源>/);

    await enter(stdin, '/project');
    await pause(20);
    assert.match(lastFrame() ?? '', /1\. Ink 是唯一界面入口（来源：docs\/roadmap\.md）/);

    await enter(stdin, '/project remove 1');
    await pause(20);
    assert.match(lastFrame() ?? '', /已删除项目背景/);
    assert.deepEqual((JSON.parse(await readFile(file, 'utf8')) as {entries: unknown[]}).entries, []);
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});

test('manages user preferences from the workbench', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'user-profile-app-'));
  const file = path.join(directory, 'user-profile.json');
  type StoredProfile = {enabled: boolean; entries: {text: string; status: string; source: string}[]};
  const stored = async () => JSON.parse(await readFile(file, 'utf8')) as unknown as StoredProfile;
  try {
    const {stdin, lastFrame} = await mount(undefined, undefined, {columns: 100, rows: 30}, undefined, file);
    await enter(stdin, '/about');
    await pause(20);
    assert.match(lastFrame() ?? '', /暂无条目/);

    await enter(stdin, '/about add 回答先给结论');
    await pause(20);
    await enter(stdin, '/about propose 可能偏好简短回复');
    await pause(20);
    let payload = await stored();
    assert.equal(payload.entries.length, 2);
    assert.deepEqual(payload.entries.map(entry => entry.status), ['confirmed', 'proposed']);

    await enter(stdin, '/about');
    await pause(20);
    assert.match(lastFrame() ?? '', /1\. \[已确认\] 回答先给结论/);
    assert.match(lastFrame() ?? '', /2\. \[待确认\] 可能偏好简短回复/);

    await enter(stdin, '/about confirm 2');
    await pause(20);
    payload = await stored();
    assert.equal(payload.entries[1]?.status, 'confirmed');

    await enter(stdin, '/about off');
    await pause(20);
    assert.match(lastFrame() ?? '', /个人偏好已停用/);
    payload = await stored();
    assert.equal(payload.enabled, false);

    await enter(stdin, '/about remove 1');
    await pause(20);
    payload = await stored();
    assert.equal(payload.entries.length, 1);
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});

test('/sidebar opens the right-hand Git sidebar through the injected launcher', async () => {
  const calls: string[] = [];
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}
    initialState={{...initialState, phase: 'idle', workspace: '/repo', model: 'fake'}}
    launchSidebar={workspace => {calls.push(workspace); return {outcome: 'started', message: '已打开右侧 Git 变更侧栏'};}}/>);
  await pause(20);
  await enter(stdin, '/sidebar');
  assert.deepEqual(calls, ['/repo']);
  assert.match(lastFrame() ?? '', /已打开右侧 Git 变更侧栏/u);
});

test('Ctrl+G also opens the Git sidebar', async () => {
  const calls: string[] = [];
  const client = new MockClient();
  const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}
    initialState={{...initialState, phase: 'idle', workspace: '/repo', model: 'fake'}}
    launchSidebar={workspace => {calls.push(workspace); return {outcome: 'toggled', message: '已切换右侧 Git 变更侧栏'};}}/>);
  await pause(20);
  stdin.write('\u0007');
  await pause(20);
  assert.deepEqual(calls, ['/repo']);
});

test('/sidebar explains when no workspace is connected yet', async () => {
  const calls: string[] = [];
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}
    initialState={{...initialState, phase: 'idle'}}
    launchSidebar={workspace => {calls.push(workspace); return {outcome: 'started', message: 'unexpected'};}}/>);
  await pause(20);
  await enter(stdin, '/sidebar');
  assert.deepEqual(calls, []);
  assert.match(lastFrame() ?? '', /尚未连接工作区/u);
});

test('unsupported sidebar hosts keep the launch reason and manual command visible', async () => {
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}
    initialState={{...initialState, phase: 'idle', workspace: '/repo', model: 'fake'}}
    launchSidebar={() => ({outcome: 'unsupported', message: '当前终端不支持自动打开侧栏；请手动运行：cd /repo && node sidebar.js'} as const)}/>);
  await pause(20);
  await enter(stdin, '/sidebar');
  assert.match(lastFrame() ?? '', /当前终端不支持自动打开侧栏/u);
  assert.match(lastFrame() ?? '', /请手动运行/u);
});

test('/sidebar setup writes the Ghostty sidebar configuration through the injected command', async () => {
  let calls = 0;
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}
    initialState={{...initialState, phase: 'idle'}}
    setupSidebar={() => {calls += 1; return {ok: true, message: '已写入 Ghostty 配置；请完全重启 Ghostty'};}}/>);
  await pause(20);
  await enter(stdin, '/sidebar setup');
  assert.equal(calls, 1);
  assert.match(lastFrame() ?? '', /已写入 Ghostty 配置/u);
});

test('/sidebar split asks the launcher for split mode so the pane follows the Ghostty window', async () => {
  const modes: (string | undefined)[] = [];
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 140, rows: 40}}
    initialState={{...initialState, phase: 'idle', workspace: '/repo', model: 'fake'}}
    launchSidebar={(workspace, mode) => {modes.push(mode); return {outcome: 'started', message: `已打开 ${workspace} 的 split 侧栏`};}}/>);
  await pause(20);
  await enter(stdin, '/sidebar split');
  assert.deepEqual(modes, ['split']);
  assert.match(lastFrame() ?? '', /split 侧栏/u);
});

test('/sidebar defaults to split mode while /sidebar quick requests the screen-anchored quick terminal', async () => {
  const modes: (string | undefined)[] = [];
  const totals: (number | undefined)[] = [];
  const client = new MockClient();
  const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 140, rows: 40}}
    initialState={{...initialState, phase: 'idle', workspace: '/repo', model: 'fake'}}
    launchSidebar={(_workspace, mode, totalColumns) => {modes.push(mode); totals.push(totalColumns); return {outcome: 'started', message: 'ok'};}}/>);
  await pause(20);
  await enter(stdin, '/sidebar');
  await pause(20);
  await enter(stdin, '/sidebar quick');
  await pause(20);
  await enter(stdin, '/sidebar window');
  await pause(20);
  assert.deepEqual(modes, ['split', 'quick', 'window']);
  assert.deepEqual(totals, [140, 140, 140]);
});

test('the TUI publishes agent-touched files so the sidebar can refresh and mark them', async () => {
  const published: {workspace: string; files: string[]}[] = [];
  const client = new MockClient();
  const patch = ['diff --git a/src/a.ts b/src/a.ts', '--- a/src/a.ts', '+++ b/src/a.ts', '@@ -1 +1 @@', '-old', '+new'].join('\n');
  render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}
    initialState={{...initialState, phase: 'idle', workspace: '/repo', model: 'fake', entries: [
      {id: 0, kind: 'tool', text: 'edited', call: {id: 'c1', name: 'edit_file', args: {path: 'tui/src/app.tsx'}}, ok: true, output: 'edited', runId: 'run-1'},
      {id: 1, kind: 'tool', text: 'patched', call: {id: 'c2', name: 'apply_patch', args: {patch}}, ok: true, output: 'applied', runId: 'run-1'},
    ]}}
    publishSidebarSignal={(workspace, signal) => {published.push({workspace, files: signal.files});}}/>);
  await pause(40);
  const latest = published.at(-1);
  assert.equal(latest?.workspace, '/repo');
  assert.ok(latest?.files.includes('tui/src/app.tsx'));
  assert.ok(latest?.files.includes('src/a.ts'));
});

test('typing /skills opens a scrollable menu and Tab completes a skill action', async () => {
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 100, rows: 30}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 's', capabilities: [], skills: [
    {name: 'ponytail', summary: '精简实现', active: false},
    {name: 'tdd', summary: '先写测试', active: false},
  ]});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  stdin.write('/skills');
  await pause(25);
  assert.match(lastFrame() ?? '', /\/skills activate ponytail/);
  assert.match(lastFrame() ?? '', /↑↓ 选择 · Tab 填充/);
  stdin.write('\u001b[B');
  await pause(15);
  stdin.write('\t');
  await pause(20);
  stdin.write('\r');
  await pause(30);
  assert.deepEqual(client.commands, ['skills']);
  assert.deepEqual(client.commandArgs, [{action: 'activate', name: 'tdd'}]);
});
