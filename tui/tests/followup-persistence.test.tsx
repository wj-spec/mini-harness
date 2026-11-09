import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import {mkdtempSync, mkdirSync, rmSync, renameSync, writeFileSync, readFileSync, existsSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {App} from '../src/app.js';
import {initialState} from '../src/view-state.js';
import type {BackendClient} from '../src/backend.js';
import type {RunTaskMeta, ServerEvent} from '../src/protocol.js';

const roots: string[] = [];
afterEach(() => {cleanup(); for (const root of roots.splice(0)) rmSync(root, {recursive: true, force: true});});
function fixture() {
  const root = mkdtempSync(path.join(tmpdir(), 'followup-session-')); roots.push(root);
  const session = path.join(root, '.vibe/sessions/test-session'); mkdirSync(session, {recursive: true});
  return {root, session};
}
class Client {
  failAfterSend = false;
  listeners = new Set<(event: ServerEvent) => void>(); runs: {id: string; text: string}[] = [];
  subscribe(fn: (event: ServerEvent) => void) {this.listeners.add(fn); return () => this.listeners.delete(fn);}
  capabilities() {return [];}
  start() {}
  run(text: string, _task?: RunTaskMeta, requestedId?: string) {
    const id = requestedId ?? `run-${this.runs.length + 1}`; this.runs.push({text, id});
    if (this.failAfterSend) throw new Error('transport interrupted');
    return id;
  }
  command() {return 'command';} cancel() {return 'cancel';} steer() {return 'steer';} approve(): string {throw new Error('not an approval');}
  async shutdown() {}
  emit(value: unknown) {for (const fn of this.listeners) fn(value as ServerEvent);}
  done(id: string, verified = false) {this.emit({v: 1, type: 'done', id, result: {status: 'done', text: '完成', stop_reason: '', steps: 0, usage: {}, cost_usd: 0, run_id: id, verified}});}
}
async function mount(where: ReturnType<typeof fixture>, columns = 100, rows = 30, taskId?: string) {
  const client = new Client();
  const view = render(<App client={client as BackendClient} attach={Boolean(taskId)} initialState={{...initialState, phase: 'idle', workspace: where.root,
    sessionId: 'test-session', sessionPath: where.session, ...(taskId ? {attachedTaskId: taskId} : {})}} windowSize={{columns, rows}} publishSidebarSignal={() => {}}/>);
  const key = async (text: string) => {view.stdin.write(text); await pause(35);};
  const send = async (text: string) => {await key(text); await key('\r');};
  const click = async (label: string) => {
    const deadline = Date.now() + 2000;
    while (!view.lastFrame()?.includes(label) && Date.now() < deadline) await pause(5);
    await pause(30); const lines = view.lastFrame()!.split('\n'); const row = lines.findIndex(line => line.includes(label));
    assert.ok(row >= 0, `missing ${label}: ${view.lastFrame()}`);
    const column = stringWidth(lines[row]!.slice(0, lines[row]!.indexOf(label))) + 2;
    await key(`\x1b[<0;${column};${row + 1}M\x1b[<0;${column};${row + 1}m`);
  };
  await pause(50); return {...view, client, key, send, click};
}

test('saved followups reopen paused and only an explicit continuation submits the edited item', async () => {
  const where = fixture(); const first = await mount(where);
  await first.send('当前任务'); await first.send('/followup 明天处理'); first.client.done('run-1'); await pause(40);
  first.unmount(); await pause(20);
  const reopened = await mount(where);
  assert.equal(reopened.client.runs.length, 0);
  await reopened.click('后续 1'); assert.match(reopened.lastFrame()!, /已恢复.*暂停/);
  assert.match(reopened.lastFrame()!, /明天处理/); assert.match(reopened.lastFrame()!, /已保存到会话/);
  await reopened.click('[ 编辑 ]'); await reopened.key('\x15'); await reopened.key('恢复后修正'); await reopened.click('[ 保存 ]');
  await reopened.click('[ 继续 ]');
  assert.equal(reopened.client.runs.length, 1); assert.equal(reopened.client.runs[0]!.text, '恢复后修正');
});

test('another window reads the same queue and explicitly takes over after the writer closes', async () => {
  const where = fixture(); const owner = await mount(where);
  await owner.send('当前任务'); await owner.send('/followup 只发送一次'); owner.client.done('run-1'); await pause(40);
  const reader = await mount(where); await reader.click('后续 1');
  assert.match(reader.lastFrame()!, /另一窗口.*只读/);
  assert.doesNotMatch(reader.lastFrame()!, /\[ 编辑 \]|\[ 撤回 \]|\[ 继续 \]/);
  await reader.click('[ 接入 ]'); assert.match(reader.lastFrame()!, /另一窗口.*只读/);
  owner.unmount(); await pause(30);
  await reader.click('[ 接入 ]'); assert.match(reader.lastFrame()!, /已恢复.*暂停/);
  assert.equal(reader.client.runs.length, 0); await reader.click('[ 继续 ]');
  assert.equal(reader.client.runs.length, 1); assert.equal(reader.client.runs[0]!.text, '只发送一次');
});

test('an interrupted send is restored for review and never replayed or cleared by a stale receipt', async () => {
  const where = fixture(); const first = await mount(where);
  await first.send('当前任务'); await first.send('/followup 可能已执行'); await first.send('/followup 下一件');
  first.client.done('run-1', true); await pause(50);
  assert.equal(first.client.runs.length, 2); const sent = first.client.runs[1]!;
  first.unmount(); await pause(20);
  const reopened = await mount(where); await reopened.click('后续 2');
  assert.match(reopened.lastFrame()!, /结果待核对/); assert.match(reopened.lastFrame()!, /可能已执行/);
  assert.ok(reopened.lastFrame()!.includes(sent.id)); assert.doesNotMatch(reopened.lastFrame()!, /\[ 继续 \]/);
  assert.equal(reopened.client.runs.length, 0);
  await reopened.click('[ 移出 ]'); assert.match(reopened.lastFrame()!, /不会停止或重发/);
  await reopened.click('[ 确认移出 ]'); assert.match(reopened.lastFrame()!, /下一件/);
  await reopened.click('[ 继续 ]'); assert.equal(reopened.client.runs.length, 1);
  assert.equal(reopened.client.runs[0]!.text, '下一件');
  await reopened.click('后续 1'); assert.match(reopened.lastFrame()!, /正在执行/); assert.doesNotMatch(reopened.lastFrame()!, /\[ 移出 \]/);
  reopened.client.done(sent.id, true); await pause(40); assert.match(reopened.lastFrame()!, /下一件/);
  reopened.client.done(reopened.client.runs[0]!.id, true); await pause(40);
  assert.match(reopened.lastFrame()!, /没有待办/);
  reopened.unmount(); await pause(20); const empty = await mount(where);
  await empty.send('/followup'); assert.match(empty.lastFrame()!, /没有待办/); assert.equal(empty.client.runs.length, 0);
});

test('a failed save preserves the edited draft and blocks execution until saving succeeds', async () => {
  const where = fixture(); const view = await mount(where);
  await view.send('当前任务'); await view.send('/followup 原文'); view.client.done('run-1'); await pause(40);
  await view.click('后续 1'); await view.click('[ 编辑 ]'); await view.key('\x15'); await view.key('不能丢的中文草稿');
  const file = path.join(where.session, 'followups.json'); renameSync(file, `${file}.saved`); mkdirSync(file);
  await view.click('[ 保存 ]'); assert.match(view.lastFrame()!, /未保存/); assert.match(view.lastFrame()!, /不能丢的中文草稿/);
  assert.match(view.lastFrame()!, /\[ 保存 \]/); assert.equal(view.client.runs.length, 1);
  rmSync(file, {recursive: true}); renameSync(`${file}.saved`, file);
  await view.click('[ 保存 ]'); await view.click('[ 继续 ]');
  assert.equal(view.client.runs.length, 2); assert.equal(view.client.runs[1]!.text, '不能丢的中文草稿');
});

test('a corrupt queue is reported and its original bytes are never overwritten', async () => {
  const where = fixture(); const file = path.join(where.session, 'followups.json'); writeFileSync(file, '{broken queue');
  const view = await mount(where); await view.send('/followup');
  assert.match(view.lastFrame()!, /未能读取/); assert.doesNotMatch(view.lastFrame()!, /已保存到会话/);
  await view.click('[ 接入 ]'); assert.equal(readFileSync(file, 'utf8'), '{broken queue');
  await view.click('[ 返回 ]'); await view.send('当前任务'); await view.send('/followup 不能清空我');
  assert.match(view.lastFrame()!, /不能清空我/); assert.equal(readFileSync(file, 'utf8'), '{broken queue');
});

test('a send exception retains an uncertain request and reopening never resends it', async () => {
  const where = fixture(); const view = await mount(where);
  await view.send('当前任务'); await view.send('/followup 发送中断'); view.client.failAfterSend = true;
  view.client.done('run-1', true); await pause(50); await view.click('后续 1');
  assert.match(view.lastFrame()!, /结果待核对/); assert.match(view.lastFrame()!, /发送中断/);
  assert.doesNotMatch(view.lastFrame()!, /\[ 继续 \]/);
  view.unmount(); await pause(20); const reopened = await mount(where, 32, 12);
  await reopened.click('后续 1'); assert.match(reopened.lastFrame()!, /结果待核对/);
  assert.equal(reopened.client.runs.length, 0); assert.equal(reopened.lastFrame()!.split('\n').length, 12);
});

test('a failed pre-send journal prevents transport and keeps the pending message', async () => {
  const where = fixture(); const view = await mount(where);
  await view.send('当前任务'); await view.send('/followup 尚未发送');
  const file = path.join(where.session, 'followups.json'); renameSync(file, `${file}.saved`); mkdirSync(file);
  view.client.done('run-1', true); await pause(40); assert.equal(view.client.runs.length, 1);
  await view.click('后续 1'); assert.match(view.lastFrame()!, /未保存/); assert.match(view.lastFrame()!, /尚未发送/);
  assert.doesNotMatch(view.lastFrame()!, /\[ 继续 \]/);
  rmSync(file, {recursive: true}); renameSync(`${file}.saved`, file);
  await view.click('[ 重试 ]'); await view.click('[ 继续 ]'); assert.equal(view.client.runs.length, 2);
});

test('quitting a persisted queue warns about an unsaved editor and cancelling keeps its text and cursor', async () => {
  const where = fixture(); const view = await mount(where);
  await view.send('当前任务'); await view.send('/followup 已落盘'); view.client.done('run-1'); await pause(40);
  await view.click('后续 1'); await view.click('[ 编辑 ]'); await view.key('\x15'); await view.key('中文草稿'); await view.key('\x1b[D');
  await view.key('\x11'); assert.match(view.lastFrame()!, /未保存/);
  await view.key('\x1b'); await view.key('中'); assert.match(view.lastFrame()!, /中文草中稿/);
  await view.click('[ 保存 ]'); await view.click('[ 返回 ]'); await view.key('\x11');
  view.unmount(); await pause(20); const reopened = await mount(where);
  await reopened.click('后续 1'); assert.match(reopened.lastFrame()!, /中文草中稿/); assert.equal(reopened.client.runs.length, 0);
});

test('workspace and attached task scopes do not share pending messages', async () => {
  const where = fixture(); const main = await mount(where);
  await main.send('当前任务'); await main.send('/followup 主会话待办'); main.client.done('run-1'); await pause(40);
  const other = await mount(fixture()); await other.send('/followup'); assert.match(other.lastFrame()!, /没有待办/);
  const task = await mount(where, 100, 30, 'task-A');
  await task.send('任务 A'); await task.send('/followup 任务 A 待办'); task.client.done('run-1'); await pause(40);
  await task.click('后续 1'); assert.match(task.lastFrame()!, /任务 A 待办/); assert.doesNotMatch(task.lastFrame()!, /主会话待办/);
  const otherTask = await mount(where, 100, 30, 'task-B'); await otherTask.send('/followup'); assert.match(otherTask.lastFrame()!, /没有待办/);
  task.unmount(); await pause(20); const recovered = await mount(where, 100, 30, 'task-A');
  await recovered.click('后续 1'); assert.match(recovered.lastFrame()!, /任务 A 待办/);
  assert.equal(recovered.client.runs.length, 0);
});

test('failure to save a terminal receipt keeps an uncertain record and prevents the next send', async () => {
  const where = fixture(); const view = await mount(where);
  await view.send('当前任务'); await view.send('/followup 第一件'); await view.send('/followup 第二件');
  view.client.done('run-1', true); await pause(40); const sent = view.client.runs[1]!;
  const file = path.join(where.session, 'followups.json'); renameSync(file, `${file}.saved`); mkdirSync(file);
  view.client.done(sent.id, true); await pause(40); assert.equal(view.client.runs.length, 2);
  await view.click('后续 2'); assert.match(view.lastFrame()!, /结果待核对/); assert.match(view.lastFrame()!, /未保存/);
  rmSync(file, {recursive: true}); renameSync(`${file}.saved`, file);
  await view.click('[ 重试 ]'); assert.doesNotMatch(view.lastFrame()!, /\[ 继续 \]/); assert.match(view.lastFrame()!, /第一件/);
});

test('a terminal transport error unlocks review of the uncertain request without calling it unsent', async () => {
  const view = await mount(fixture()); await view.send('当前任务'); await view.send('/followup 有可能执行');
  view.client.done('run-1', true); await pause(40);
  view.client.emit({v: 1, type: 'error', id: view.client.runs[1]!.id, code: 'backend_error', message: '执行通道出错'}); await pause(40);
  await view.click('后续 1'); assert.match(view.lastFrame()!, /结果待核对/);
  assert.doesNotMatch(view.lastFrame()!, /尚未发送|正在执行/); assert.match(view.lastFrame()!, /\[ 移出 \]/);
  assert.equal(view.client.runs.length, 2);
});

test('a followup refused before execution returns to its queue and waits for an explicit retry', async () => {
  const where = fixture(); const view = await mount(where);
  await view.send('当前任务'); await view.send('/followup 等待会话空闲后执行');
  view.client.done('run-1', true); await pause(40);
  const rejected = view.client.runs[1]!;
  view.client.emit({v: 1, type: 'error', id: rejected.id, code: 'session_busy', message: '会话正在其他连接运行'}); await pause(40);
  await view.click('后续 1');
  assert.match(view.lastFrame()!, /等待会话空闲后执行/);
  assert.doesNotMatch(view.lastFrame()!, /结果待核对/);
  assert.match(view.lastFrame()!, /\[ 继续 \]/);
  assert.equal(view.client.runs.length, 2);
  view.unmount(); await pause(30);
  const reopened = await mount(where); await reopened.click('后续 1');
  assert.equal(reopened.client.runs.length, 0); await reopened.click('[ 继续 ]');
  assert.equal(reopened.client.runs[0]!.text, rejected.text);
  assert.notEqual(reopened.client.runs[0]!.id, rejected.id);
});

test('replacing the session directory cannot redirect a live queue writer', async () => {
  const where = fixture(); const view = await mount(where);
  await view.send('当前任务'); await view.send('/followup 原目录待办'); view.client.done('run-1'); await pause(40);
  renameSync(where.session, `${where.session}.original`); mkdirSync(where.session);
  await view.click('后续 1'); await view.click('[ 编辑 ]'); await view.key('\x15'); await view.key('保留修改'); await view.click('[ 保存 ]');
  assert.match(view.lastFrame()!, /未保存/); assert.match(view.lastFrame()!, /保留修改/);
  assert.equal(existsSync(path.join(where.session, 'followups.json')), false);
});
