import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import {App} from '../src/app.js';
import type {BackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';
import {initialState} from '../src/view-state.js';
import stringWidth from 'string-width';
import {contextSnapshot} from './context-fixture.js';

class MockClient {
  listeners = new Set<(event: ServerEvent) => void>();
  runs: string[] = [];
  cancellations: string[] = [];
  approvals: [string, 'once' | 'session' | 'workspace' | 'deny'][] = [];
  commands: string[] = [];
  shutdowns = 0;
  sendError = false;

  subscribe(listener: (event: ServerEvent) => void) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
  run(text: string) {this.runs.push(text); return `run-${this.runs.length}`;}
  cancel(id: string) {if (this.sendError) throw new Error('backend disconnected'); this.cancellations.push(id);}
  approve(id: string, decision: 'once' | 'session' | 'workspace' | 'deny') {if (this.sendError) throw new Error('backend disconnected'); this.approvals.push([id, decision]);}
  command(command: string) {if (this.sendError) throw new Error('backend disconnected'); this.commands.push(command); return `command-${this.commands.length}`;}
  async shutdown() {this.shutdowns += 1;}
  emit(event: Record<string, unknown>) {
    for (const listener of this.listeners) listener({v: 1, id: null, ...event} as ServerEvent);
  }
}

afterEach(cleanup);

async function mount() {
  const client = new MockClient();
  const view = render(<App client={client as unknown as BackendClient}/>);
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
  await enter(stdin, '下一条草稿');
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

test('busy editor keeps a draft for the next task without sending it early', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'first');
  await enter(stdin, 'next draft');
  assert.deepEqual(client.runs, ['first']);
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
