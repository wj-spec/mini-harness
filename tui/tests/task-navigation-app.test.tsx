import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {App} from '../src/app.js';
import {initialState} from '../src/view-state.js';
import type {BackendClient, TaskConnectionTarget} from '../src/backend.js';
import type {BackendCommand, CommandArgs, ServerEvent, TaskCatalogRow} from '../src/protocol.js';

afterEach(cleanup);
const row = (id: string, text: string): TaskCatalogRow => ({task_id: id, objective: text, kind: 'run', mode: 'act', status: 'paused', priority: 0,
  created_at: '2026-11-09T00:00:00Z', updated_at: '2026-11-09T00:01:00Z', worker: null, worker_error: null, pending_approvals: 0});
class Client implements BackendClient {
  listeners = new Set<(event: ServerEvent) => void>(); commands: {id: string; command: BackendCommand; args?: CommandArgs}[] = [];
  runs: string[] = []; cancellations: string[] = [];
  approvals: string[] = [];
  replay: ServerEvent[] = []; shutdowns = 0;
  connections: {target: TaskConnectionTarget; signal?: AbortSignal; resolve: (client: BackendClient) => void; reject: (error: Error) => void}[] = [];
  caps = ['task-navigation', 'inspection-history'];
  capabilities() {return this.caps;} start() {}
  connectTask(target: TaskConnectionTarget, signal?: AbortSignal) {return new Promise<BackendClient>((resolve, reject) => this.connections.push({target, signal, resolve, reject}));}
  subscribe(fn: (event: ServerEvent) => void) {this.listeners.add(fn); queueMicrotask(() => {if (this.listeners.has(fn)) this.replay.forEach(fn);}); return () => this.listeners.delete(fn);}
  run(text: string) {this.runs.push(text); return `run-${this.runs.length}`;}
  command(command: BackendCommand, args?: CommandArgs) {const id = `command-${this.commands.length + 1}`; this.commands.push({id, command, args}); return id;}
  cancel(target: string) {this.cancellations.push(target); return 'cancel';} steer() {return 'steer';} approve(id: string) {this.approvals.push(id); return 'approval';}
  async shutdown() {this.shutdowns += 1;}
  emit(event: unknown) {for (const fn of this.listeners) fn(event as ServerEvent);}
  page(id: string, records: TaskCatalogRow[], next: string | null = null) {
    this.emit({v: 1, type: 'task_catalog', id, workspace: '/workspace', session_id: 'session', records, next_cursor: next, skipped: 0, problems: []});
  }
}

test('clicking a task connects inside the workbench and keeps each draft and cursor when returning', async () => {
  const view = await mount(); await view.key('原会话草稿'); await view.key('\x1b[D'); await view.click('[ 任务 ]');
  const task = {...row('t-selected', '待审批的后台任务'), controls: {attach: 'live' as const, attach_reason: null, stop: true, stop_reason: null}};
  view.client.page('command-1', [task]); await pause(40); await view.click('[ 接入任务 ]');
  assert.deepEqual(view.client.connections[0]!.target, {workspace: '/workspace', sessionId: 'session', taskId: 't-selected'});
  const selected = new Client(); selected.caps = ['attach', 'run', 'cancel', 'steer', 'inspection-history'];
  selected.replay = [{v: 1, type: 'ready', id: null, workspace: '/workspace', session_id: 'session', model: 'fake', capabilities: selected.caps},
    {v: 1, type: 'task_snapshot', id: null, task: {task_id: 't-selected', kind: 'run', status: 'running', segment: 1, run_id: 'worker-run',
      objective: '待审批的后台任务', goal_id: null, mode: 'act', plan_id: null, plan_version: null, worktree: null}, approvals: []}];
  view.client.connections[0]!.resolve(selected); await pause(80);
  assert.match(view.lastFrame()!, /返回原会话/); assert.equal(view.client.shutdowns, 0);
  await view.key('任务草稿'); await view.key('\x1b[D'); await view.click('[ 返回原会话 ]');
  assert.equal(selected.shutdowns, 1); await view.click('[ 返回 ]'); await view.key('中');
  assert.match(view.lastFrame()!, /原会话草中稿/);
  await view.click('[ 任务 ]'); view.client.page('command-2', [task]); await pause(40); await view.click('[ 接入任务 ]');
  const reopened = new Client(); reopened.caps = selected.caps; reopened.replay = selected.replay;
  view.client.connections[1]!.resolve(reopened); await pause(80); await view.key('中');
  assert.match(view.lastFrame()!, /任务草中稿/); assert.equal(view.client.runs.length, 0); assert.equal(reopened.runs.length, 0);
});

test('a selected worker receives approval and stop by its fresh run identity while the original session stays untouched', async () => {
  const view = await mount(); await view.click('[ 任务 ]');
  const task = {...row('t-selected', '所选后台任务'), controls: {attach: 'live' as const, attach_reason: null, stop: true, stop_reason: null}};
  view.client.page('command-1', [task]); await pause(40); await view.click('[ 接入任务 ]');
  const child = new Client(); child.caps = ['attach', 'run', 'cancel', 'approval_once'];
  child.replay = [{v: 1, type: 'ready', id: null, workspace: '/workspace', session_id: 'session', model: 'fake', capabilities: child.caps},
    {v: 1, type: 'task_snapshot', id: null, task: {task_id: 't-selected', kind: 'run', status: 'running', segment: 2, run_id: 'fresh-run',
      objective: '所选后台任务', goal_id: null, mode: 'act', plan_id: null, plan_version: null, worktree: null}, approvals: []},
    {v: 1, type: 'approval_request', id: 'fresh-run', approval_id: 'selected-approval', call: {id: 'write', name: 'write_file', args: {path: 'x', content: 'hello'}}, preview: 'hello', decisions: ['once', 'deny']}];
  view.client.connections[0]!.resolve(child); await pause(80); await view.click('[ 允许本次写入 ]');
  assert.deepEqual(child.approvals, ['selected-approval']); assert.deepEqual(view.client.approvals, []);
  await view.click('[ 返回原会话 ]'); await view.click('[ 停止此任务 ]');
  const control = new Client(); control.caps = child.caps; control.replay = child.replay.slice(0, 2);
  view.client.connections[1]!.resolve(control); await pause(80);
  assert.deepEqual(control.cancellations, ['fresh-run']); assert.deepEqual(view.client.cancellations, []);
  control.emit(child.replay[1]); await pause(40); assert.equal(control.cancellations.length, 1);
});

test('switching during a run waits for its terminal receipt and does not consume the original draft', async () => {
  const view = await mount(); await view.key('原任务'); await view.key('\r'); await view.key('中文草稿'); await view.key('\x1b[D');
  await view.click('[ 任务 ]');
  view.client.page('command-1', [{...row('t-other', '要接入的任务'), controls: {attach: 'live', attach_reason: null, stop: true, stop_reason: null}}]);
  await pause(40); await view.click('[ 接入任务 ]'); await view.click('[ 停止当前并接入 ]');
  assert.deepEqual(view.client.cancellations, ['run-1']); assert.equal(view.client.connections.length, 0);
  view.client.emit({v: 1, type: 'ack', id: 'cancel', request_type: 'cancel'}); await pause(40);
  assert.equal(view.client.connections.length, 0);
  view.client.emit({v: 1, type: 'done', id: 'run-1', result: {status: 'cancelled', summary: '', steps: 1, input_tokens: 0, output_tokens: 0, cost: 0}});
  await pause(40); assert.equal(view.client.connections.length, 1); assert.equal(view.client.connections[0]!.target.taskId, 't-other');
  view.client.connections[0]!.reject(new Error('另一个窗口已接管')); await pause(40);
  assert.match(view.lastFrame()!, /接入失败/); await view.click('[ 返回 ]'); await view.key('中');
  assert.match(view.lastFrame()!, /中文草中稿/); assert.deepEqual(view.client.runs, ['原任务']);
});

test('cancelled pending connections cannot steal focus and a stop is refused after the worker becomes offline', async () => {
  const view = await mount(32, 12); await view.key('原草稿'); await view.click('[ 任务 ]');
  const task = {...row('t-selected', '选中任务'), controls: {attach: 'live' as const, attach_reason: null, stop: true, stop_reason: null}};
  view.client.page('command-1', [task]); await pause(40); await view.click('[ 接入任务 ]'); await view.key('\x1b');
  assert.equal(view.client.connections[0]!.signal?.aborted, true);
  const late = new Client(); view.client.connections[0]!.resolve(late); await pause(40); assert.equal(late.shutdowns, 1);
  assert.match(view.lastFrame()!, /原草稿/); await view.click('[ 任务 ]'); view.client.page('command-2', [task]); await pause(40); await view.click('[ 停止此任务 ]');
  const offline = new Client(); offline.caps = ['attach', 'attach-offline', 'inspection-history'];
  offline.replay = [{v: 1, type: 'ready', id: null, workspace: '/workspace', session_id: 'session', model: 'fake', capabilities: offline.caps},
    {v: 1, type: 'task_snapshot', id: null, task: {task_id: 't-selected', kind: 'run', status: 'running', segment: 1, run_id: 'old-run',
      objective: '选中任务', goal_id: null, mode: 'act', plan_id: null, plan_version: null, worktree: null}, approvals: []}];
  view.client.connections[1]!.resolve(offline); await pause(80);
  assert.deepEqual(offline.cancellations, []); assert.equal(view.lastFrame()!.split('\n').length, 12);
  assert.match(view.lastFrame()!, /离线只读/);
  await view.click('[ 返回原会话 ]'); await view.click('[ 返回 ]'); assert.match(view.lastFrame()!, /原草稿/);
});

test('attach now uses the in-workbench connection without shutting down the original client', async () => {
  const view = await mount();
  view.client.emit({v: 1, type: 'task_state', id: null, task: {task_id: 't-known', kind: 'run', status: 'paused', segment: 1, goal_id: null}});
  await pause(40); await view.key('/attach now'); await view.key('\r');
  assert.equal(view.client.connections[0]!.target.taskId, 't-known'); assert.equal(view.client.shutdowns, 0);
  view.client.connections[0]!.reject(new Error('无法连接')); await pause(40); assert.equal(view.client.shutdowns, 0);
});
async function mount(columns = 80, rows = 24) {
  const client = new Client();
  const view = render(<App client={client} initialState={{...initialState, phase: 'idle', workspace: '/workspace', sessionId: 'session'}}
    windowSize={{columns, rows}} publishSidebarSignal={() => {}}/>);
  const key = async (text: string) => {view.stdin.write(text); await pause(35);};
  const click = async (label: string) => {
    const until = Date.now() + 1500;
    while (!view.lastFrame()?.includes(label) && Date.now() < until) await pause(5);
    await pause(30); const lines = view.lastFrame()!.split('\n'); const index = lines.findIndex(line => line.includes(label));
    assert.ok(index >= 0, `missing ${label}: ${view.lastFrame()}`);
    const column = stringWidth(lines[index]!.slice(0, lines[index]!.indexOf(label))) + 2;
    await key(`\x1b[<0;${column};${index + 1}M\x1b[<0;${column};${index + 1}m`);
  };
  await pause(40); return {...view, client, key, click};
}

test('task selection opens only the selected task history and returns to the original draft cursor', async () => {
  const view = await mount(); await view.key('中文草稿'); await view.key('\x1b[D');
  await view.click('[ 任务 ]'); assert.deepEqual(view.client.commands[0], {id: 'command-1', command: 'tasks', args: {}});
  view.client.page('command-1', [row('t-first', '第一个完整目标'), row('t-second', '第二个完整目标')]); await pause(40);
  assert.match(view.lastFrame()!, /第一个完整目标/); await view.click('[ 后条 ]'); assert.match(view.lastFrame()!, /第二个完整目标/);
  await view.click('[ 查看记录 ]');
  assert.deepEqual(view.client.commands[1], {id: 'command-2', command: 'inspections', args: {task_id: 't-second'}});
  view.client.emit({v: 1, type: 'inspection_history', id: 'command-2', workspace: '/workspace', session_id: 'session', task_id: 't-second', records: [], next_cursor: null, skipped: 0});
  await pause(40); await view.click('[ 返回 ]'); await view.key('中');
  assert.match(view.lastFrame()!, /中文草中稿/); assert.equal(view.client.runs.length, 0);
});

test('paging and retry reject foreign or stale replies without replacing the original page', async () => {
  const view = await mount(); await view.click('[ 任务 ]');
  view.client.page('command-1', [row('t-current', '保留原页')], 'cursor-next'); await pause(40);
  await view.click('[ 更早 ]'); assert.deepEqual(view.client.commands[1]!.args, {cursor: 'cursor-next'});
  view.client.page('command-1', [row('t-stale', '旧回执不可接入')]); await pause(40);
  assert.match(view.lastFrame()!, /保留原页/); assert.doesNotMatch(view.lastFrame()!, /旧回执不可接入/);
  view.client.emit({v: 1, type: 'task_catalog', id: 'command-2', workspace: '/foreign', session_id: 'session',
    records: [row('t-foreign', '别的工作区')], next_cursor: null, skipped: 0, problems: []}); await pause(40);
  assert.match(view.lastFrame()!, /回执不匹配/); assert.match(view.lastFrame()!, /保留原页/);
  await view.click('[ 重试 ]'); assert.deepEqual(view.client.commands[2]!.args, {cursor: 'cursor-next'});
  view.client.page('command-3', [row('t-older', '正确的旧任务')]); await pause(40); assert.match(view.lastFrame()!, /正确的旧任务/);
  await view.click('[ 刷新 ]'); assert.deepEqual(view.client.commands[3]!.args, {cursor: 'cursor-next'});
  view.client.page('command-4', [row('t-older', '正确的旧任务')]); await pause(40);
  await view.click('[ 较新 ]'); assert.deepEqual(view.client.commands[4]!.args, {});
});

test('an approval arriving during narrow task reading preserves the draft and Ctrl+C target', async () => {
  const view = await mount(32, 12); await view.key('当前运行'); await view.key('\r');
  await view.key('中文草稿'); await view.key('\x1b[D'); await view.click('[ 任务 ]');
  view.client.page('command-1', [{...row('t-other', '另一个任务的完整目标'), pending_approvals: 1}]); await pause(40);
  view.client.emit({v: 1, type: 'approval_request', id: 'run-1', approval_id: 'original-approval', call: {id: 'write', name: 'write_file', args: {path: 'x', content: '等待确认'}}, preview: '等待确认', decisions: ['once', 'deny']}); await pause(40);
  assert.match(view.lastFrame()!, /待审批.*返回/); assert.equal(view.lastFrame()!.split('\n').length, 12);
  await view.key('\x03'); assert.deepEqual(view.client.cancellations, ['run-1']);
  await view.click('[ 返回 ]'); await view.key('中'); assert.match(view.lastFrame()!, /中文草中稿/);
  assert.equal(view.client.runs.length, 1);
});

test('closing a pending directory ignores its late result and reopening queries again', async () => {
  const view = await mount(); await view.click('[ 任务 ]'); await view.click('[ 返回 ]');
  view.client.page('command-1', [row('t-late', '迟到的目录')]); await pause(40);
  assert.doesNotMatch(view.lastFrame()!, /迟到的目录/); await view.click('[ 任务 ]');
  assert.equal(view.client.commands.length, 2); view.client.page('command-2', []); await pause(40);
  assert.match(view.lastFrame()!, /没有可读取的任务/); assert.equal(view.client.runs.length, 0);
});

test('history pagination retains the selected task filter and the ordinary history entry clears it', async () => {
  const view = await mount(); await view.click('[ 任务 ]'); view.client.page('command-1', [row('t-selected', '选中的任务')]); await pause(40);
  await view.click('[ 查看记录 ]');
  const cursor = `${'0'.repeat(20)}-${'a'.repeat(64)}.json`;
  view.client.emit({v: 1, type: 'inspection_history', id: 'command-2', workspace: '/workspace', session_id: 'session', task_id: 't-selected', records: [], next_cursor: cursor, skipped: 0}); await pause(40);
  await view.click('[ 更早 ]'); assert.deepEqual(view.client.commands[2]!.args, {task_id: 't-selected', cursor});
  view.client.emit({v: 1, type: 'inspection_history', id: 'command-3', workspace: '/workspace', session_id: 'session', task_id: 't-selected', records: [], next_cursor: null, skipped: 0}); await pause(40);
  await view.click('[ 返回 ]'); await view.click('[ 历史结果 ]'); assert.equal(view.client.commands[3]!.args, undefined);
});

test('the task command remains available during a run when structured navigation is supported', async () => {
  const view = await mount(); await view.key('当前运行'); await view.key('\r');
  await view.key('/tasks'); await view.key('\r');
  assert.deepEqual(view.client.commands[0], {id: 'command-1', command: 'tasks', args: {}});
  view.client.page('command-1', []); await pause(40); assert.match(view.lastFrame()!, /没有可读取的任务/);
  await view.key('\x03'); assert.deepEqual(view.client.cancellations, ['run-1']);
});

test('refresh preserves the selected task identity and a removed task requires a new selection', async () => {
  const view = await mount(); await view.click('[ 任务 ]');
  view.client.page('command-1', [row('t-one', '首条任务'), row('t-chosen', '正在读的任务')]); await pause(40);
  await view.click('[ 后条 ]'); await view.click('[ 刷新 ]');
  view.client.page('command-2', [row('t-new', '新插入的任务'), row('t-one', '首条任务'), row('t-chosen', '正在读的任务')]); await pause(40);
  assert.match(view.lastFrame()!, /正在读的任务/); assert.doesNotMatch(view.lastFrame()!, /新插入的任务/);
  await view.click('[ 刷新 ]'); view.client.page('command-3', [row('t-new', '剩下的任务')]); await pause(40);
  assert.match(view.lastFrame()!, /原任务.*重新选择/); assert.doesNotMatch(view.lastFrame()!, /\[ 查看记录 \]/);
  await view.click('[ 后条 ]'); assert.match(view.lastFrame()!, /剩下的任务/); await view.click('[ 查看记录 ]');
  assert.deepEqual(view.client.commands[3]!.args, {task_id: 't-new'});
});
