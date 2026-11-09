import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {App} from '../src/app.js';
import type {BackendClient} from '../src/backend.js';
import type {RunResult, ServerEvent} from '../src/protocol.js';
import {initialState} from '../src/view-state.js';

afterEach(cleanup);
class Client {
  listeners = new Set<(event: ServerEvent) => void>();
  runs: string[] = []; cancellations: string[] = []; shutdowns = 0; fail = false;
  subscribe(fn: (event: ServerEvent) => void) {this.listeners.add(fn); return () => this.listeners.delete(fn);}
  capabilities() {return [];}
  start() {}
  run(text: string) {if (this.fail) throw new Error('未送达'); this.runs.push(text); return `run-${this.runs.length}`;}
  command() {return 'command';}
  cancel(id: string) {this.cancellations.push(id); return 'cancel';}
  steer() {return 'steer';}
  approve(): string {throw new Error('queue must not approve');}
  async shutdown() {this.shutdowns++;}
  emit(event: unknown) {for (const fn of this.listeners) fn(event as ServerEvent);}
}
const done = (id: string, result: Partial<RunResult> = {}): ServerEvent => ({v: 1, type: 'done', id,
  result: {status: 'done', text: '结束', stop_reason: 'completed', run_id: id, steps: 0, usage: {}, cost_usd: 0, ...result}});
async function mount(columns = 100, rows = 30) {
  const client = new Client();
  const view = render(<App client={client as BackendClient} windowSize={{columns, rows}}
    initialState={{...initialState, phase: 'idle', workspace: '/tmp/followup-app', sessionId: 'queue-test'}} publishSidebarSignal={() => {}}/>);
  const key = async (input: string) => {view.stdin.write(input); await pause(35);};
  const send = async (text: string) => {await key(text); await key('\r');};
  const click = async (label: string) => {
    const deadline = Date.now() + 1500;
    while (!view.lastFrame()?.includes(label) && Date.now() < deadline) await pause(5);
    await pause(30);
    const lines = view.lastFrame()!.split('\n'); const row = lines.findIndex(line => line.includes(label));
    assert.ok(row >= 0, `missing ${label}: ${view.lastFrame()}`);
    const col = stringWidth(lines[row]!.slice(0, lines[row]!.indexOf(label))) + 2;
    await key(`\x1b[<0;${col};${row + 1}M\x1b[<0;${col};${row + 1}m`);
  };
  await pause(40); return {...view, client, key, send, click};
}

test('failed verification retains followups for mouse editing, withdrawal and explicit continuation', async () => {
  const view = await mount();
  await view.send('当前任务'); await view.send('/followup 下一条'); await view.send('/followup 最后一条');
  await view.key('中文草稿'); await view.key('\x1b[D');
  view.client.emit(done('run-1', {verified: false})); await pause(40);
  assert.deepEqual(view.client.runs, ['当前任务']);
  await view.click('后续 2'); assert.match(view.lastFrame()!, /验收未通过/);
  await view.click('[ 编辑 ]'); await view.key('\x15'); await view.key('修正后的下一条'); await view.click('[ 保存 ]');
  assert.match(view.lastFrame()!, /修正后的下一条/);
  await view.click('[ 后条 ]'); assert.match(view.lastFrame()!, /最后一条/);
  await view.click('[ 撤回 ]'); assert.doesNotMatch(view.lastFrame()!, /最后一条/);
  await view.click('[ 继续 ]');
  assert.deepEqual(view.client.runs, ['当前任务', '修正后的下一条']);
  await view.key('中'); assert.match(view.lastFrame()!, /中文草中稿/);
  await view.key('\x03'); assert.deepEqual(view.client.cancellations, ['run-2']);
});

test('cancelled and unknown runs keep pending messages, and closed queues remain editable but cannot resume', async () => {
  const view = await mount();
  await view.send('第一轮'); await view.send('/followup 保留这条');
  view.client.emit(done('run-1', {status: 'cancelled'})); await pause(40);
  await view.click('后续 1'); assert.match(view.lastFrame()!, /已取消/);
  await view.click('[ 返回 ]');
  await view.send('新的当前任务'); view.client.emit({v: 1, type: 'closed', id: null}); await pause(40);
  await view.click('后续 1'); assert.match(view.lastFrame()!, /结果未知/);
  assert.doesNotMatch(view.lastFrame()!, /\[ 继续 \]/);
  await view.click('[ 编辑 ]'); await view.key('\x15'); await view.key('断线后修改'); await view.click('[ 保存 ]');
  assert.match(view.lastFrame()!, /断线后修改/);
  assert.deepEqual(view.client.runs, ['第一轮', '新的当前任务']);
});

test('viewing and editing pause automatic dispatch; late completion cannot consume another pending item', async () => {
  const view = await mount(32, 12);
  await view.send('第一轮'); await view.send('/followup 原待办'); await view.send('/followup 最后一条');
  await view.click('后续 2'); await view.click('[ 编辑 ]'); await view.key('\x15'); await view.key('正在编辑');
  view.client.emit(done('run-1')); await pause(40);
  assert.deepEqual(view.client.runs, ['第一轮']); assert.match(view.lastFrame()!, /正在编辑/);
  await view.click('[ 取消 ]'); assert.match(view.lastFrame()!, /原待办/);
  await view.click('[ 继续 ]'); assert.deepEqual(view.client.runs, ['第一轮', '原待办']);
  view.client.emit(done('run-1')); await pause(40);
  assert.deepEqual(view.client.runs, ['第一轮', '原待办']);
  view.client.emit(done('run-2')); await pause(40);
  assert.deepEqual(view.client.runs, ['第一轮', '原待办', '最后一条']);
  for (const line of view.lastFrame()!.split('\n')) assert.ok(stringWidth(line) <= 32, line);
});

test('sending failure retains the selected message and retry submits it once', async () => {
  const view = await mount();
  await view.send('第一轮'); await view.send('/followup 待重试');
  view.client.fail = true; view.client.emit(done('run-1')); await pause(40);
  await view.click('后续 1'); assert.match(view.lastFrame()!, /未能启动/);
  view.client.fail = false; await view.click('[ 继续 ]');
  assert.deepEqual(view.client.runs, ['第一轮', '待重试']);
});

test('pending messages require explicit discard on exit, and no-argument followup opens the manager', async () => {
  const view = await mount();
  await view.send('第一轮'); await view.send('/followup 尚未发送'); view.client.emit(done('run-1', {status: 'error'})); await pause(40);
  await view.send('/followup'); assert.match(view.lastFrame()!, /后续消息 · 1\/1/);
  await view.key('\x11'); assert.match(view.lastFrame()!, /退出并丢弃 1 条待办/);
  assert.equal(view.client.shutdowns, 0);
  await view.key('\x1b'); assert.match(view.lastFrame()!, /尚未发送/);
  await view.key('\x11'); await view.key('2'); assert.equal(view.client.shutdowns, 1);
});

test('editing survives a cancelled exit dialog and incoming approval; stop still targets the current run', async () => {
  const view = await mount();
  await view.send('当前任务'); await view.send('/followup 原文');
  await view.key('中文草稿'); await view.key('\x1b[D');
  await view.click('后续 1'); await view.click('[ 编辑 ]'); await view.key('\x15'); await view.key('未保存的中文');
  await view.key('\x11'); await view.key('\x1b');
  assert.match(view.lastFrame()!, /未保存的中文/);
  view.client.emit({v: 1, type: 'approval_request', id: 'run-1', approval_id: 'pending',
    call: {id: 'tool', name: 'write_file', args: {}}, preview: 'write file', decisions: ['once', 'deny']});
  await pause(40); assert.match(view.lastFrame()!, /待审批/);
  await view.key('ysnp'); assert.match(view.lastFrame()!, /未保存的中文ysnp/);
  await view.key('\x03'); assert.deepEqual(view.client.cancellations, ['run-1']);
  await view.click('[ 保存 ]'); await view.click('[ 返回 ]');
  assert.match(view.lastFrame()!, /正在停止/);
  assert.doesNotMatch(view.lastFrame()!, /允许本次写入/);
  await view.key('中'); assert.match(view.lastFrame()!, /中文草中稿/);
  assert.deepEqual(view.client.runs, ['当前任务']);
});

test('pending messages do not hide the existing wait-before-exit choice for a running task', async () => {
  const view = await mount();
  await view.send('当前任务'); await view.send('/followup 待办');
  await view.key('\x11');
  assert.match(view.lastFrame()!, /等待完成/);
  assert.match(view.lastFrame()!, /丢弃 1 条待办/);
  await view.key('1'); assert.equal(view.client.shutdowns, 0);
  view.client.emit(done('run-1')); await pause(40);
  assert.equal(view.client.shutdowns, 1);
  assert.deepEqual(view.client.runs, ['当前任务']);
});

for (const [label, result] of [
  ['stopped', {status: 'stopped'}], ['truncated', {status: 'truncated'}],
  ['completion rejected', {completion: {allowed: false, codes: ['failed'], messages: ['未通过']}}],
  ['plan awaiting review', {stop_reason: 'plan_submitted'}],
] as [string, Partial<RunResult>][]) {
  test(`${label} pauses remaining messages until explicit continuation`, async () => {
    const view = await mount(); await view.send('当前任务'); await view.send('/followup 等待决定');
    view.client.emit(done('run-1', result)); await pause(40);
    assert.deepEqual(view.client.runs, ['当前任务']);
    await view.click('后续 1'); assert.match(view.lastFrame()!, /待办保留/);
    await view.click('[ 返回 ]'); await view.send('另一次任务'); view.client.emit(done('run-2')); await pause(40);
    assert.deepEqual(view.client.runs, ['当前任务', '另一次任务']);
    await view.click('后续 1'); await view.click('[ 继续 ]');
    assert.deepEqual(view.client.runs, ['当前任务', '另一次任务', '等待决定']);
  });
}

test('a failed verification command pauses even when the final summary has no verification verdict', async () => {
  const view = await mount(); await view.send('当前任务');
  view.client.emit({v: 1, type: 'tool_result', id: 'run-1', call: {id: 'check', name: 'bash', args: {command: 'exit 1', verification: true}},
    ok: false, output: 'failed', execution: {status: 'failed', exit_code: 1}});
  await view.send('/followup 失败后排队');
  view.client.emit(done('run-1', {verified: null, completion: {allowed: true, codes: [], messages: []}})); await pause(40);
  assert.deepEqual(view.client.runs, ['当前任务']);
  await view.click('后续 1'); assert.match(view.lastFrame()!, /验收未通过/);
});

test('authoritative verified success after repair can advance, and another run failure cannot pause it', async () => {
  const view = await mount(); await view.send('当前任务'); await view.send('/followup 下一条'); await view.send('/followup 最后');
  const failure = {v: 1, type: 'tool_result', id: 'run-1', call: {id: 'check', name: 'bash', args: {command: 'exit 1', verification: true}},
    ok: false, output: 'failed', execution: {status: 'failed', exit_code: 1}};
  view.client.emit(failure); view.client.emit(done('run-1', {verified: true})); await pause(40);
  assert.deepEqual(view.client.runs, ['当前任务', '下一条']);
  view.client.emit(failure); view.client.emit(done('run-2')); await pause(40);
  assert.deepEqual(view.client.runs, ['当前任务', '下一条', '最后']);
});
