import assert from 'node:assert/strict';
import {mkdir, mkdtemp, readFile, readdir, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import {App} from '../src/app.js';
import type {BackendClient} from '../src/backend.js';
import type {RunTaskMeta, ServerEvent} from '../src/protocol.js';
import {initialState, type ViewState} from '../src/view-state.js';
import stringWidth from 'string-width';
import {contextSnapshot, loadedContextSnapshot} from './context-fixture.js';
import {readSidebarSignal, writeSidebarSignal, type SidebarSignalInput} from '../src/sidebar/signal.js';
import {validOwner} from '../src/sidebar/channel.js';

class MockClient {
  listeners = new Set<(event: ServerEvent) => void>();
  runs: string[] = [];
  runTasks: (RunTaskMeta | undefined)[] = [];
  capabilityList: string[] = [];
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
  run(text: string, task?: RunTaskMeta) {this.runs.push(text); this.runTasks.push(task); return `run-${this.runs.length}`;}
  capabilities() {return this.capabilityList;}
  cancel(id: string) {if (this.sendError) throw new Error('backend disconnected'); this.cancellations.push(id); return `cancel-${this.cancellations.length}`;}
  steer(target: string, text: string) {if (this.sendError) throw new Error('backend disconnected'); this.steers.push({target, text}); return `steer-${this.steers.length}`;}
  approve(id: string, decision: 'once' | 'session' | 'workspace' | 'deny') {if (this.sendError) throw new Error('backend disconnected'); this.approvals.push([id, decision]); return `approve-${this.approvals.length}`;}
  command(command: string, args?: Record<string, unknown>) {if (this.sendError) throw new Error('backend disconnected'); this.commands.push(command); this.commandArgs.push(args); return `command-${this.commands.length}`;}
  async shutdown() {this.shutdowns += 1;}
  emit(event: Record<string, unknown>) {
    for (const listener of this.listeners) listener({v: 1, id: null, ...event} as ServerEvent);
  }
}

afterEach(cleanup);

const runningTask = (mode: string) => ({task_id: 't-one', kind: 'run', status: 'running', mode, segment: 1,
  objective: '修复标题', goal_id: null, updated_at: ''});

test('clicking an earlier result selects its evidence without changing the active run or Chinese draft', async () => {
  const client = new MockClient(); const signals: SidebarSignalInput[] = []; let launches = 0;
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 100, rows: 40}}
    publishSidebarSignal={(_root, signal) => signals.push(signal)}
    launchSidebar={() => {launches++; return {outcome: 'started', message: '已打开绑定侧栏'};}}/>);
  await pause(20); client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'}); await pause(20);
  await enter(view.stdin, '检查旧结果');
  client.emit({type: 'tool_result', id: 'run-1', call: {id: 'check', name: 'bash', args: {command: 'old-test', verification: true}},
    ok: true, output: 'old failure', execution: {status: 'failed', exit_code: 2}});
  client.emit({type: 'done', id: 'run-1', result: {text: '旧结果未通过', status: 'done', steps: 1, usage: {}, cost_usd: 0,
    run_id: 'trace-old', stop_reason: '', verified: false}});
  await pause(35); await enter(view.stdin, '继续新任务');
  view.stdin.write('中文草稿'); await pause(25); view.stdin.write('\u001b[D'); await pause(20);
  await clickButton(view.stdin, view.lastFrame()!, '验证');
  assert.equal(launches, 1);
  const selectedDeadline = Date.now() + 1500;
  while (signals.at(-1)?.run?.requestId !== 'run-1' && Date.now() < selectedDeadline) await pause(10);
  assert.equal(signals.at(-1)?.run?.requestId, 'run-1');
  assert.equal(signals.at(-1)?.verification?.commands[0]?.execution?.exit_code, 2);
  assert.match(view.lastFrame()!, /中文草稿/);
  view.stdin.write('\u000f'); await pause(25);
  assert.doesNotMatch(view.lastFrame()!, /\[ 回到当前 \]/);
  view.stdin.write('\u001b'); await pause(25);
  view.stdin.write('中'); await pause(20); assert.match(view.lastFrame()!, /中文草中稿/);
  view.stdin.write('\u0003'); await pause(20); assert.deepEqual(client.cancellations, ['run-2']);
  await clickButton(view.stdin, view.lastFrame()!, '回到当前');
  const currentDeadline = Date.now() + 1500;
  while (signals.at(-1)?.run?.requestId !== 'run-2' && Date.now() < currentDeadline) await pause(10);
  assert.equal(signals.at(-1)?.run?.requestId, 'run-2');
  assert.deepEqual(signals.at(-1)?.verification?.commands, []);
  assert.deepEqual(client.approvals, []); assert.deepEqual(client.commands, []);
});

test('an already selected result receives its own late plan receipt without adopting another result', async () => {
  const client = new MockClient(); client.capabilityList = ['plan-review'];
  const signals: SidebarSignalInput[] = [];
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 100, rows: 40}}
    publishSidebarSignal={(_root, signal) => signals.push(signal)}
    launchSidebar={() => ({outcome: 'started', message: '已打开绑定侧栏'})}/>);
  await pause(20); client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: ['plan-review']});
  client.emit({type: 'status', phase: 'idle'}); await pause(20);
  await enter(view.stdin, '生成计划');
  client.emit({type: 'task_state', id: 'run-1', task: {...runningTask('plan'), task_id: 't-plan'}});
  client.emit({type: 'done', id: 'run-1', result: {text: '计划已提交', status: 'done', steps: 1, usage: {}, cost_usd: 0,
    run_id: 'trace-plan', stop_reason: 'plan_submitted'}});
  await pause(35); await clickButton(view.stdin, view.lastFrame()!, '计划');
  const selectedDeadline = Date.now() + 1500;
  while (signals.at(-1)?.navigation?.mode !== 'result' && Date.now() < selectedDeadline) await pause(10);
  assert.equal(signals.at(-1)?.navigation?.mode, 'result');
  assert.equal(signals.at(-1)?.plan, undefined);
  const navigation = signals.at(-1)?.navigation;
  const target = {session_id: 'test', plan_task_id: 't-plan', plan_id: 'p1', plan_version: 1, plan_hash: 'a'.repeat(64)};
  const receipt = {type: 'plan_review', outcome: 'ready', target, replacement_target: null, code: null, message: '计划已生成',
    launch: null, body_page: '', next_cursor: null, available_actions: ['approve-execute'],
    summary: {objective: '原计划', status: 'proposed', executable: true, step_count: 1, criterion_count: 0, question_count: 0}};
  client.emit({...receipt, id: 'command-1'});
  const receiptDeadline = Date.now() + 1500;
  while (!signals.at(-1)?.plan && Date.now() < receiptDeadline) await pause(10);
  assert.deepEqual(signals.at(-1)?.plan?.target, target);
  assert.deepEqual(signals.at(-1)?.navigation, navigation);
  await enter(view.stdin, '/plan');
  client.emit({...receipt, id: 'command-2', target: {...target, plan_task_id: 'another-task', plan_id: 'another-plan'}});
  await pause(30);
  assert.deepEqual(signals.at(-1)?.plan?.target, target);
  assert.equal(signals.at(-1)?.run?.requestId, 'run-1');
});

test('sidebar verification preserves command facts rather than inferring success from output text', async () => {
  const client = new MockClient();
  const signals: SidebarSignalInput[] = [];
  const view = render(<App client={client as unknown as BackendClient} publishSidebarSignal={(_workspace, signal) => signals.push(signal)}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'}); await pause(20);
  await enter(view.stdin, '检查修改');
  client.emit({type: 'tool_result', id: 'run-1', call: {id: 'test-1', name: 'bash', args: {command: 'npm test', verification: true}},
    ok: true, output: 'ALL TESTS PASSED', execution: {status: 'failed', exit_code: 2}});
  client.emit({type: 'done', id: 'run-1', result: {text: '完成', status: 'stopped', steps: 1, usage: {}, cost_usd: 0,
    run_id: 'trace-check', stop_reason: 'verification_failed', verified: false,
    completion: {allowed: false, codes: ['verification_failed'], messages: ['验证命令未通过']}}});
  const deadline = Date.now() + 1500;
  while (signals.at(-1)?.run?.status !== 'finished' && Date.now() < deadline) await pause(10);
  assert.deepEqual(JSON.parse(JSON.stringify(signals.at(-1))).verification, {
    requestId: 'run-1', status: 'stopped', stopReason: 'verification_failed', verified: false,
    completionAllowed: false, completionMessages: ['验证命令未通过'], omittedCommands: 0,
    commands: [{id: 'test-1', command: 'npm test', verification: true, pending: false, ok: true,
      execution: {status: 'failed', exit_code: 2}, output: 'ALL TESTS PASSED', truncated: false}],
  });
  await enter(view.stdin, '下一任务');
  const next = JSON.parse(JSON.stringify(signals.at(-1))).verification;
  assert.equal(next.requestId, 'run-2'); assert.equal(next.verified, null);
  assert.deepEqual(next.commands, []);
  client.emit({type: 'tool_result', id: 'run-2', call: {id: 'long', name: 'bash', args: {command: 'echo ' + '中'.repeat(1100)}},
    ok: true, output: '文'.repeat(2500), execution: {status: 'succeeded', exit_code: 0}});
  const outputDeadline = Date.now() + 1500;
  while (signals.at(-1)?.verification?.commands[0]?.id !== 'long' && Date.now() < outputDeadline) await pause(10);
  const clipped = JSON.parse(JSON.stringify(signals.at(-1))).verification.commands[0];
  assert.match(clipped.command, /已截断/);
  assert.ok(clipped.command.length <= 1000);
  assert.equal(clipped.output.length, 2000); assert.equal(clipped.truncated, true);
});

test('sidebar scope follows the accepted task segment and clears prior files before the next task writes', async () => {
  const client = new MockClient();
  const signals: SidebarSignalInput[] = [];
  const view = render(<App client={client as unknown as BackendClient} publishSidebarSignal={(_workspace, signal) => signals.push(signal)}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'}); await pause(20);
  await enter(view.stdin, '修改第一个文件');
  client.emit({type: 'task_state', id: 'run-1', task: {task_id: 't-first', kind: 'run', status: 'running', segment: 2, objective: '修改第一个文件', goal_id: null, updated_at: 'now'}});
  client.emit({type: 'tool_result', id: 'run-1', call: {id: 'c1', name: 'write_file', args: {path: 'first.ts'}}, ok: true, output: 'written'});
  client.emit({type: 'done', id: 'run-1', result: {text: '完成', status: 'done', steps: 1, usage: {}, cost_usd: 0, run_id: 'trace-first', stop_reason: ''}});
  const deadline = Date.now() + 1500;
  while (signals.at(-1)?.run?.status !== 'finished' && Date.now() < deadline) await pause(10);
  assert.deepEqual(signals.at(-1)?.files, ['first.ts'], JSON.stringify(signals.at(-1)));
  assert.deepEqual(signals.at(-1)?.run, {requestId: 'run-1', traceRunId: 'trace-first', taskId: 't-first', segment: 2,
    taskStatus: 'running', status: 'finished', objective: '修改第一个文件'});
  await enter(view.stdin, '第二个只读任务');
  assert.deepEqual(signals.at(-1)?.files, [], 'a new task must not retain previous writes before any new tool result');
  assert.equal(signals.at(-1)?.run?.requestId, 'run-2');
  assert.equal(signals.at(-1)?.run?.taskId, null);
  client.emit({type: 'task_state', id: 'run-1', task: {task_id: 't-first', kind: 'run', status: 'completed', segment: 2, objective: 'old', goal_id: null, updated_at: 'later'}});
  await pause(20);
  assert.equal(signals.at(-1)?.run?.taskId, null, 'a delayed task state cannot bind a new request');
  view.unmount();
});

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

async function approve(stdin: {write(value: string): void}, key: string) {
  stdin.write('\u0012');
  await pause(20);
  stdin.write(key);
  await pause(20);
  stdin.write('\r');
  await pause(20);
}

function buttonPoint(frame: string, label: string) {
  const lines = frame.split('\n');
  const row = lines.findIndex(line => line.includes(`[ ${label} ]`) || line.includes(`› ${label} ‹`));
  assert.ok(row >= 0, `missing button ${label}\n${frame}`);
  return {row: row + 1, column: stringWidth(lines[row]!.slice(0, lines[row]!.indexOf(label))) + 1};
}

async function clickButton(stdin: {write(value: string): void}, frame: string, label: string) {
  const {row, column} = buttonPoint(frame, label);
  stdin.write(`\u001b[<0;${column};${row}M`);
  stdin.write(`\u001b[<0;${column};${row}m`);
  await pause(25);
}

test('clicking the visible approval button sends once and preserves the Chinese draft and cursor', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  await enter(stdin, 'write file');
  stdin.write('中文草稿');
  await pause(20);
  stdin.write('\u001b[D');
  await pause(20);
  client.emit({type: 'approval_request', id: 'run-1', approval_id: 'click-once',
    call: {id: 'tool', name: 'write_file', args: {}}, preview: 'write a.txt',
    decisions: ['once', 'session', 'workspace', 'deny']});
  await pause(25);
  const frame = lastFrame() ?? '';
  assert.match(frame, /更多授权/);
  assert.doesNotMatch(frame, /精确规则|工作区授权/);
  await clickButton(stdin, frame, '允许本次写入');
  await clickButton(stdin, frame, '允许本次写入');
  assert.deepEqual(client.approvals, [['click-once', 'once']]);
  assert.match(lastFrame() ?? '', /审批已发送.*等待后端确认/);
  stdin.write('保留');
  await pause(20);
  assert.match(lastFrame() ?? '', /中文草保留稿/);
});

test('Tab enters visible approval actions without completing a slash draft and arrows choose denial', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  await enter(stdin, 'work');
  stdin.write('/autono');
  await pause(20);
  client.emit({type: 'approval_request', id: 'run-1', approval_id: 'tab-deny',
    call: {id: 'tool', name: 'bash', args: {}}, preview: 'python check.py', decisions: ['once', 'deny']});
  await pause(20);
  stdin.write('\t');
  await pause(20);
  assert.match(lastFrame() ?? '', /› 允许本次命令 ‹/);
  assert.doesNotMatch(lastFrame() ?? '', /\/autonomy/);
  assert.deepEqual(client.approvals, []);
  stdin.write('\u001b[C');
  await pause(20);
  assert.match(lastFrame() ?? '', /› 拒绝 ‹/);
  stdin.write('\r');
  await pause(20);
  assert.deepEqual(client.approvals, [['tab-deny', 'deny']]);
  assert.match(lastFrame() ?? '', /\/autono/);
});

for (const [columns, rows] of [[32, 12], [80, 24], [120, 40]]) {
  test(`clickable approval scopes fit ${columns}x${rows} and denial needs no keyboard focus`, async () => {
    const client = new MockClient();
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns, rows}}
      initialState={{...initialState, phase: 'waiting_approval', activeId: 'run-1', approval: {
        id: 'scopes', tool: 'write_file', call: {id: 'tool', name: 'write_file', args: {}}, preview: '中文路径/file.txt',
        decisions: ['once', 'session', 'workspace', 'deny']}}}/>);
    await pause(25);
    await clickButton(stdin, lastFrame() ?? '', columns < 60 ? '更多' : '更多授权…');
    assert.deepEqual(client.approvals, []);
    const expanded = lastFrame() ?? '';
    assert.match(expanded, /精确规则/);
    buttonPoint(expanded, columns < 60 ? '规则' : '记住精确规则');
    buttonPoint(expanded, columns < 60 ? '工作区' : '授权此工作区');
    assert.ok(expanded.split('\n').length <= rows);
    assert.ok(expanded.split('\n').every(line => stringWidth(line) <= columns));
    assert.match(expanded, /Ctrl\+C/);
    await clickButton(stdin, expanded, '返回');
    await clickButton(stdin, lastFrame() ?? '', '拒绝');
    assert.deepEqual(client.approvals, [['scopes', 'deny']]);
  });
}

test('a newly rendered denial button never grants the previous expanded authorization scope', async () => {
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}
    initialState={{...initialState, phase: 'waiting_approval', activeId: 'run-1', approval: {
      id: 'rapid-scope', tool: 'write_file', call: {id: 'tool', name: 'write_file', args: {}}, preview: 'file',
      decisions: ['once', 'session', 'workspace', 'deny']}}}/>);
  const click = async (label: string) => {
    const deadline = Date.now() + 1500;
    while (!lastFrame()?.includes(`[ ${label} ]`) && Date.now() < deadline) await pause(0);
    const {column, row} = buttonPoint(lastFrame()!, label);
    stdin.write(`\u001b[<0;${column};${row}M\u001b[<0;${column};${row}m`);
  };
  await click('更多授权…'); await click('返回'); await click('拒绝');
  const deadline = Date.now() + 1500;
  while (!client.approvals.length && Date.now() < deadline) await pause(5);
  assert.deepEqual(client.approvals, [['rapid-scope', 'deny']]);
});

test('more authorization shows only backend scopes and clicking the rule grants that scope once', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  client.emit({type: 'status', id: 'startup', phase: 'starting'});
  client.emit({type: 'approval_request', id: 'startup', approval_id: 'mcp-buttons',
    call: {id: 'tool', name: 'mcp_start', args: {}}, preview: '启动本地服务', decisions: ['once', 'session', 'deny']});
  await pause(25);
  await clickButton(stdin, lastFrame() ?? '', '更多授权…');
  assert.match(lastFrame() ?? '', /本进程内记住同一工作区/);
  assert.doesNotMatch(lastFrame() ?? '', /授权此工作区/);
  const frame = lastFrame() ?? '';
  await clickButton(stdin, frame, '记住精确规则');
  await clickButton(stdin, frame, '记住精确规则');
  assert.deepEqual(client.approvals, [['mcp-buttons', 'session']]);
});

test('Tab and Shift+Tab return from approval actions to the same draft without sending', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  await enter(stdin, 'work');
  stdin.write('草稿');
  await pause(20);
  client.emit({type: 'approval_request', id: 'run-1', approval_id: 'focus-cycle',
    call: {id: 'tool', name: 'bash', args: {}}, preview: 'check', decisions: ['once', 'deny']});
  await pause(20);
  for (let i = 0; i < 3; i++) {stdin.write('\t'); await pause(20);}
  stdin.write('继续');
  await pause(20);
  assert.match(lastFrame() ?? '', /草稿继续/);
  stdin.write('\u001b[Z');
  await pause(20);
  assert.match(lastFrame() ?? '', /› 拒绝 ‹/);
  stdin.write('\u001b');
  await pause(20);
  stdin.write('保留');
  await pause(20);
  assert.match(lastFrame() ?? '', /草稿继续保留/);
  assert.deepEqual(client.approvals, []);
});

test('approval buttons ignore pasted reports, drags, outside releases and a press on an expired request', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  await enter(stdin, 'work');
  const request = (id: string) => client.emit({type: 'approval_request', id: 'run-1', approval_id: id,
    call: {id: 'tool', name: 'bash', args: {}}, preview: 'check', decisions: ['once', 'deny']});
  request('old');
  await pause(25);
  const {column, row} = buttonPoint(lastFrame() ?? '', '允许本次命令');
  const press = `\u001b[<0;${column};${row}M`, release = `\u001b[<0;${column};${row}m`;
  stdin.write(`\u001b[200~${press}${release}\u001b[201~`);
  await pause(20);
  stdin.write(press + `\u001b[<0;79;${row}m`);
  await pause(20);
  stdin.write(press + `\u001b[<32;${column};${row}M` + release);
  await pause(20);
  stdin.write(release);
  await pause(20);
  assert.deepEqual(client.approvals, []);
  stdin.write(press);
  await pause(20);
  request('new');
  await pause(25);
  stdin.write(release);
  await pause(20);
  assert.deepEqual(client.approvals, []);
  await clickButton(stdin, lastFrame() ?? '', '拒绝');
  assert.deepEqual(client.approvals, [['new', 'deny']]);
});

test('a pressed approval shows focus and resizing cancels its pending click', async () => {
  const client = new MockClient();
  const state: ViewState = {...initialState, phase: 'waiting_approval', activeId: 'run-1', approval: {
    id: 'resize', tool: 'write_file', call: {id: 'tool', name: 'write_file', args: {}}, preview: 'write file', decisions: ['once', 'deny']}};
  const {stdin, lastFrame, rerender} = render(<App client={client as unknown as BackendClient}
    initialState={state} windowSize={{columns: 80, rows: 24}}/>);
  await pause(25);
  const point = buttonPoint(lastFrame() ?? '', '允许本次写入');
  stdin.write(`\u001b[<0;${point.column};${point.row}M`);
  await pause(25);
  assert.match(lastFrame() ?? '', /› 允许本次写入 ‹/);
  assert.deepEqual(client.approvals, []);
  rerender(<App client={client as unknown as BackendClient} initialState={state} windowSize={{columns: 32, rows: 12}}/>);
  await pause(25);
  const resized = buttonPoint(lastFrame() ?? '', '允许本次');
  stdin.write(`\u001b[<0;${resized.column};${resized.row}m`);
  await pause(25);
  assert.deepEqual(client.approvals, []);
  await clickButton(stdin, lastFrame() ?? '', '拒绝');
  assert.deepEqual(client.approvals, [['resize', 'deny']]);
});

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
    assert.match(frame, /当前上下文.*50\/900/);
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
  assert.match(frame, /当前上下文 100\/960k/);
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

test('approval arrival preserves typing until the user focuses, selects and confirms a scope', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'write file');
  stdin.write('草稿');
  await pause(20);
  client.emit({type: 'approval_request', id: 'run-1', approval_id: 'approval-focus',
    call: {id: 'tool-focus', name: 'write_file', args: {}}, preview: 'write a.txt',
    decisions: ['once', 'session', 'workspace', 'deny']});
  await pause(20);
  for (const key of ['y', 's', 'p', 'n']) {
    stdin.write(key);
    await pause(15);
  }
  assert.deepEqual(client.approvals, []);
  assert.match(lastFrame() ?? '', /草稿yspn/);
  stdin.write('\u0012');
  await pause(20);
  stdin.write('s');
  await pause(20);
  assert.deepEqual(client.approvals, []);
  stdin.write('\r');
  await pause(20);
  assert.deepEqual(client.approvals, [['approval-focus', 'session']]);
  assert.match(lastFrame() ?? '', /草稿yspn/);
});

test('shows complete approval preview and clears a denied request', async () => {
  const {client, stdin, lastFrame, frames} = await mount();
  await enter(stdin, 'write file');
  client.emit({type: 'approval_request', id: 'run-1', approval_id: 'approval-1', call: {id: 'tool-1', name: 'write_file', args: {}}, preview: 'PREVIEW_START\nwrite /tmp/repo/a.txt\nPREVIEW_END'});
  await pause(20);
  assert.match(lastFrame() ?? '', /等待审批/);
  assert.match(frames.join('\n'), /PREVIEW_END/);
  await approve(stdin, 'n');
  assert.deepEqual(client.approvals, [['approval-1', 'deny']]);
  assert.doesNotMatch(lastFrame() ?? '', /y 允许一次/);
  client.emit({type: 'approval_resolved', id: 'run-1', approval_id: 'approval-1', decision: 'deny'});
  await pause(20);
  assert.doesNotMatch(lastFrame() ?? '', /y 允许一次/);
});

test('approval selection is bound to its request and a duplicate request cannot be approved twice', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'work');
  const request = (id: string) => ({type: 'approval_request', id: 'run-1', approval_id: id,
    call: {id: 'tool', name: 'write_file', args: {}}, preview: id, decisions: ['once', 'session', 'deny']});
  client.emit(request('old'));
  await pause(20);
  stdin.write('\u0012');
  await pause(20);
  stdin.write('s');
  await pause(20);
  client.emit(request('new'));
  await pause(20);
  assert.doesNotMatch(lastFrame() ?? '', /已选择/);
  stdin.write('\r');
  await pause(20);
  assert.deepEqual(client.approvals, []);
  stdin.write('\u0012');
  await pause(20);
  stdin.write('s');
  await pause(20);
  stdin.write('\r');
  stdin.write('\r');
  await pause(20);
  client.emit(request('new'));
  await pause(20);
  stdin.write('\u0012');
  await pause(20);
  stdin.write('s');
  await pause(20);
  stdin.write('\r');
  await pause(20);
  assert.deepEqual(client.approvals, [['new', 'session']]);
});

test('asynchronous approval rejection stays visible with a safe recovery action', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'work');
  client.emit({type: 'approval_request', id: 'run-1', approval_id: 'expired',
    call: {id: 'tool', name: 'write_file', args: {}}, preview: 'write'});
  await pause(20);
  await approve(stdin, 'y');
  assert.match(lastFrame() ?? '', /审批已发送.*等待后端确认/);
  client.emit({type: 'error', id: 'approve-1', code: 'approval_mismatch', message: 'already resolved'});
  await pause(20);
  assert.match(lastFrame() ?? '', /审批未生效/);
  assert.match(lastFrame() ?? '', /Ctrl\+C/);
  stdin.write('\u0003');
  await pause(20);
  assert.deepEqual(client.cancellations, ['run-1']);
});

test('an older answered approval cannot reopen after the next one has been answered', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'work');
  const request = (id: string) => ({type: 'approval_request', id: 'run-1', approval_id: id,
    call: {id: 'tool', name: 'write_file', args: {}}, preview: id});
  for (const id of ['first', 'second']) {
    client.emit(request(id));
    await pause(20);
    await approve(stdin, 'n');
  }
  client.emit(request('first'));
  await pause(20);
  assert.doesNotMatch(lastFrame() ?? '', /等待审批/);
  assert.deepEqual(client.approvals, [['first', 'deny'], ['second', 'deny']]);
});

for (const outcome of ['done', 'closed'] as const) {
  test(`approval send feedback does not hide the final ${outcome} outcome`, async () => {
    const {client, stdin, lastFrame} = await mount();
    await enter(stdin, 'work');
    client.emit({type: 'approval_request', id: 'run-1', approval_id: 'a',
      call: {id: 'tool', name: 'write_file', args: {}}, preview: 'write'});
    await pause(20);
    await approve(stdin, 'y');
    if (outcome === 'done') {
      client.emit({type: 'ack', id: 'approve-1', action: 'approval'});
      client.emit(completed('run-1'));
    } else client.emit({type: 'closed'});
    await pause(20);
    assert.doesNotMatch(lastFrame() ?? '', /等待后端确认|等待执行结果/);
    if (outcome === 'closed') assert.match(lastFrame() ?? '', /审批结果未知/);
  });
}

test('approval navigation requires a choice, Escape restores editing, and expiry clears the choice', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'work');
  stdin.write('草稿');
  await pause(20);
  client.emit({type: 'approval_request', id: 'run-1', approval_id: 'expiring',
    call: {id: 't', name: 'write_file', args: {}}, preview: 'change', decisions: ['once', 'deny']});
  await pause(20);
  stdin.write('\u0012');
  await pause(20);
  stdin.write('\r');
  await pause(20);
  assert.deepEqual(client.approvals, []);
  assert.match(lastFrame() ?? '', /先选择/);
  stdin.write('\u001b[B');
  await pause(20);
  assert.match(lastFrame() ?? '', /已选择.*允许本次/);
  stdin.write('\u001b');
  await pause(20);
  stdin.write('y');
  await pause(20);
  assert.match(lastFrame() ?? '', /草稿y/);
  assert.deepEqual(client.approvals, []);
  stdin.write('\u0012');
  await pause(20);
  stdin.write('y');
  await pause(20);
  client.emit({type: 'approval_resolved', id: 'run-1', approval_id: 'expiring', decision: 'deny'});
  await pause(20);
  stdin.write('\r');
  await pause(20);
  assert.deepEqual(client.approvals, []);
});

for (const [columns, rows] of [[32, 12], [80, 24], [120, 40]]) {
  test(`approval actions stay visible above plan and goal hints at ${columns}x${rows}`, async () => {
    const client = new MockClient();
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient}
      windowSize={{columns: columns!, rows: rows!}}
      initialState={{...initialState, phase: 'idle', planMode: {...initialState.planMode, active: true}}}/>);
    await pause(20);
    await enter(stdin, `/goal ${'很长的目标标题与约束'.repeat(10)}`);
    client.emit({type: 'approval_request', id: 'run-1', approval_id: 'a',
      call: {id: 't', name: 'write_file', args: {}}, preview: '这是一段很长的审批预览，需要保留拒绝与停止入口。',
      decisions: ['once', 'session', 'workspace', 'deny']});
    await pause(20);
    let footer = (lastFrame() ?? '').split('\n').slice(-2).join('\n');
    assert.match(lastFrame() ?? '', /›\s+草稿可编辑/);
    buttonPoint(lastFrame() ?? '', columns! < 60 ? '允许本次' : '允许本次写入');
    buttonPoint(lastFrame() ?? '', '拒绝');
    buttonPoint(lastFrame() ?? '', columns! < 60 ? '更多' : '更多授权…');
    assert.match(footer, /Tab/);
    assert.match(footer, /Ctrl\+C/);
    stdin.write('\u0012');
    await pause(20);
    footer = (lastFrame() ?? '').split('\n').slice(-3).join('\n');
    assert.match(footer, /Enter/);
    assert.match(footer, /Esc/);
    assert.match(footer, /Ctrl\+C/);
    assert.ok((lastFrame() ?? '').split('\n').length <= rows!);
    assert.ok((lastFrame() ?? '').split('\n').every(line => stringWidth(line) <= columns!));
  });
}

test('an undersized terminal offers safe exit without enabling approval actions', async () => {
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient}
    windowSize={{columns: 24, rows: 11}} initialState={{...initialState, phase: 'waiting_approval', activeId: 'r',
      approval: {id: 'a', tool: 'write_file', call: {id: 't', name: 'write_file', args: {}}, preview: 'change', decisions: ['once', 'deny']}}}/>);
  await pause(20);
  await approve(stdin, 'y');
  assert.deepEqual(client.approvals, []);
  assert.match(lastFrame() ?? '', /32×12/);
  assert.match(lastFrame() ?? '', /Ctrl\+C/);
  stdin.write('\u0011');
  await pause(20);
  assert.match(lastFrame() ?? '', /停止任务并退出/);
  assert.match(lastFrame() ?? '', /Enter/);
  stdin.write('\u001b');
  await pause(20);
  assert.deepEqual(client.approvals, []);
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

test('Ctrl+Q prompts before shutting down during a run', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'work');
  stdin.write('\u0011');
  await pause(120);
  assert.equal(client.shutdowns, 0);
  assert.match(lastFrame() ?? '', /退出选项/);
  stdin.write('2');
  await pause(25);
  assert.equal(client.shutdowns, 1);
});

test('attach Ctrl+Q detaches without prompting or cancelling the worker', async () => {
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} attach />);
  await pause(20);
  client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 's'});
  client.emit({type: 'status', phase: 'starting', id: 'run-worker'});
  client.emit({type: 'status', phase: 'running', id: 'run-worker'});
  await pause(20);
  stdin.write('\u0011');
  await pause(80);
  assert.equal(client.cancellations.length, 0);
  assert.equal(client.shutdowns, 1);
  assert.doesNotMatch(lastFrame() ?? '', /停止任务并退出/);
});

test('/goal quit prompt offers background handoff when a task exists', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-quit-goal-'));
  try {
    const state = {...initialState, phase: 'idle' as const, workspace: '/repo', model: 'fake', sessionId: 'session-quit', sessionPath: dir};
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} initialState={state} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 'session-quit', session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/goal 后台目标');
    await pause(20);
    client.emit({type: 'task_state', id: 'run-1', task: {
      task_id: 't-20261103-120000-abcdef', kind: 'goal', status: 'running', segment: 1,
      objective: '后台目标', goal_id: null, updated_at: '2026-11-03T12:00:00+00:00',
    }});
    await pause(20);
    stdin.write('\u0011');
    await pause(80);
    assert.match(lastFrame() ?? '', /转入后台/);
    assert.match(lastFrame() ?? '', /等待完成/);
    stdin.write('4');
    await pause(20);
    assert.equal(client.shutdowns, 0);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('/task lists session tasks and startup hints are visible', async () => {
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 's', capabilities: [], tasks: [
    {task_id: 't-20261103-120000-abcdef', kind: 'goal', status: 'paused', worker_status: 'running', stale: false},
  ]});
  client.emit({type: 'status', phase: 'idle'});
  await pause(30);
  assert.match(lastFrame() ?? '', /检测到 1 个任务/);
  await enter(stdin, '/task');
  await pause(20);
  assert.deepEqual(client.commands, ['tasks']);
  assert.match(lastFrame() ?? '', /\/task/);
  client.emit({type: 'command_result', id: 'command-1', command: 'tasks', text: '任务列表'});
  await pause(20);
  await enter(stdin, '/approvals');
  await pause(20);
  assert.deepEqual(client.commands, ['tasks', 'approvals']);
  await enter(stdin, '/attach');
  await pause(20);
  await enter(stdin, '/logs');
  await pause(20);
  assert.match(lastFrame() ?? '', /工作台记录 ·/);
  assert.match(lastFrame() ?? '', /--attach-task t-20261103-120000-abcdef/);
});
test('Shift+Enter from a kitty-capable terminal inserts a newline before sending', async () => {
  const {client, stdin, lastFrame} = await mount();
  stdin.write('第一行'); await pause(20);
  stdin.write('\u001b[13;2u'); await pause(20);
  stdin.write('第二行'); await pause(20);
  assert.deepEqual(client.runs, []);
  assert.match(lastFrame() ?? '', /第二行/);
  stdin.write('\r'); await pause(30);
  assert.deepEqual(client.runs, ['第一行\n第二行']);
});

test('Enter during startup keeps the draft and prompts again once the backend is ready', async () => {
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 80, rows: 24}}/>);
  await pause(30);
  assert.match(lastFrame() ?? '', /正在启动后端|可编辑草稿/u);
  stdin.write('计划任务'); await pause(20);
  stdin.write('\r'); await pause(30);
  assert.deepEqual(client.runs, []);
  assert.match(lastFrame() ?? '', /未发送：后端尚未就绪，草稿保留/u);
  assert.match(lastFrame() ?? '', /后端仍在启动/u);
  client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 's', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(30);
  assert.deepEqual(client.runs, []);
  assert.match(lastFrame() ?? '', /后端已就绪 · 草稿已保留，再按 Enter 发送/u);
  stdin.write('\r'); await pause(30);
  assert.deepEqual(client.runs, ['计划任务']);
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
  await approve(stdin, 'n');
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

test('a short workbench shows twelve transcript lines while retaining context and a three-line draft', async () => {
  const client = new MockClient();
  const state: ViewState = {...initialState, phase: 'idle', workspace: '/repo', model: 'fake',
    contextSnapshots: [loadedContextSnapshot()], entries: [{id: 0, kind: 'assistant',
      text: Array.from({length: 80}, (_, i) => `review-line-${i}`).join('\n')}]};
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} initialState={state} windowSize={{columns: 80, rows: 24}}/>);
  await pause(20);
  const frame = lastFrame() ?? '';
  assert.ok(frame.split('\n').filter(line => line.includes('review-line-')).length >= 12, frame);
  assert.match(frame, /当前上下文.*50\/900.*S20 M0 T30 H0/);
  assert.ok((frame.match(/就绪/g) ?? []).length <= 2, 'avoid a third duplicate status');
  stdin.write('\u001b[200~第一行草稿\n第二行草稿\n第三行草稿\u001b[201~'); await pause(20);
  for (const draft of ['第一行草稿', '第二行草稿', '第三行草稿']) assert.match(lastFrame() ?? '', new RegExp(draft));
  stdin.write('\u000b'); await pause(20);
  assert.match(lastFrame() ?? '', /基础上下文已装载/);
  stdin.write('\u001b'); await pause(20);
  assert.match(lastFrame() ?? '', /第三行草稿/);
  assert.ok((lastFrame() ?? '').split('\n').filter(line => line.includes('review-line-')).length >= 12);
  assert.ok((lastFrame() ?? '').split('\n').length <= 24);
  assert.ok((lastFrame() ?? '').split('\n').every(line => stringWidth(line) <= 80));
  assert.deepEqual(client.runs, []);
});

for (const [columns, rows] of [[80, 24], [32, 12]]) {
  test(`exit choices remain fully visible at ${columns}x${rows} and Escape restores the draft`, async () => {
    const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns, rows});
    await enter(stdin, 'task');
    stdin.write('保留退出前的草稿'); await pause(20);
    stdin.write('\u001b[D'); await pause(20);
    stdin.write('\u0011'); await pause(20);
    for (const label of ['等待完成', '停止任务并退出', '取消关闭']) assert.match(lastFrame() ?? '', new RegExp(label));
    assert.ok((lastFrame() ?? '').split('\n').length <= rows);
    assert.ok((lastFrame() ?? '').split('\n').every(line => stringWidth(line) <= columns));
    stdin.write('\u001b'); await pause(20);
    assert.match(lastFrame() ?? '', /保留退出前的草稿/);
    stdin.write('中'); await pause(20);
    assert.match(lastFrame() ?? '', /保留退出前的草中稿/);
    assert.deepEqual(client.cancellations, []);
    assert.equal(client.shutdowns, 0);
  });
}

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
  await approve(stdin, 'y');
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
  assert.match(lastFrame() ?? '', /\[ 允许本次 \] \[ 拒绝 \]/);
  stdin.write('\u001b[200~y\u001b[201~');
  await pause(25);
  assert.equal(client.approvals.length, 0);
  stdin.write('\u0012');
  await pause(20);
  stdin.write('\u001b[F');
  await pause(25);
  assert.match(lastFrame() ?? '', /PREVIEW_END/);
  const lines = (lastFrame() ?? '').split('\n');
  assert.ok(lines.length <= 12);
  assert.ok(lines.every(line => stringWidth(line) <= 32));
  stdin.write('n');
  await pause(20);
  stdin.write('\r');
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
  assert.match(frame, /↑↓ 选择 · Enter 确认 · Tab 填充 · 1\/\d+/);
  assert.ok(frame.split('\n').length <= 24);
  for (const line of frame.split('\n')) assert.ok(stringWidth(line) <= 80, line);
  stdin.write('\u001b[B');
  await pause(15);
  assert.match(lastFrame() ?? '', /2\/\d+/);
  stdin.write('\t');
  await pause(15);
  frame = lastFrame() ?? '';
  assert.match(frame, /\/context {2,}查看 · 查看上下文与预算/);
});

test('Chinese command intent accepts the visible operation and reaches its existing handler', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  stdin.write('/查看改动'); await pause(25);
  assert.match(lastFrame() ?? '', /\/diff/);
  stdin.write('\r'); await pause(25);
  assert.deepEqual(client.commands, ['diff']);
  assert.deepEqual(client.runs, []);
  assert.doesNotMatch(lastFrame() ?? '', /未知命令/);
});

test('accepting a parameter command keeps a draft until its Chinese task is supplied', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  client.capabilityList = ['tasks'];
  await enter(stdin, '/ac');
  assert.match(lastFrame() ?? '', /\/act <任务描述>/);
  assert.deepEqual(client.runs, []); assert.deepEqual(client.commands, []);
  await enter(stdin, '修复中文标题');
  assert.deepEqual(client.runs, ['修复中文标题']);
  assert.equal(client.runTasks[0]?.mode, 'act');
});

for (const command of ['/plan diff', '/plan  diff']) test(`bare ${command} requests parameters instead of generating a new plan`, async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 32, rows: 12});
  client.capabilityList = ['tasks'];
  await enter(stdin, command);
  assert.deepEqual(client.runs, []); assert.deepEqual(client.commands, []);
  assert.match(lastFrame() ?? '', /\/plan\s+diff/);
  assert.doesNotMatch(lastFrame() ?? '', /未知命令/);
  await enter(stdin, '1 2');
  assert.deepEqual(client.commands, ['plan']);
  assert.deepEqual(client.commandArgs, [{action: 'diff', version_a: 1, version_b: 2}]);
});

test('topic help describes the same operations as the menu without dispatching a task', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  await enter(stdin, '/help context');
  assert.match(lastFrame() ?? '', /用法：\/context/);
  await enter(stdin, '/logs');
  await pause(20);
  assert.match(lastFrame() ?? '', /查看上下文与预算/);
  stdin.write('\u001b'); await pause(20);
  await enter(stdin, '/help tools');
  assert.match(lastFrame() ?? '', /用法：\/tools/);
  await enter(stdin, '/logs');
  await pause(20);
  assert.match(lastFrame() ?? '', /查看可用工具目录/);
  assert.doesNotMatch(lastFrame() ?? '', /工具调用统计|未知命令/);
  assert.deepEqual(client.commands, []); assert.deepEqual(client.runs, []);
});

test('incomplete or invalid reserved plan commands never fall through to a model task', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  client.capabilityList = ['tasks'];
  for (const command of ['/plan diff 1', '/plan diff invalid 2', '/plan edit-step s1', '/plan off extra']) {
    stdin.write('\u0015'); await pause(15);
    await enter(stdin, command);
    assert.deepEqual(client.runs, [], command); assert.deepEqual(client.commands, [], command);
    assert.match(lastFrame() ?? '', /用法/);
    assert.ok(lastFrame()?.includes(command));
  }
});

test('Tab expands a command family without executing its first action', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  stdin.write('/plan'); await pause(20); stdin.write('\t'); await pause(20);
  assert.match(lastFrame() ?? '', /\/plan on/);
  assert.deepEqual(client.commands, []); assert.deepEqual(client.runs, []);
  stdin.write('\u001b[B'); await pause(15); stdin.write('\r'); await pause(20);
  assert.deepEqual(client.commands, ['plan']);
  assert.deepEqual(client.commandArgs, [{action: 'mode-off'}]);
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
  assert.match(lastFrame() ?? '', /更多授权/);
  assert.doesNotMatch(lastFrame() ?? '', /授权此工作区/);
  stdin.write('\u0012');
  await pause(20);
  stdin.write('p');
  await pause(20);
  assert.deepEqual(client.approvals, []);
  stdin.write('s');
  stdin.write('s');
  await pause(20);
  assert.deepEqual(client.approvals, []);
  stdin.write('\r');
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
  buttonPoint(lastFrame() ?? '', '更多');
  stdin.write('\u0012');
  await pause(20);
  stdin.write('p');
  stdin.write('p');
  await pause(20);
  assert.match(lastFrame() ?? '', /› 工作区 ‹/);
  assert.match(lastFrame() ?? '', /仅本进程有效/);
  assert.deepEqual(client.approvals, []);
  stdin.write('\r');
  stdin.write('\r');
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
  await approve(stdin, 'n');
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

test('cancelling a run retains queued follow-ups and keeps them paused during another task', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'first task');
  await enter(stdin, '/followup 迟到的后续');
  stdin.write('\u0003');
  await pause(20);
  client.emit(completed('run-1', 'cancelled'));
  await pause(20);
  assert.match(lastFrame() ?? '', /待办保留/);
  assert.match(lastFrame() ?? '', /后续 1/);
  assert.deepEqual(client.runs, ['first task']);
  await enter(stdin, 'second task');
  await pause(20);
  assert.deepEqual(client.runs, ['first task', 'second task']);
  client.emit(completed('run-2'));
  await pause(20);
  assert.deepEqual(client.runs, ['first task', 'second task']);
  assert.match(lastFrame() ?? '', /后续 1/);
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
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  await enter(stdin, '/diff');
  assert.deepEqual(client.commands, ['diff']);
  stdin.write('/followup'); await pause(20);
  assert.match(lastFrame() ?? '', /暂不可用.*命令执行中/);
  await enter(stdin, ' 迟到的后续');
  assert.match(lastFrame() ?? '', /命令执行中，结束后再发送/);
  assert.deepEqual(client.runs, []);
  client.emit({type: 'command_result', id: 'command-1', command: 'diff', text: '无改动'}); await pause(20);
  assert.deepEqual(client.runs, []);
  assert.match(lastFrame() ?? '', /› \/followup 迟到的后续/);
});

test('/followup queues messages while running and starts them in FIFO order after each done', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'first task');
  await enter(stdin, '/followup 第二个任务');
  await enter(stdin, '/followup 第三个任务');
  assert.deepEqual(client.runs, ['first task']);
  await enter(stdin, '/logs');
  await pause(20);
  assert.match(lastFrame() ?? '', /已排队为后续消息：第二个任务/);
  assert.match(lastFrame() ?? '', /已排队为后续消息：第三个任务/);
  stdin.write('\u001b'); await pause(20);
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
  stdin.write('2');
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

for (const copied of [true, false]) {
  test(`copy ${copied ? 'success' : 'failure'} preserves the reading anchor, focus and draft`, async () => {
    const state: ViewState = {...initialState, phase: 'idle', entries: [{id: 1, kind: 'assistant',
      text: Array.from({length: 90}, (_, i) => `stable-line-${i}`).join('\n')}], nextEntry: 2};
    const {stdin, lastFrame} = await mount(() => copied ? 'pbcopy' : null, state, {columns: 80, rows: 24});
    stdin.write('保留的草稿');
    await pause(20);
    stdin.write('\u0012');
    await pause(20);
    stdin.write('\u001b[5~');
    await pause(20);
    const anchor = (lastFrame() ?? '').match(/stable-line-\d+/)?.[0];
    assert.ok(anchor);
    stdin.write('\u0019');
    await pause(25);
    assert.equal((lastFrame() ?? '').match(/stable-line-\d+/)?.[0], anchor);
    assert.match(lastFrame() ?? '', /阅读焦点/);
    assert.match(lastFrame() ?? '', /保留的草稿/);
    assert.match((lastFrame() ?? '').split('\n').slice(-2).join('\n'), copied ? /已复制/ : /复制失败/);
  });
}

test('theme feedback leaves a scrolled conversation in place', async () => {
  const state: ViewState = {...initialState, phase: 'idle', entries: [{id: 1, kind: 'assistant',
    text: Array.from({length: 90}, (_, i) => `theme-line-${i}`).join('\n')}], nextEntry: 2};
  const {stdin, lastFrame} = await mount(undefined, state, {columns: 80, rows: 24});
  stdin.write('\u001b[5~');
  await pause(20);
  const anchor = (lastFrame() ?? '').match(/theme-line-\d+/)?.[0];
  assert.ok(anchor);
  await enter(stdin, '/theme light');
  assert.equal((lastFrame() ?? '').match(/theme-line-\d+/)?.[0], anchor);
  assert.match((lastFrame() ?? '').split('\n').slice(-2).join('\n'), /主题已切换/);
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
    await enter(stdin, '/logs');
    await pause(20);
    assert.match(lastFrame() ?? '', /暂无条目/);
    stdin.write('\u001b'); await pause(20);

    await enter(stdin, '/about add 回答先给结论');
    await pause(20);
    await enter(stdin, '/about propose 可能偏好简短回复');
    await pause(20);
    let payload = await stored();
    assert.equal(payload.entries.length, 2);
    assert.deepEqual(payload.entries.map(entry => entry.status), ['confirmed', 'proposed']);

    await enter(stdin, '/about');
    await pause(20);
    await enter(stdin, '/logs');
    await pause(20);
    assert.match(lastFrame() ?? '', /1\. \[已确认\] 回答先给结论/);
    assert.match(lastFrame() ?? '', /2\. \[待确认\] 可能偏好简短回复/);
    stdin.write('\u001b'); await pause(20);

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

test('workbenches publish and launch the same unique owner and unmount only removes their own signal', async () => {
  const workspace = await mkdtemp(path.join(tmpdir(), 'sidebar-app-owners-'));
  const signals: SidebarSignalInput[] = [];
  const launches: unknown[][] = [];
  const publish = (root: string, input: SidebarSignalInput) => {signals.push(input); writeSidebarSignal(root, input);};
  const props = {windowSize: {columns: 80, rows: 24}, publishSidebarSignal: publish};
  const a = render(<App {...props} client={new MockClient() as unknown as BackendClient}
    initialState={{...initialState, phase: 'idle', workspace, sessionId: 'a'}}
    launchSidebar={(...args) => {launches.push(args); return {outcome: 'started', message: 'opened'};}}/>);
  await pause(20);
  const ownerA = signals.at(-1)?.owner;
  const b = render(<App {...props} client={new MockClient() as unknown as BackendClient}
    initialState={{...initialState, phase: 'idle', workspace, sessionId: 'b'}}/>);
  try {
    await pause(20); const ownerB = signals.at(-1)?.owner;
    assert.ok(validOwner(ownerA) && validOwner(ownerB));
    assert.notEqual(ownerA.instanceId, ownerB.instanceId);
    await enter(a.stdin, '/sidebar');
    assert.deepEqual(launches[0]?.[3], ownerA);
    assert.equal(readSidebarSignal(workspace, ownerA)?.scope?.sessionId, 'a');
    a.unmount(); await pause(20);
    assert.equal(readSidebarSignal(workspace, ownerA), null);
    assert.equal(readSidebarSignal(workspace, ownerB)?.scope?.sessionId, 'b');
  } finally {a.unmount(); b.unmount(); await rm(workspace, {recursive: true, force: true});}
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

test('an already-running sidebar is focused instead of opening a duplicate', async () => {
  const launches: string[] = []; const focuses: string[] = [];
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}
    initialState={{...initialState, phase: 'idle', workspace: '/repo', model: 'fake'}}
    launchSidebar={workspace => {launches.push(workspace); return {outcome: 'started', message: 'unexpected'};}}
    focusSidebar={workspace => {focuses.push(workspace); return true;}}/>);
  await pause(20);
  await enter(stdin, '/sidebar');
  assert.deepEqual(focuses, ['/repo']);
  assert.deepEqual(launches, []);
  assert.match(lastFrame() ?? '', /侧栏已在运行 · 已聚焦/u);
});

test('plan review and live progress render a structured subtask checklist', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 100, rows: 40});
  client.capabilityList = ['plan-review', 'tasks'];
  await enter(stdin, '/plan');
  assert.deepEqual(client.commands, ['plan']);
  const target = {session_id: 'test', plan_task_id: 't-20261108-000000-abcdef', plan_id: 'pl-1', plan_version: 1, plan_hash: 'a'.repeat(64)};
  client.emit({type: 'plan_review', id: 'command-1', target, outcome: 'ready', code: null, message: '计划已生成',
    replacement_target: null, launch: null, body_page: '计划正文', next_cursor: null,
    available_actions: ['approve-execute', 'approve-only', 'dismiss'],
    summary: {objective: '修复输入', status: 'proposed', executable: false, step_count: 2, criterion_count: 1, question_count: 0},
    steps: [
      {step_id: 's1', title: '读入口', kind: 'read', risk: 'read', files: ['README.md'], depends_on: [], acceptance: '', executor: 'self'},
      {step_id: 's2', title: '改实现', kind: 'edit', risk: 'write', files: ['app/a.ts'], depends_on: ['s1'], acceptance: '测试通过', executor: 'self'},
    ]});
  await pause(30);
  assert.match(lastFrame() ?? '', /1\. ○ s1 读入口/);
  assert.match(lastFrame() ?? '', /2\. ○ s2 改实现/);
  await enter(stdin, '执行计划');
  assert.deepEqual(client.runs, ['执行计划']);
  client.emit({type: 'plan_progress', id: 'run-1',
    plan: {plan_id: 'pl-1', task_id: 't-20261108-000000-abcdef', version: 1, status: 'executing', objective: '修复输入'},
    steps: [
      {step_id: 's1', title: '读入口', kind: 'read', risk: 'read', files: ['README.md'], status: 'done', evidence_count: 2},
      {step_id: 's2', title: '改实现', kind: 'edit', risk: 'write', files: ['app/a.ts'], status: 'in_progress', evidence_count: 0},
    ], done: 1, total: 2});
  await pause(30);
  const frame = lastFrame() ?? '';
  assert.match(frame, /计划 v1 · 1\/2 步 · 当前 s2/);
  assert.match(frame, /✓ s1 读入口/);
  assert.match(frame, /◐ s2 改实现/);
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
  const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}
    initialState={{...initialState, phase: 'idle', workspace: '/repo', model: 'fake'}}
    publishSidebarSignal={(workspace, signal) => {published.push({workspace, files: signal.files});}}/>);
  await pause(20); await enter(stdin, '修改文件');
  client.emit({type: 'tool_result', id: 'run-1', call: {id: 'c1', name: 'edit_file', args: {path: 'tui/src/app.tsx'}}, ok: true, output: 'edited'});
  client.emit({type: 'tool_result', id: 'run-1', call: {id: 'c2', name: 'apply_patch', args: {patch}}, ok: true, output: 'applied'});
  await pause(40);
  const latest = published.at(-1);
  assert.equal(latest?.workspace, '/repo');
  assert.ok(latest?.files.includes('tui/src/app.tsx'));
  assert.ok(latest?.files.includes('src/a.ts'));
});

test('skills defaults to listing and activates only the explicitly selected skill', async () => {
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
  assert.match(lastFrame() ?? '', /↑↓ 选择 · Enter 确认 · Tab 填充/);
  stdin.write('\r'); await pause(25);
  assert.deepEqual(client.commandArgs, [{action: 'list'}]);
  client.emit({type: 'command_result', id: 'command-1', command: 'skills', text: '技能列表'});
  await pause(20); stdin.write('/skills'); await pause(20);
  stdin.write('\u001b[B'); await pause(15);
  stdin.write('\u001b[B');
  await pause(15);
  stdin.write('\t');
  await pause(20);
  stdin.write('\r');
  await pause(30);
  assert.deepEqual(client.commands, ['skills', 'skills']);
  assert.deepEqual(client.commandArgs, [{action: 'list'}, {action: 'activate', name: 'tdd'}]);
});


test('/goal automatically continues a long task across resource pauses', async () => {
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 's', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/goal 完成跨文件重构');
  await pause(30);
  assert.equal(client.runs.length, 1);
  assert.match(client.runs[0]!, /\[GOAL MODE\]/);
  client.emit({type: 'done', id: 'run-1', result: {
    text: 'checkpoint', steps: 3, status: 'stopped', stop_reason: 'max_run_seconds',
    usage: {total_tokens: 100}, cost_usd: 0.01, run_id: 'run-1',
  }});
  await pause(30);
  assert.equal(client.runs.length, 2);
  assert.match(client.runs[1]!, /\[GOAL MODE · 第 2\/8 段\]/);
  assert.match(lastFrame() ?? '', /目标续段/);
  client.emit({type: 'done', id: 'run-2', result: {
    text: '已完成', steps: 1, status: 'done', stop_reason: '',
    usage: {total_tokens: 50}, cost_usd: 0.005, run_id: 'run-2',
  }});
  await pause(30);
  assert.equal(client.runs.length, 2);
  assert.match(lastFrame() ?? '', /目标模式.*完成|目标完成/u);
});

test('/goal sends task metadata and persists the task id when the backend supports tasks', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-goal-task-'));
  try {
    const state = {...initialState, phase: 'idle' as const, workspace: '/repo', model: 'fake', sessionId: 'session-task', sessionPath: dir};
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} initialState={state} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 'session-task', session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);

    await enter(stdin, '/goal 长任务');
    await pause(30);
    assert.equal(client.runs.length, 1);
    assert.deepEqual(client.runTasks[0], {kind: 'goal', objective: '长任务'});

    client.emit({type: 'task_state', id: 'run-1', task: {
      task_id: 't-20261103-120000-abcdef', kind: 'goal', status: 'running', segment: 1,
      objective: '长任务', goal_id: null, updated_at: '2026-11-03T12:00:00+00:00',
    }});
    await pause(30);
    assert.match(lastFrame() ?? '', /任务已记录/);
    const raw = JSON.parse(await readFile(path.join(dir, 'goal.json'), 'utf8'));
    assert.equal(raw.taskId, 't-20261103-120000-abcdef');

    client.emit({type: 'done', id: 'run-1', result: {
      text: 'checkpoint', steps: 1, status: 'stopped', stop_reason: 'max_run_seconds',
      usage: {total_tokens: 10}, cost_usd: 0, run_id: 'run-1',
    }});
    await pause(40);
    assert.equal(client.runs.length, 2);
    assert.equal(client.runTasks[1]?.task_id, 't-20261103-120000-abcdef');
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('/goal stop pauses the active goal without starting another segment', async () => {
  const client = new MockClient();
  const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 's', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/goal 长任务');
  await pause(20);
  await enter(stdin, '/goal stop');
  await pause(20);
  client.emit({type: 'done', id: 'run-1', result: {
    text: 'checkpoint', steps: 1, status: 'stopped', stop_reason: 'max_run_seconds',
    usage: {total_tokens: 10}, cost_usd: 0, run_id: 'run-1',
  }});
  await pause(20);
  assert.equal(client.runs.length, 1);
});

test('a goal is saved in the session and restored as paused after restart', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-goal-app-'));
  try {
    const state = {...initialState, phase: 'idle' as const, workspace: '/repo', model: 'fake', sessionId: 'session-goal', sessionPath: dir};
    const client = new MockClient();
    const first = render(<App client={client as unknown as BackendClient} initialState={state} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    await enter(first.stdin, '/goal 保存到会话');
    await pause(40);
    const raw = JSON.parse(await readFile(path.join(dir, 'goal.json'), 'utf8'));
    assert.equal(raw.objective, '保存到会话');
    assert.equal(raw.active, true);
    first.unmount();

    const secondClient = new MockClient();
    const second = render(<App client={secondClient as unknown as BackendClient} initialState={state} windowSize={{columns: 120, rows: 40}}/>);
    await pause(40);
    const frame = second.lastFrame() ?? '';
    assert.match(frame, /目标状态已从会话恢复/);
    assert.match(frame, /目标暂停/);
    await enter(second.stdin, '/goal resume');
    await pause(30);
    assert.equal(secondClient.runs.length, 1);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('edits, tags and disables a user preference', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'user-profile-edit-'));
  const file = path.join(directory, 'user-profile.json');
  type Stored = {version: number; entries: {text: string; status: string; triggers?: string[]}[]};
  try {
    const {stdin} = await mount(undefined, undefined, {columns: 100, rows: 30}, undefined, file);
    await enter(stdin, '/about add 回答先给结论');
    await pause(20);
    await enter(stdin, '/about triggers 1 解释,说明');
    await pause(20);
    await enter(stdin, '/about edit 1 解释代码时先给结论');
    await pause(20);
    await enter(stdin, '/about disable 1');
    await pause(20);
    const payload = JSON.parse(await readFile(file, 'utf8')) as Stored;
    assert.equal(payload.version, 2);
    assert.equal(payload.entries[0]?.text, '解释代码时先给结论');
    assert.deepEqual(payload.entries[0]?.triggers, ['解释', '说明']);
    assert.equal(payload.entries[0]?.status, 'stale');
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});


test('/about observe turns explicit session statements into proposed preferences', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'preference-observe-app-'));
  const profile = path.join(directory, 'user-profile.json');
  try {
    await writeFile(path.join(directory, 'events.jsonl'), JSON.stringify({
      type: 'message', message: {role: 'user', content: '以后解释代码时先给结论，再给细节。'},
    }) + '\n', 'utf8');
    const state = {...initialState, phase: 'idle' as const, workspace: '/repo', sessionId: 's-observe', sessionPath: directory};
    const client = new MockClient();
    const {stdin} = render(<App client={client as unknown as BackendClient} initialState={state} windowSize={{columns: 100, rows: 30}} userProfilePath={profile}/>);
    await pause(20);
    await enter(stdin, '/about observe');
    await pause(30);
    const stored = JSON.parse(await readFile(profile, 'utf8')) as {entries: {status: string; source: string; triggers?: string[]}[]};
    assert.equal(stored.entries.at(-1)?.status, 'proposed');
    assert.equal(stored.entries.at(-1)?.source, 'observation');
    assert.ok(stored.entries.at(-1)?.triggers?.includes('解释'));
    await enter(stdin, '/about confirm 1');
    await pause(30);
    const confirmed = JSON.parse(await readFile(profile, 'utf8')) as {entries: {status: string}[]};
    assert.equal(confirmed.entries[0]?.status, 'confirmed');
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});


test('auto-observation writes proposed preferences after a completed run when enabled', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'auto-observe-app-'));
  const profile = path.join(directory, 'user-profile.json');
  try {
    await writeFile(path.join(directory, 'events.jsonl'), JSON.stringify({
      type: 'message', message: {role: 'user', content: '每次修改前先说明影响文件。'},
    }) + '\n', 'utf8');
    const state = {...initialState, phase: 'running' as const, activeId: 'run-1', workspace: '/repo', sessionId: 's-auto', sessionPath: directory, sessionPurpose: 'user' as const};
    const client = new MockClient();
    render(<App client={client as unknown as BackendClient} initialState={state} windowSize={{columns: 100, rows: 30}} userProfilePath={profile} autoObserve/>);
    await pause(20);
    client.emit({type: 'done', id: 'run-1', result: {text: 'done', steps: 1, status: 'done', stop_reason: '', usage: {}, cost_usd: 0, run_id: 'run-1'}});
    await pause(40);
    const stored = JSON.parse(await readFile(profile, 'utf8')) as {entries: {status: string; text: string}[]};
    assert.equal(stored.entries[0]?.status, 'proposed');
    assert.match(stored.entries[0]?.text ?? '', /修改前先说明/u);
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});

test('sets expiry and clears proposed or stale entries without touching confirmed ones', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'user-profile-expire-'));
  const file = path.join(directory, 'user-profile.json');
  type Stored = {entries: {text: string; status: string; expires_at?: string}[]};
  try {
    const {stdin} = await mount(undefined, undefined, {columns: 100, rows: 30}, undefined, file);
    await enter(stdin, '/about add 保留的确认偏好');
    await pause(20);
    await enter(stdin, '/about propose 待清理观察');
    await pause(20);
    await enter(stdin, '/about expire 1 1');
    await pause(20);
    let payload = JSON.parse(await readFile(file, 'utf8')) as Stored;
    assert.ok(payload.entries[0]?.expires_at);
    await enter(stdin, '/about expire 1 -');
    await pause(20);
    payload = JSON.parse(await readFile(file, 'utf8')) as Stored;
    assert.equal(payload.entries[0]?.expires_at, undefined);
    await enter(stdin, '/about clear proposed');
    await pause(20);
    payload = JSON.parse(await readFile(file, 'utf8')) as Stored;
    assert.deepEqual(payload.entries.map(entry => entry.text), ['保留的确认偏好']);
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});


test('rejects sensitive preference text and omits it from export', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'user-profile-secret-'));
  const file = path.join(directory, 'user-profile.json');
  try {
    await writeFile(file, JSON.stringify({version: 2, enabled: true, entries: [
      {text: '以后用中文回答', source: 'user', status: 'confirmed'},
      {text: 'api_key=sk-1234567890abcdef', source: 'user', status: 'confirmed'},
    ]}), 'utf8');
    const {stdin, lastFrame} = await mount(undefined, undefined, {columns: 100, rows: 30}, undefined, file);
    await enter(stdin, '/about add password=super-secret-value');
    await pause(20);
    assert.match(lastFrame() ?? '', /疑似密钥|凭据/u);
    await enter(stdin, '/about export');
    await pause(30);
    const exportedDir = path.join(directory, 'exports');
    const exportedFiles = await readdir(exportedDir);
    assert.equal(exportedFiles.length, 1);
    const payload = JSON.parse(await readFile(path.join(exportedDir, exportedFiles[0]!), 'utf8')) as {entries: {text: string}[]};
    assert.deepEqual(payload.entries.map(entry => entry.text), ['以后用中文回答']);
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});

test('/about reset requires confirmation and removes the profile', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'user-profile-reset-'));
  const file = path.join(directory, 'user-profile.json');
  try {
    const {stdin, lastFrame} = await mount(undefined, undefined, {columns: 100, rows: 30}, undefined, file);
    await enter(stdin, '/about add 以后回答一律用中文');
    await pause(20);
    await enter(stdin, '/about reset');
    await pause(20);
    assert.match(lastFrame() ?? '', /reset confirm/u);
    await enter(stdin, '/about');
    await pause(20);
    assert.match(lastFrame() ?? '', /1\. \[已确认\]/u);
    await enter(stdin, '/about reset');
    await pause(20);
    await enter(stdin, '/about reset confirm');
    await pause(30);
    await enter(stdin, '/about');
    await pause(30);
    assert.match(lastFrame() ?? '', /暂无条目/u);
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});

test('/about resolve replaces a conflicted confirmed preference', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'user-profile-resolve-'));
  const file = path.join(directory, 'user-profile.json');
  try {
    await writeFile(file, JSON.stringify({version: 2, enabled: true, entries: [
      {id: 'pref-old', text: '回答先给结论', source: 'user', status: 'confirmed'},
      {id: 'pref-new', text: '回答先给结论再给细节', source: 'observation', status: 'proposed', evidence: ['conflict:pref-old']},
    ]}), 'utf8');
    const {stdin, lastFrame} = await mount(undefined, undefined, {columns: 100, rows: 30}, undefined, file);
    await enter(stdin, '/about');
    await pause(20);
    await enter(stdin, '/logs');
    await pause(20);
    assert.match(lastFrame() ?? '', /冲突待确认/u);
    stdin.write('\u001b'); await pause(20);
    await enter(stdin, '/about resolve 2');
    await pause(30);
    const payload = JSON.parse(await readFile(file, 'utf8')) as {entries: {status: string}[]};
    assert.deepEqual(payload.entries.map(entry => entry.status), ['stale', 'confirmed']);
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});


test('about audit shows recent profile actions', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'user-profile-audit-app-'));
  const file = path.join(directory, 'user-profile.json');
  try {
    const {stdin, lastFrame} = await mount(undefined, undefined, {columns: 100, rows: 30}, undefined, file);
    await enter(stdin, '/about add 以后回答一律用中文');
    await pause(20);
    await enter(stdin, '/about audit');
    await pause(20);
    assert.match(lastFrame() ?? '', /add · 1 条/u);
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});

test('unverified completion is a visible warning state and receipt card', async () => {
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 80, rows: 24}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 's', capabilities: [], autonomy: 'ask', autonomy_label: '每次询问'});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '修复 calc.py');
  assert.deepEqual(client.runs, ['修复 calc.py']);
  client.emit({type: 'done', id: 'run-1', result: {
    text: '已修复并完成。', steps: 2, status: 'done', usage: {}, cost_usd: 0, run_id: 'run-1', stop_reason: '',
    verified: false,
    completion: {allowed: false, codes: ['no_verification'], messages: ['有文件改动，但没有记录到验证命令']},
    audit: {status: 'incomplete', integrity: 'clean', verified: false, changed_files: 1, coverage: 'full'},
    receipt: '[运行结束] status=done steps=2\n[任务验收] 未验证：有文件改动，但没有记录到验证命令\n完成判定：未通过：有文件改动，但没有记录到验证命令',
  }});
  await pause(30);
  const frame = lastFrame() ?? '';
  assert.match(frame, /本轮结束 · 待验证|完成待验证/);
  assert.match(frame, /验收未通过/);
  assert.match(frame, /完成判定：未通过/);
  assert.doesNotMatch(frame, /已完成 · 已验证/);
});

test('verified completion is distinguished from unverified completion', async () => {
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 's', capabilities: [], autonomy: 'edits', autonomy_label: '自动改文件'});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '修复并验证');
  client.emit({type: 'done', id: 'run-1', result: {
    text: '已修复并验证。', steps: 3, status: 'done', usage: {}, cost_usd: 0, run_id: 'run-1', stop_reason: '',
    verified: true,
    completion: {allowed: true, codes: [], messages: []},
    audit: {status: 'complete', integrity: 'clean', verified: true, changed_files: 1, coverage: 'full'},
    receipt: '[运行结束] status=done steps=3\n[任务验收] 已验证（有环境证据）；仍需你核对是否满足原始需求\n环境审计：status=complete integrity=clean verified=True',
  }});
  await pause(30);
  const frame = lastFrame() ?? '';
  assert.match(frame, /本轮结束 · 已验证|已完成 · 已验证/);
  assert.match(frame, /验收通过/);
  assert.match(frame, /授权 edits · 自动改文件/);
});

test('command menu explains a busy operation before Enter and re-enables it after completion', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  await enter(stdin, 'work');
  stdin.write('/查看改动'); await pause(20);
  assert.match(lastFrame() ?? '', /\/diff.*暂不可用.*运行中/);
  stdin.write('\r'); await pause(20);
  assert.deepEqual(client.commands, []);
  assert.match(lastFrame() ?? '', /› \/查看改动/);
  assert.match(lastFrame() ?? '', /未执行.*草稿保留/);
  stdin.write('\u0015'); await pause(15);
  await enter(stdin, '/help tools');
  await enter(stdin, '/logs');
  await pause(20);
  assert.match(lastFrame() ?? '', /查看可用工具目录/);
  stdin.write('\u001b'); await pause(20);
  assert.deepEqual(client.runs, ['work']);
  stdin.write('/查看改动'); await pause(20);
  client.emit({type: 'done', id: 'run-1', result: {status: 'done', text: '完成'}}); await pause(20);
  assert.match(lastFrame() ?? '', /\/diff.*查看工作区改动/);
  stdin.write('\r'); await pause(20);
  assert.deepEqual(client.commands, ['diff']);
  assert.deepEqual(client.steers, []);
});

test('task-mode commands explain missing backend support before accepting a candidate', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  stdin.write('/ac'); await pause(20);
  assert.match(lastFrame() ?? '', /暂不可用.*未启用任务运行时/);
  stdin.write('\r'); await pause(20);
  assert.match(lastFrame() ?? '', /› \/ac/);
  assert.deepEqual(client.runs, []); assert.deepEqual(client.commands, []);
  client.capabilityList = ['tasks'];
  client.emit({type: 'status', phase: 'idle'}); await pause(20);
  stdin.write('\r'); await pause(20);
  assert.match(lastFrame() ?? '', /请补充参数.*\/act/);
  await enter(stdin, '修复标题');
  assert.deepEqual(client.runs, ['修复标题']);
  assert.equal(client.runTasks[0]?.mode, 'act');
});

test('disconnected resume shows a connection explanation instead of an executable budget hint', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  client.emit({type: 'closed'}); await pause(20);
  stdin.write('/resume'); await pause(20);
  assert.match(lastFrame() ?? '', /暂不可用.*连接已断开/);
  assert.doesNotMatch(lastFrame() ?? '', /开启新一轮预算/);
  stdin.write('\r'); await pause(20);
  assert.deepEqual(client.commands, []); assert.deepEqual(client.runs, []);
  assert.match(lastFrame() ?? '', /未发送.*连接已断开.*草稿保留/);
});

for (const command of ['/autonomy edits', '/cost', '/trace 24', '/plan on', '/priority task-1 50']) {
  test(`busy command preserves the draft and reports rejection: ${command}`, async () => {
    const {client, stdin, lastFrame} = await mount();
    await enter(stdin, 'work');
    await enter(stdin, command);
    assert.deepEqual(client.commands, []);
    assert.ok((lastFrame() ?? '').includes(`› ${command}`));
    assert.match(lastFrame() ?? '', /未执行.*运行中/);
    assert.deepEqual(client.steers, []);
  });
}

for (const phase of ['starting', 'stopping', 'waiting_approval', 'closed'] as const) {
  test(`disabled submission explains ${phase} and preserves editable draft`, async () => {
    const client = new MockClient();
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient}
      windowSize={{columns: 80, rows: 24}} initialState={{...initialState, phase}}/>);
    await pause(20);
    stdin.write('/autonomy edits'); await pause(20);
    const reason = {starting: '后端尚未就绪', stopping: '正在停止', waiting_approval: '等待审批', closed: '连接已断开'}[phase];
    assert.ok((lastFrame() ?? '').includes(`暂不可用 · ${reason}`));
    stdin.write('\r'); await pause(25);
    assert.deepEqual(client.commands, []);
    assert.ok((lastFrame() ?? '').includes('› /autonomy edits'));
    assert.match(lastFrame() ?? '', /未发送.*草稿保留/);
    stdin.write('x');
    await pause(20);
    assert.ok((lastFrame() ?? '').includes('› /autonomy editsx'));
  });
}

for (const command of ['/autonomy edits', '/plan on', '/plan dismiss', '/plan request-changes simplify', '/priority task-1 50']) {
  test(`failed command send preserves the draft: ${command}`, async () => {
    const {client, stdin, lastFrame} = await mount();
    client.sendError = true;
    await enter(stdin, command);
    assert.deepEqual(client.commands, []);
    assert.ok((lastFrame() ?? '').includes(`› ${command}`));
    assert.match(lastFrame() ?? '', /未送达/);
  });
}

test('/evidence and /autonomy reach the backend and sync the autonomy badge', async () => {
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 's', capabilities: [], autonomy: 'ask', autonomy_label: '每次询问'});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);

  await enter(stdin, '/evidence');
  assert.deepEqual(client.commands, ['evidence']);
  client.emit({type: 'command_result', id: 'command-1', command: 'evidence', text: '最近一轮：round_001\n完成判定：未通过（no_verification）'});
  await pause(20);
  assert.match(lastFrame() ?? '', /round_001/);

  await enter(stdin, '/autonomy');
  assert.deepEqual(client.commands, ['evidence']);
  assert.match(lastFrame() ?? '', /当前授权档位：ask · 每次询问/);

  await enter(stdin, '/autonomy edits');
  assert.deepEqual(client.commands, ['evidence', 'autonomy']);
  assert.deepEqual(client.commandArgs[1], {level: 'edits'});
  client.emit({type: 'command_result', id: 'command-2', command: 'autonomy', text: '授权档位已切换：edits（自动改文件）', autonomy: 'edits'});
  await pause(20);
  assert.match(lastFrame() ?? '', /授权档位已切换：edits/);
  assert.match(lastFrame() ?? '', /授权 edits/);
});

test('approval panel moves the tier into the title instead of the preview tail', async () => {
  const client = new MockClient();
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 80, rows: 24}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 's', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '改一下 a.txt');
  client.emit({type: 'approval_request', id: 'run-1', approval_id: 'a1',
    call: {id: 'c1', name: 'write_file', args: {path: 'a.txt'}},
    preview: '--- a/a.txt\n+++ b/a.txt\n+hello\n\n[审批层] T1 工作区写入',
    decisions: ['once', 'session', 'workspace', 'deny'],
    tier: 'write', tier_label: 'T1 工作区写入',
    rule: {source: 'project', tool: 'write_file', decision: 'once', note: '项目要求逐次'}});
  await pause(25);
  const frame = lastFrame() ?? '';
  assert.match(frame, /审批 · T1 工作区写入 · write_file/);
  assert.match(frame, /层级：T1 工作区写入/);
  assert.match(frame, /命中规则：\[project\] write_file → once · 项目要求逐次/);
  assert.ok(frame.split('\n').every(line => stringWidth(line) <= 80));
});

test('fake demo mode prefills an actionable demo prompt', async () => {
  const previous = process.env.VIBE_FAKE_DEMO;
  process.env.VIBE_FAKE_DEMO = '1';
  try {
    const client = new MockClient();
    const {lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 100, rows: 30}}/>);
    await pause(20);
    assert.match(lastFrame() ?? '', /创建 \.vibe\/fake-demo\.txt 并验证/);
  } finally {
    if (previous === undefined) delete process.env.VIBE_FAKE_DEMO;
    else process.env.VIBE_FAKE_DEMO = previous;
  }
});

test('/chat and /act submit run tasks with mode metadata', async () => {
  const client = new MockClient();
  client.capabilityList = ['tasks', 'modes'];
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: ['tasks', 'modes']});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/chat 只聊天');
  assert.deepEqual(client.runTasks[0], {kind: 'run', mode: 'chat', objective: '只聊天'});
  client.emit(completed('run-1'));
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/act 改代码');
  assert.deepEqual(client.runTasks[1], {kind: 'run', mode: 'act', objective: '改代码'});
  assert.match(lastFrame() ?? '', /改代码/);
});

test('/notifications sends backend command', async () => {
  const client = new MockClient();
  client.capabilityList = ['tasks'];
  const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: ['tasks']});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/notifications');
  assert.equal(client.commands.at(-1), 'notifications');
  client.emit({type: 'command_result', id: 'command-1', text: '通知'});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/notifications plan_acceptance');
  assert.equal(client.commands.at(-1), 'notifications');
  assert.deepEqual(client.commandArgs.at(-1), {kind: 'plan_acceptance'});
});

test('/explore sends a read-only subagent command', async () => {
  const client = new MockClient();
  client.capabilityList = ['tasks', 'modes'];
  const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: ['tasks', 'modes']});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/explore 读 README');
  assert.equal(client.commands.at(-1), 'explore');
  assert.deepEqual(client.commandArgs.at(-1), {prompt: '读 README'});
});

test('/plan submits a plan-mode run and /plan renders the latest artifact', async () => {
  const sidebarSignals: SidebarSignalInput[] = [];
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-view-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'execution.v1.json'), JSON.stringify({
      schema_version: 1, task_id: 't-20261103-120000-abcdef', plan_id: 'pl-1', plan_version: 1,
      steps: [{step_id: 's1', status: 'in_progress'}],
    }));
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-1', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'approved', objective: '修复问题',
      acceptance_criteria: [{id: 'ac1', text: '测试通过', confirmed: false}],
      steps: [{step_id: 's1', title: '读代码', kind: 'read'}],
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks', 'modes'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}
      publishSidebarSignal={(_workspace, signal) => sidebarSignals.push(signal)}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks', 'modes']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    assert.match(lastFrame() ?? '', /修复问题/);
    assert.match(lastFrame() ?? '', /候选/);
    assert.match(lastFrame() ?? '', /in_progress/);
    assert.ok(sidebarSignals.every(signal => signal.plan === undefined), 'legacy latest-by-time data cannot establish a scoped sidebar plan');
    stdin.write('\u001b');
    await pause(20);
    await enter(stdin, '/plan all');
    assert.match(lastFrame() ?? '', /步骤（1）/);
    stdin.write('\u001b');
    await pause(20);
    await enter(stdin, '/plan 先规划');
    assert.deepEqual(client.runTasks[0], {kind: 'run', mode: 'plan', objective: '先规划'});
    client.emit(completed('run-1'));
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan approve');
    assert.equal(client.commands.at(-1), 'plan');
    assert.deepEqual(client.commandArgs.at(-1), {action: 'approve'});
    client.emit(completed('command-1'));
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan execute');
    const executed = client.runTasks.at(-1);
    assert.equal(executed?.kind, 'run');
    assert.equal(executed?.mode, 'act');
    assert.equal(executed?.plan_id, 'pl-1');
    assert.equal(executed?.plan_version, 1);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('/plan edit-step sends step title', async () => {
  const client = new MockClient();
  client.capabilityList = ['tasks'];
  const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: ['tasks']});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/plan edit-step s1 新标题');
  assert.deepEqual(client.commandArgs.at(-1), {action: 'edit-step', step_id: 's1', title: '新标题'});
});

test('/plan acceptance sends backend acceptance command', async () => {
  const client = new MockClient();
  client.capabilityList = ['tasks'];
  const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: ['tasks']});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/plan acceptance');
  assert.equal(client.commands.at(-1), 'plan');
  assert.deepEqual(client.commandArgs.at(-1), {action: 'acceptance'});
});

test('/plan diff sends version pair to backend', async () => {
  const client = new MockClient();
  client.capabilityList = ['tasks'];
  const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: ['tasks']});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/plan diff 1 2');
  assert.equal(client.commands.at(-1), 'plan');
  assert.deepEqual(client.commandArgs.at(-1), {action: 'diff', version_a: 1, version_b: 2});
});

test('/plan list sends backend plan list command', async () => {
  const client = new MockClient();
  client.capabilityList = ['tasks'];
  const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: ['tasks']});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/plan list');
  assert.equal(client.commands.at(-1), 'plan');
  assert.deepEqual(client.commandArgs.at(-1), {action: 'list'});
});

test('/plan revise sends objective to backend', async () => {
  const client = new MockClient();
  client.capabilityList = ['tasks'];
  const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: ['tasks']});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/plan revise 新目标');
  assert.equal(client.commands.at(-1), 'plan');
  assert.deepEqual(client.commandArgs.at(-1), {action: 'revise', objective: '新目标'});
});

test('plan overlay approves a proposed plan with y', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-approve-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-2', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'proposed', objective: '待批计划',
      acceptance_criteria: [], steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    stdin.write('y');
    await pause(30);
    assert.equal(client.commands.at(-1), 'plan');
    assert.deepEqual(client.commandArgs.at(-1), {action: 'approve'});
    assert.doesNotMatch(lastFrame() ?? '', /待批计划/);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('plan overlay Enter executes an approved plan', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-execute-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-3', task_id: 't-20261103-120000-abcdef',
      version: 2, status: 'approved', objective: '执行目标',
      acceptance_criteria: [], steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    stdin.write('\r');
    await pause(30);
    const task = client.runTasks.at(-1);
    assert.equal(task?.plan_id, 'pl-3');
    assert.equal(task?.plan_version, 2);
    assert.equal(task?.mode, 'act');
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('/plan execute refuses a stale approved plan', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-stale-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-stale', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'approved', objective: 'stale 目标',
      approval: {stale: true}, acceptance_criteria: [],
      steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan execute');
    assert.equal(client.runTasks.length, 0);
    assert.match(lastFrame() ?? '', /stale/);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('plan overlay s opens selected step detail', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-step-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-step', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'proposed', objective: '步骤目标', acceptance_criteria: [],
      steps: [{step_id: 's1', title: '读代码', kind: 'read'}],
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    for (let i = 0; i < 5; i += 1) {stdin.write('\u001b[B'); await pause(10);}
    assert.match(lastFrame() ?? '', /›\s+- s1/);
    stdin.write('s');
    await pause(30);
    assert.equal(client.commands.at(-1), 'plan');
    assert.deepEqual(client.commandArgs.at(-1), {action: 'step', step_id: 's1'});
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('plan overlay e prefills plan revision command', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-edit-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-edit', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'proposed', objective: '可编辑计划', acceptance_criteria: [],
      steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    stdin.write('e');
    await pause(30);
    assert.match(lastFrame() ?? '', /\/plan revise/);
    await enter(stdin, '新目标');
    assert.equal(client.commands.at(-1), 'plan');
    assert.deepEqual(client.commandArgs.at(-1), {action: 'revise', objective: '新目标'});
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('/plan execute uses latest executable plan when newest is draft', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-fallback-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-approved', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'approved', objective: '已批准',
      acceptance_criteria: [], steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    await writeFile(path.join(taskDir, 'plan.v2.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-approved', task_id: 't-20261103-120000-abcdef',
      version: 2, status: 'draft', objective: '草稿',
      acceptance_criteria: [], steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan execute');
    const task = client.runTasks.at(-1);
    assert.equal(task?.plan_id, 'pl-approved');
    assert.equal(task?.plan_version, 1);
    assert.match(lastFrame() ?? '', /approved v1/);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('plan overlay shows approval metadata', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-approval-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-meta', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'approved', objective: '已批准计划', acceptance_criteria: [],
      approval: {approved_by: 'alice', approved_at: '2026-11-06T12:00:00Z', plan_hash: 'abcdef1234567890'},
      steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    assert.match(lastFrame() ?? '', /批准：alice/);
    assert.match(lastFrame() ?? '', /hash abcdef123456/);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('plan overlay shows saved acceptance result', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-acceptance-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef');
    const planDir = path.join(taskDir, 'plan');
    await mkdir(planDir, {recursive: true});
    await writeFile(path.join(planDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-acc', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'approved', objective: '验收计划', acceptance_criteria: [],
      steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    await writeFile(path.join(taskDir, 'acceptance.v1.json'), JSON.stringify({
      schema_version: 1, plan_version: 1, status: false, codes: ['acceptance_failed'],
      results: {v1: {ok: false}},
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    assert.match(lastFrame() ?? '', /验收结果：✗ 失败/);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('paused acceptance failure shows plan guidance', async () => {
  const client = new MockClient();
  client.capabilityList = ['tasks'];
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: ['tasks']});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '执行计划');
  const id = `run-${client.runs.length}`;
  client.emit({type: 'done', id, result: {
    text: '停止', steps: 1, status: 'stopped', usage: {}, cost_usd: 0, run_id: id,
    stop_reason: 'acceptance_acceptance_failed',
  }});
  await pause(30);
  assert.match(lastFrame() ?? '', /\/plan acceptance/);
  assert.match(lastFrame() ?? '', /\/plan revise/);
});

test('/plan background sends prepare-background command', async () => {
  const client = new MockClient();
  client.capabilityList = ['tasks'];
  const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: ['tasks']});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/plan background');
  assert.equal(client.commands.at(-1), 'plan');
  assert.deepEqual(client.commandArgs.at(-1), {action: 'prepare-background'});
});

test('resumed plan auto-discovery stays quiet when no plan artifact exists until explicitly requested', async () => {
  const {client, stdin, lastFrame} = await mount();
  client.capabilityList = ['plan-review'];
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: ['plan-review'], resumed: true});
  client.emit({type: 'status', phase: 'idle'});
  await pause(30);
  assert.deepEqual(client.commands, ['plan']);
  assert.deepEqual(client.commandArgs[0], {action: 'review'});
  client.emit({type: 'plan_review', id: 'command-1', target: null, outcome: 'unavailable', code: 'not_found',
    message: '找不到计划产物', replacement_target: null, launch: null, summary: null, body_page: '', next_cursor: null,
    available_actions: []});
  await pause(20);
  assert.doesNotMatch(lastFrame() ?? '', /找不到计划产物/, '启动自动查询不应把「没有计划」显示成提示');
  await enter(stdin, '/plan');
  await pause(20);
  assert.equal(client.commands.at(-1), 'plan');
  client.emit({type: 'plan_review', id: 'command-2', target: null, outcome: 'unavailable', code: 'not_found',
    message: '找不到计划产物', replacement_target: null, launch: null, summary: null, body_page: '', next_cursor: null,
    available_actions: []});
  await pause(20);
  assert.match(lastFrame() ?? '', /找不到计划产物/, '显式 /plan 查询仍应给出原因');
});

test('/plan background keeps TUI when worker spawn fails', async () => {
  const client = new MockClient();
  client.capabilityList = ['tasks'];
  const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: ['tasks']});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/plan background');
  const id = `command-${client.commands.length}`;
  client.emit({type: 'command_result', id, text: 'background task prepared: task_id=t-20261106-000000-abcdef plan_id=pl-1 version=1'});
  await pause(30);
  await enter(stdin, '/logs');
  await pause(20);
  assert.match(lastFrame() ?? '', /后台 worker 未启动/);
});

test('plan overlay b sends prepare-background command', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-bg-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-bg', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'approved', objective: '后台',
      acceptance_criteria: [], steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    stdin.write('b');
    await pause(30);
    assert.equal(client.commands.at(-1), 'plan');
    assert.deepEqual(client.commandArgs.at(-1), {action: 'prepare-background'});
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('plan overlay v lists versions', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-versions-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-v', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'proposed', objective: '版本', acceptance_criteria: [],
      steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    stdin.write('v');
    await pause(30);
    assert.deepEqual(client.commandArgs.at(-1), {action: 'list'});
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('plan overlay shows approval review remaining', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-ttl-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    const file = path.join(taskDir, 'plan.v1.json');
    await writeFile(file, JSON.stringify({
      schema_version: 1, plan_id: 'pl-ttl', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'proposed', objective: '时限', acceptance_criteria: [],
      steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    process.env.VIBE_PLAN_REVIEW_TIMEOUT_SECONDS = '3600';
    try {
      await enter(stdin, '/plan');
      assert.match(lastFrame() ?? '', /审批剩余/);
    } finally {
      delete process.env.VIBE_PLAN_REVIEW_TIMEOUT_SECONDS;
    }
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('plan overlay d compares current version with the previous one', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-diff-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-diff', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'draft', objective: '旧目标',
      steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    await writeFile(path.join(taskDir, 'plan.v2.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-diff', task_id: 't-20261103-120000-abcdef',
      version: 2, status: 'approved', objective: '新目标',
      steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    const client = new MockClient();
    const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    stdin.write('d');
    await pause(30);
    assert.deepEqual(client.commandArgs.at(-1), {action: 'diff', version_a: 1, version_b: 2});
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('plan overlay d on the first version only shows a notice', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-diff1-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-diff1', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'proposed', objective: '唯一版本',
      steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    const client = new MockClient();
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    stdin.write('d');
    await pause(30);
    assert.equal(client.commands.filter(command => command === 'plan').length, 0);
    assert.match(lastFrame() ?? '', /d比较/);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('plan overlay t prefills selected step title edit', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-step-edit-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-step-edit', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'proposed', objective: '步骤编辑计划', acceptance_criteria: [],
      steps: [{step_id: 's1', title: '旧标题', kind: 'read'}],
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    for (let i = 0; i < 5; i += 1) {stdin.write('\u001b[B'); await pause(10);}
    assert.match(lastFrame() ?? '', /›\s+- s1/);
    stdin.write('t');
    await pause(30);
    assert.match(lastFrame() ?? '', /\/plan edit-step s1/);
    await enter(stdin, '新标题');
    assert.deepEqual(client.commandArgs.at(-1), {action: 'edit-step', step_id: 's1', title: '新标题'});
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});


test('/trace sends the trace command to the backend', async () => {
  const client = new MockClient();
  const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/trace');
  assert.equal(client.commands.at(-1), 'trace');
});


test('/trace with hours forwards bounded hours argument', async () => {
  const client = new MockClient();
  const {stdin} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/trace 24');
  assert.equal(client.commands.at(-1), 'trace');
  assert.deepEqual(client.commandArgs.at(-1), {hours: 24});
});


test('/trace rejects invalid hours without sending a command', async () => {
  const client = new MockClient();
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  const {stdin} = view;
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/trace abc');
  assert.match(String(view.lastFrame() ?? ''), /用法：\/trace/);
  assert.equal(client.commands.includes('trace'), false);
});


test('/priority forwards task id and priority', async () => {
  const client = new MockClient();
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  const {stdin} = view;
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/priority t-123 9');
  assert.equal(client.commands.at(-1), 'priority');
  assert.deepEqual(client.commandArgs.at(-1), {task_id: 't-123', priority: 9});
});

test('/priority rejects invalid arguments without sending a command', async () => {
  const client = new MockClient();
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  const {stdin} = view;
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/priority t-123 900');
  assert.match(String(view.lastFrame() ?? ''), /用法：\/priority/);
  assert.equal(client.commands.includes('priority'), false);
});


test('/queue sends the queue command to the backend', async () => {
  const client = new MockClient();
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  const {stdin} = view;
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/queue');
  assert.equal(client.commands.at(-1), 'queue');
});


test('/plan kind sends step kind edit', async () => {
  const client = new MockClient();
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  const {stdin} = view;
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/plan kind s1 verify');
  assert.deepEqual(client.commandArgs.at(-1), {action: 'edit-step', step_id: 's1', kind: 'verify'});
});

test('/plan kind rejects unknown kind without sending a command', async () => {
  const client = new MockClient();
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  const {stdin} = view;
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/plan kind s1 bogus');
  assert.match(String(view.lastFrame() ?? ''), /read\|edit\|run\|verify/);
  assert.equal(client.commands.includes('plan'), false);
});


test('/plan files forwards step file scope', async () => {
  const client = new MockClient();
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  const {stdin} = view;
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/plan files s1 a.ts,b.ts');
  assert.deepEqual(client.commandArgs.at(-1), {action: 'edit-step', step_id: 's1', files: ['a.ts', 'b.ts']});
});


test('/plan depends forwards step dependencies', async () => {
  const client = new MockClient();
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  const {stdin} = view;
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/plan depends s2 s1');
  assert.deepEqual(client.commandArgs.at(-1), {action: 'edit-step', step_id: 's2', depends_on: ['s1']});
});


test('plan overlay c opens approval card', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-card-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-card', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'proposed', objective: '审批卡计划', acceptance_criteria: [],
      steps: [{step_id: 's1', title: '读', kind: 'read'}],
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    for (let i = 0; i < 5; i += 1) {stdin.write('\u001b[B'); await pause(10);}
    assert.match(lastFrame() ?? '', /›\s+- s1/);
    stdin.write('c');
    await pause(30);
    assert.deepEqual(client.commandArgs.at(-1), {action: 'card'});
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});


test('/plan card sends approval card command', async () => {
  const client = new MockClient();
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
  const {stdin} = view;
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: []});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  await enter(stdin, '/plan card');
  assert.deepEqual(client.commandArgs.at(-1), {action: 'card'});
});


for (const [input, expected] of [
  ['/plan on', {action: 'mode-on'}],
  ['/plan off', {action: 'mode-off'}],
  ['/plan status', {action: 'mode-status'}],
  ['/plan dismiss', {action: 'dismiss'}],
  ['/plan markdown', {action: 'markdown'}],
] as const) {
  test(`${input} forwards the plan mode action without starting a plan run`, async () => {
    const {client, stdin} = await mount();
    await enter(stdin, input);
    assert.equal(client.commands.at(-1), 'plan');
    assert.deepEqual(client.commandArgs.at(-1), expected);
    assert.deepEqual(client.runs, []);
  });
}

test('/plan request-changes requires feedback and forwards it to the backend', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, '/plan request-changes');
  assert.match(lastFrame() ?? '', /用法：\/plan request-changes/);
  assert.equal(client.commands.length, 0);
  assert.match(lastFrame() ?? '', /› \/plan request-changes/);
  await enter(stdin, ' 补充失败路径测试');
  assert.equal(client.commands.at(-1), 'plan');
  assert.deepEqual(client.commandArgs.at(-1), {action: 'request-changes', feedback: '补充失败路径测试'});
});

test('one-off act displays the actual run mode separately from the next default', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  client.capabilityList = ['tasks'];
  client.emit({type: 'plan_mode', mode: {active: true, pending: false, wanted: null}}); await pause(20);
  await enter(stdin, '/act 修复标题');
  client.emit({type: 'task_state', id: 'run-1', task: {task_id: 't-one', kind: 'run', status: 'running', mode: 'act',
    segment: 1, objective: '修复标题', goal_id: null, updated_at: ''}}); await pause(20);
  assert.match(lastFrame() ?? '', /本次：执行.*下次：只读规划/);
  client.emit({type: 'task_state', id: 'other-run', task: runningTask('chat')}); await pause(20);
  assert.match(lastFrame() ?? '', /本次：执行/);
  client.emit({type: 'task_state', id: 'run-1', task: runningTask('constructor')}); await pause(20);
  assert.doesNotMatch(lastFrame() ?? '', /本次：|function|计划模式 ·/);
  client.emit(completed('run-1')); await pause(20);
  client.emit({type: 'task_state', id: 'run-1', task: runningTask('act')}); await pause(20);
  assert.doesNotMatch(lastFrame() ?? '', /本次：执行/);
  assert.match(lastFrame() ?? '', /下次：只读规划/);
  assert.equal(client.runTasks[0]?.mode, 'act');
  assert.deepEqual(client.commands, []);
});

test('changing the next default during a run waits for the backend and keeps the cancellation target', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  client.capabilityList = ['tasks', 'plan-mode-control'];
  client.emit({type: 'plan_mode', mode: {active: true, pending: false, wanted: null}}); await pause(20);
  await enter(stdin, '/act 修复标题');
  client.emit({type: 'task_state', id: 'run-1', task: runningTask('act')}); await pause(20);
  await enter(stdin, '/plan off');
  assert.deepEqual(client.commandArgs, [{action: 'mode-off'}]);
  assert.match(lastFrame() ?? '', /本次：执行.*下次：只读规划/);
  assert.match(lastFrame() ?? '', /› \/plan off/);
  stdin.write('\u0015'); await pause(15); stdin.write('新的中文草稿'); await pause(15);
  client.emit({type: 'plan_mode', mode: {active: true, pending: true, wanted: false}});
  client.emit({type: 'command_result', id: 'command-1', command: 'plan', text: '默认模式将在当前运行结束后生效'}); await pause(20);
  assert.match(lastFrame() ?? '', /本次：执行.*下次：执行.*待生效/);
  assert.match(lastFrame() ?? '', /新的中文草稿/);
  stdin.write('\u0003'); await pause(20);
  assert.deepEqual(client.cancellations, ['run-1']);
  client.emit({type: 'plan_mode', mode: {active: false, pending: false, wanted: null}});
  client.emit(completed('run-1', 'cancelled')); await pause(20);
  assert.doesNotMatch(lastFrame() ?? '', /本次：执行|下次：执行（待生效）/);
  assert.match(lastFrame() ?? '', /新的中文草稿/);
  assert.deepEqual(client.runs, ['修复标题']);
});

for (const code of ['default_mode_unavailable', 'session_busy', 'session_unavailable']) {
test(`a task rejected before starting restores its input and leaves the UI idle: ${code}`, async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  await enter(stdin, '保留这份中文任务');
  client.emit({type: 'error', id: 'run-1', code, message: '任务未启动，请核对会话后重试'}); await pause(20);
  assert.match(lastFrame() ?? '', /› 保留这份中文任务/);
  assert.doesNotMatch(lastFrame() ?? '', /Enter 提交纠偏/);
  assert.equal(client.runs.length, 1);
  stdin.write('\u0015'); await pause(20); await enter(stdin, '/cost');
  assert.deepEqual(client.commands, ['cost']);
});
}

test('session plan mode state is visible in the pane title and hint', async () => {
  const {client, lastFrame} = await mount(undefined, undefined, {columns: 120, rows: 40});
  assert.doesNotMatch(lastFrame() ?? '', /计划模式/);
  client.emit({type: 'plan_mode', mode: {active: false, pending: true, wanted: true}});
  await pause(25);
  assert.match(lastFrame() ?? '', /下次：只读规划（待生效）/);
  client.emit({type: 'plan_mode', mode: {active: true, pending: false, wanted: true}});
  await pause(25);
  assert.match(lastFrame() ?? '', /计划模式/);
  assert.match(lastFrame() ?? '', /只读规划/);
  assert.match(lastFrame() ?? '', /\/plan off/);
  assert.doesNotMatch(lastFrame() ?? '', /下次：只读规划（待生效）/);
  client.emit({type: 'plan_mode', mode: {active: false, pending: false, wanted: false}});
  await pause(25);
  assert.doesNotMatch(lastFrame() ?? '', /计划模式/);
});

test('restored session explains its next mode and unsent draft without starting a task', async () => {
  const {client, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 'restored', capabilities: [], resumed: true});
  client.emit({type: 'plan_mode', mode: {active: true, pending: false, wanted: null}});
  client.emit({type: 'status', phase: 'idle'}); await pause(25);
  assert.match(lastFrame() ?? '', /会话已恢复，尚未运行/);
  assert.match(lastFrame() ?? '', /请重新输入未发送的草稿/);
  assert.match(lastFrame() ?? '', /下次：只读规划/);
  assert.deepEqual(client.runs, []); assert.deepEqual(client.commands, []);
});

test('mode display waits for state and ignores late confirmation after disconnect', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  client.capabilityList = ['tasks', 'plan-mode-control'];
  assert.doesNotMatch(lastFrame() ?? '', /下次：/);
  await enter(stdin, '/plan on');
  client.emit({type: 'closed'}); await pause(20);
  client.emit({type: 'plan_mode', mode: {active: true, pending: false, wanted: null}});
  client.emit({type: 'command_result', id: 'command-1', command: 'plan', text: '计划模式已开启'}); await pause(20);
  assert.match(lastFrame() ?? '', /结果待确认/);
  assert.match(lastFrame() ?? '', /› \/plan on/);
  assert.doesNotMatch(lastFrame() ?? '', /下次：|计划模式已开启/);
  assert.deepEqual(client.runs, []);
});

test('an unknown mode result can be queried and its late error cannot replace the new result', async () => {
  const {client, stdin, lastFrame} = await mount(undefined, undefined, {columns: 80, rows: 24});
  client.capabilityList = ['tasks', 'plan-mode-control'];
  await enter(stdin, '/plan on');
  await pause(15_050);
  assert.match(lastFrame() ?? '', /结果待确认/);
  assert.match(lastFrame() ?? '', /› \/plan on/);
  stdin.write('\u0015'); await pause(15); await enter(stdin, '/plan status');
  client.emit({type: 'error', id: 'command-1', code: 'plan_mode_error', message: '迟到的旧错误'}); await pause(20);
  assert.doesNotMatch(lastFrame() ?? '', /迟到的旧错误/);
  assert.match(lastFrame() ?? '', /› \/plan status/);
  client.emit({type: 'plan_mode', mode: {active: true, pending: false, wanted: null}});
  client.emit({type: 'command_result', id: 'command-2', command: 'plan', text: '已查询当前模式'}); await pause(20);
  assert.match(lastFrame() ?? '', /下次：只读规划/);
  assert.doesNotMatch(lastFrame() ?? '', /› \/plan status/);
  assert.deepEqual(client.runs, []);
});

test('a narrow terminal distinguishes current mode, queued default and restored input', async () => {
  const {client, stdin, lastFrame, unmount} = await mount(undefined, undefined, {columns: 32, rows: 12});
  client.capabilityList = ['tasks'];
  await enter(stdin, '/act 修复');
  client.emit({type: 'task_state', id: 'run-1', task: runningTask('act')});
  client.emit({type: 'plan_mode', mode: {active: false, pending: true, wanted: true}}); await pause(20);
  assert.match(lastFrame() ?? '', /执行中 · 结束后规划/);
  client.emit(completed('run-1'));
  unmount();
  const restored = await mount(undefined, undefined, {columns: 32, rows: 12});
  restored.client.emit({type: 'ready', workspace: '/repo', model: 'fake', session_id: 'restored', capabilities: [], resumed: true});
  restored.client.emit({type: 'plan_mode', mode: {active: true, pending: false, wanted: null}});
  restored.client.emit({type: 'status', phase: 'idle'}); await pause(20);
  const frame = restored.lastFrame() ?? '';
  assert.match(frame, /下次规划/);
  assert.match(frame, /已恢复，尚未运行/);
  assert.match(frame, /未发送草稿需重输/);
  assert.ok(frame.split('\n').length <= 12);
  assert.ok(frame.split('\n').every(line => stringWidth(line) <= 32));
});

test('plan overlay r prefills request-changes feedback and m sends markdown review', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-plan-review-keys-'));
  try {
    const taskDir = path.join(dir, 'tasks', 't-20261103-120000-abcdef', 'plan');
    await mkdir(taskDir, {recursive: true});
    await writeFile(path.join(taskDir, 'plan.v1.json'), JSON.stringify({
      schema_version: 1, plan_id: 'pl-review', task_id: 't-20261103-120000-abcdef',
      version: 1, status: 'proposed', objective: '待审阅计划', acceptance_criteria: [],
      steps: [{step_id: 's1', title: '读代码', kind: 'read'}],
    }));
    const client = new MockClient();
    client.capabilityList = ['tasks'];
    const {stdin, lastFrame} = render(<App client={client as unknown as BackendClient} windowSize={{columns: 120, rows: 40}}/>);
    await pause(20);
    client.emit({type: 'ready', workspace: '/tmp/repo', model: 'fake', session_id: 'test',
      session_path: dir, capabilities: ['tasks']});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    assert.match(lastFrame() ?? '', /r提反馈/);
    assert.match(lastFrame() ?? '', /m审阅稿/);
    stdin.write('r');
    await pause(30);
    assert.match(lastFrame() ?? '', /\/plan request-changes/);
    await enter(stdin, '补充失败路径测试');
    assert.equal(client.commands.at(-1), 'plan');
    assert.deepEqual(client.commandArgs.at(-1), {action: 'request-changes', feedback: '补充失败路径测试'});
    client.emit({type: 'command_result', id: 'command-1', command: 'plan', text: '已提交反馈'});
    client.emit({type: 'status', phase: 'idle'});
    await pause(20);
    await enter(stdin, '/plan');
    stdin.write('m');
    await pause(30);
    assert.equal(client.commands.at(-1), 'plan');
    assert.deepEqual(client.commandArgs.at(-1), {action: 'markdown'});
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});
