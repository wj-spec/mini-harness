import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {App} from '../src/app.js';
import {initialState, reduceView} from '../src/view-state.js';
import {agentStatus} from '../src/agent-status.js';
import {parseServerEvent, type ServerEvent} from '../src/protocol.js';
import type {InspectionSnapshot} from '../src/inspection.js';
import type {SidebarSignalInput} from '../src/sidebar/signal.js';

afterEach(cleanup);
const cursor = '00000000000000000001-' + 'a'.repeat(64) + '.json';
const snapshot = (requestId = 'old'): InspectionSnapshot => ({
  workspace: '/tmp/history-test', sessionId: 'history-session',
  scope: {requestId, traceRunId: 'trace-old', taskId: null, segment: null, taskStatus: null, status: 'finished', objective: '修复旧问题'},
  plan: null, files: ['src/example.ts'],
  verification: {requestId, status: 'stopped', stopReason: 'verification_failed', verified: false, completionAllowed: false,
    completionMessages: [], omittedCommands: 0, commands: [{id: 'c1', command: 'exit 2', verification: true, pending: false,
      ok: false, execution: {status: 'failed', exit_code: 2}, output: '失败证据', truncated: false}]},
});
const event = (id: string, records = [snapshot()], extra = {}) => ({v: 1, type: 'inspection_history', id,
  workspace: '/tmp/history-test', session_id: 'history-session', records, next_cursor: null, skipped: 0, ...extra});

test('history protocol validates nested facts and rejects cross-request or cross-session snapshots', () => {
  assert.ok(parseServerEvent(JSON.stringify(event('query'))));
  const wrong = snapshot(); wrong.verification.requestId = 'another';
  assert.throws(() => parseServerEvent(JSON.stringify(event('query', [wrong]))));
  assert.throws(() => parseServerEvent(JSON.stringify(event('query', [snapshot()], {session_id: 'another'}))));
  const bad = snapshot(); bad.verification.commands[0]!.execution = {status: 'succeeded', exit_code: 2};
  assert.throws(() => parseServerEvent(JSON.stringify(event('query', [bad]))));
  assert.throws(() => parseServerEvent(JSON.stringify(event('query', [snapshot()], {next_cursor: '../outside'}))));
});

class Client {
  listeners = new Set<(event: ServerEvent) => void>();
  commands: {command: string; args?: unknown}[] = [];
  runs: string[] = []; cancellations: string[] = [];
  capabilities() {return ['inspection-history', 'plan-review'];}
  subscribe(fn: (event: ServerEvent) => void) {this.listeners.add(fn); return () => this.listeners.delete(fn);}
  command(command: string, args?: unknown) {this.commands.push({command, args}); return `q-${this.commands.length}`;}
  run(text: string) {this.runs.push(text); return 'live-run';}
  cancel(id: string) {this.cancellations.push(id); return 'cancel';}
  approve(): string {throw new Error('history cannot approve');}
  start() {}
  steer() {return 'steer';}
  async shutdown() {}
  emit(value: unknown) {for (const fn of this.listeners) fn(value as ServerEvent);}
}
async function mount(columns = 100, rows = 30, attach = false) {
  const client = new Client(); const signals: SidebarSignalInput[] = [];
  const view = render(<App client={client} attach={attach} initialState={{...initialState, phase: 'idle', workspace: '/tmp/history-test',
    sessionId: 'history-session', resumed: true}} windowSize={{columns, rows}}
    launchSidebar={() => ({outcome: 'started', message: 'test host'})}
    publishSidebarSignal={(_workspace, value) => signals.push(value)}/>);
  const key = async (text: string) => {view.stdin.write(text); await pause(30);};
  const click = async (label: string) => {
    await pause(30); const lines = view.lastFrame()!.split('\n'); const row = lines.findIndex(line => line.includes(label));
    assert.ok(row >= 0, view.lastFrame());
    const col = stringWidth(lines[row]!.slice(0, lines[row]!.indexOf(label))) + 2;
    await key(`\x1b[<0;${col};${row + 1}M\x1b[<0;${col};${row + 1}m`);
  };
  await pause(40); return {...view, client, signals, key, click};
}

async function selectedResult(signals: SidebarSignalInput[], requestId: string) {
  const deadline = Date.now() + 2000;
  while ((signals.at(-1)?.navigation?.mode !== 'result' || signals.at(-1)?.run?.requestId !== requestId) && Date.now() < deadline) await pause(5);
  assert.equal(signals.at(-1)?.navigation?.mode, 'result');
  assert.equal(signals.at(-1)?.run?.requestId, requestId);
}

test('restored results open through mouse buttons and keep draft cursor and running cancel target', async () => {
  const view = await mount();
  await view.key('中文草稿'); await view.key('\x1b[D');
  await view.click('历史结果');
  assert.equal(view.client.commands[0]?.command, 'inspections');
  view.client.emit(event('q-1')); await pause(40);
  assert.match(view.lastFrame()!, /修复旧问题/);
  await view.click('[ 验证 ]');
  await selectedResult(view.signals, 'old');
  assert.equal(view.signals.at(-1)?.verification?.commands[0]?.execution?.exit_code, 2);
  await view.key('中'); assert.match(view.lastFrame()!, /中文草中稿/);
  await view.key('\x1b[F'); await view.key('\x15'); await view.key('新任务'); await view.key('\r');
  await view.key('/inspections'); await view.key('\r');
  assert.equal(view.client.commands[1]?.command, 'inspections');
  view.client.emit(event('q-2')); await pause(30);
  await view.key('\x03'); assert.deepEqual(view.client.cancellations, ['live-run']);
  assert.equal(view.client.runs.length, 1);
});

const attached = (status = 'running'): ServerEvent => ({v: 1, type: 'task_snapshot', id: null,
  task: {task_id: 'bound-task', kind: 'run', status, segment: 1, run_id: 'attached-run', objective: '绑定任务',
    goal_id: null, worktree: null}, approvals: []} as ServerEvent);
const offlineReady: ServerEvent = {v: 1, type: 'ready', id: null, workspace: '/tmp/history-test',
  session_id: 'history-session', model: 'fake', capabilities: ['attach', 'attach-offline', 'inspection-history', 'plan-review']};
const boundSnapshot = () => {const value = snapshot(); value.scope.taskId = 'bound-task'; value.scope.segment = 1; return value;};

test('attach history stays task-scoped across pagination and rejects foreign task receipts', async () => {
  const view = await mount(100, 30, true);
  view.client.emit(attached()); await pause(30); await view.click('历史结果');
  assert.deepEqual(view.client.commands[0], {command: 'inspections', args: {task_id: 'bound-task'}});
  view.client.emit(event('q-1', [boundSnapshot()], {task_id: 'bound-task', next_cursor: cursor})); await pause(30);
  await view.click('更早');
  assert.deepEqual(view.client.commands[1]?.args, {task_id: 'bound-task', cursor});
  view.client.emit(event('q-2', [snapshot('foreign')], {task_id: 'bound-task'})); await pause(30);
  assert.match(view.lastFrame()!, /不匹配/);
  await view.click('重试'); assert.deepEqual(view.client.commands[2]?.args, {task_id: 'bound-task', cursor});
  view.client.emit(event('q-3', [boundSnapshot()], {task_id: 'bound-task'})); await pause(30);
  await view.click('[ 验证 ]');
  await selectedResult(view.signals, 'old');
  assert.equal(view.signals.at(-1)?.run?.taskId, 'bound-task');
  assert.equal(view.client.runs.length, 0);
});

test('offline attach retains draft and cursor, reads history and never starts a run', async () => {
  const view = await mount(100, 30, true);
  await view.key('中文草稿'); await view.key('\x1b[D');
  view.client.emit(offlineReady); view.client.emit(attached()); await pause(40);
  assert.match(view.lastFrame()!, /离线只读/);
  await view.key('\r'); assert.equal(view.client.runs.length, 0);
  await view.key('中'); assert.match(view.lastFrame()!, /中文草中稿/);
  await view.click('[ 历史结果 ]');
  view.client.emit(event('q-1', [boundSnapshot()], {task_id: 'bound-task'})); await pause(30);
  await view.click('[ 验证 ]');
  await selectedResult(view.signals, 'old');
  assert.equal(view.signals.at(-1)?.verification?.commands[0]?.execution?.exit_code, 2);
  await view.key('\x03'); assert.equal(view.client.cancellations.length, 0);
  assert.equal(view.client.runs.length, 0);
});

test('authoritative attach snapshot preserves replay evidence; offline stale running has no live target', () => {
  let state = reduceView(initialState, {type: 'event', event: attached()});
  state = reduceView(state, {type: 'event', event: {v: 1, type: 'done', id: 'attached-run',
    result: {status: 'done', stop_reason: 'completed', verified: true, text: '完成', steps: 0,
      usage: {prompt_tokens: 0, completion_tokens: 0, total_tokens: 0}, cost_usd: 0, run_id: 'trace'}}});
  state = reduceView(state, {type: 'event', event: attached('completed')});
  assert.equal(state.activeId, null);
  assert.equal(state.inspection?.result?.verified, true);
  state = reduceView(state, {type: 'event', event: offlineReady});
  state = reduceView(state, {type: 'event', event: attached()});
  assert.equal(state.activeId, null); assert.equal(state.phase, 'idle');
  assert.equal(state.inspection?.scope.status, 'unknown');
  assert.equal(agentStatus(state).label, '离线只读');
});

test('history pagination rejects wrong scope, retries the same cursor and ignores late replies', async () => {
  const view = await mount(); await view.click('历史结果');
  view.client.emit(event('q-1', [snapshot()], {next_cursor: cursor})); await pause(30);
  await view.click('更早');
  assert.deepEqual(view.client.commands[1], {command: 'inspections', args: {cursor}});
  view.client.emit(event('q-2', [snapshot('foreign')], {session_id: 'foreign'})); await pause(30);
  assert.match(view.lastFrame()!, /不匹配/);
  await view.click('重试'); assert.deepEqual(view.client.commands[2]?.args, {cursor});
  view.client.emit(event('q-2', [snapshot('late')]));
  view.client.emit(event('q-3', [snapshot('older')], {skipped: 1})); await pause(30);
  assert.match(view.lastFrame()!, /跳过 1/);
  await view.click('[ 验证 ]'); await selectedResult(view.signals, 'older');
  assert.equal(view.client.runs.length, 0);
});

test('historical worktree origin follows the selected result and returning current restores the project origin', async () => {
  const view = await mount();
  const project = {root: '/tmp/history-test', identity: '1:2'};
  const worktree = {root: '/tmp/history-test/.vibe/worktrees/task', identity: '1:3'};
  view.client.emit({v: 1, type: 'ready', id: null, workspace: project.root, session_id: 'history-session',
    model: 'fake', capabilities: ['inspection-history'], file_source: project});
  view.client.emit({v: 1, type: 'status', id: null, phase: 'idle'}); await pause(40);
  await view.click('[ 历史结果 ]');
  const saved = snapshot(); saved.fileSource = worktree;
  view.client.emit(event('q-1', [saved])); await pause(40);
  await view.click('[ 变更 ]');
  await selectedResult(view.signals, 'old');
  assert.deepEqual(view.signals.at(-1)?.fileSource, worktree);
  await view.click('回到当前');
  const deadline = Date.now() + 2000;
  while (view.signals.at(-1)?.navigation?.mode === 'result' && Date.now() < deadline) await pause(5);
  assert.deepEqual(view.signals.at(-1)?.fileSource, project);
  assert.equal(view.client.runs.length, 0);
});

test('narrow history offers keyboard result selection and never presents an unfinished run as verified', async () => {
  const view = await mount(32, 12); await view.click('[ 历史 ]');
  const pending = snapshot(); pending.scope.status = 'unknown'; pending.verification.status = null;
  pending.verification.verified = null; pending.verification.commands[0]!.pending = true;
  pending.verification.commands[0]!.execution = null;
  view.client.emit(event('q-1', [pending])); await pause(30);
  await view.key('\t'); await view.key('\r');
  await selectedResult(view.signals, 'old');
  assert.equal(view.signals.at(-1)?.run?.status, 'unknown');
  assert.equal(view.signals.at(-1)?.verification?.verified, null);
  assert.equal(view.signals.at(-1)?.navigation?.page, 'verification');
  assert.equal(view.client.runs.length, 0);
});

test('switching sessions discards cached history and late replies from the old scope', async () => {
  const view = await mount(); await view.click('历史结果'); view.client.emit(event('q-1')); await pause(30);
  view.client.emit({v: 1, type: 'ready', id: null, workspace: '/tmp/new-history', session_id: 'new-session', model: 'fake', capabilities: ['inspection-history']});
  view.client.emit({v: 1, type: 'status', id: null, phase: 'idle'}); await pause(40);
  assert.doesNotMatch(view.lastFrame()!, /历史结果 · 第 1 页/);
  await view.click('历史结果'); view.client.emit(event('q-1')); await pause(30);
  assert.doesNotMatch(view.lastFrame()!, /修复旧问题/);
  view.client.emit(event('q-2', [], {workspace: '/tmp/new-history', session_id: 'new-session'})); await pause(30);
  assert.match(view.lastFrame()!, /没有保存的检查记录/);
});
