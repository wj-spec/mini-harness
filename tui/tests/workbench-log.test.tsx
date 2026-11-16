import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import {App} from '../src/app.js';
import {initialState, type Entry} from '../src/view-state.js';
import type {BackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';

afterEach(cleanup);

class Client implements BackendClient {
  listeners = new Set<(event: ServerEvent) => void>();
  capabilities() {return [];} start() {}
  subscribe(fn: (event: ServerEvent) => void) {this.listeners.add(fn); return () => {this.listeners.delete(fn);};}
  run(text: string) {return `run-${text.length}`;}
  command(command: string) {return `command-${command}`;}
  cancel() {return 'cancel';} steer() {return 'steer';} approve() {return 'approval';} async shutdown() {}
  emit(event: unknown) {for (const fn of this.listeners) fn(event as ServerEvent);}
}

function historyEntries(): Entry[] {
  const entries: Entry[] = [];
  for (let index = 0; index < 60; index += 1) entries.push({id: index, kind: 'assistant', text: `第 ${index} 条历史回复：用于撑满阅读区。`});
  return entries;
}

function mount(entries: Entry[] = []) {
  const client = new Client();
  const view = render(<App client={client} initialState={{...initialState, phase: 'idle', workspace: '/workspace', sessionId: 'session',
    entries, nextEntry: entries.length, hasStartedRun: entries.length > 0}} windowSize={{columns: 80, rows: 24}} publishSidebarSignal={() => {}}/>);
  return {view, client};
}

test('workbench notices leave the conversation pane and show in a record line with /logs access', async () => {
  const {view, client} = mount([{id: 0, kind: 'task', text: '修一个 bug'}]);
  await pause(40);
  client.emit({v: 1, type: 'notice', id: null, message: '已复制所选文本：12 字符'});
  await pause(40);
  const frame = view.lastFrame()!;
  assert.match(frame, /记录：已复制所选文本/);
  assert.doesNotMatch(frame, /工作台记录/);
  assert.match(frame, /你 · 当前任务/);
  view.stdin.write('/logs'); await pause(35); view.stdin.write('\r'); await pause(60);
  assert.match(view.lastFrame()!, /工作台记录 · 1 条/);
  assert.match(view.lastFrame()!, /已复制所选文本：12 字符/);
  view.stdin.write('\x1b'); await pause(40);
  view.stdin.write('草稿'); await pause(40);
  assert.match(view.lastFrame()!, /草稿/);
  assert.doesNotMatch(view.lastFrame()!, /工作台记录 · 1 条/);
});

test('a new notice does not move the reading position while scrolling back', async () => {
  const {view, client} = mount(historyEntries());
  await pause(50);
  view.stdin.write('\x12'); await pause(40); // Ctrl+R: reading focus
  for (let step = 0; step < 4; step += 1) {view.stdin.write('\x1b[A'); await pause(25);}
  const range = view.lastFrame()!.match(/回看 · 下方 \d+ 行/)?.[0];
  assert.ok(range, view.lastFrame()!);
  client.emit({v: 1, type: 'notice', id: null, message: '后台提示：另一个窗口已接入'});
  await pause(50);
  assert.equal(view.lastFrame()!.match(/回看 · 下方 \d+ 行/)?.[0], range);
  assert.match(view.lastFrame()!, /记录：后台提示/);
});

test('run receipts stay in the conversation transcript while workbench notices stay in the log', async () => {
  const {view, client} = mount([{id: 0, kind: 'task', text: '执行一次验证'}]);
  await pause(40);
  client.emit({v: 1, type: 'done', id: null, result: {text: '完成', steps: 1, status: 'done', usage: {},
    cost_usd: 0, run_id: 'r-1', stop_reason: '', receipt: '[运行结束] status=done steps=1 tokens=10 cost=$0.0000\n[任务验收] 已验证'}});
  await pause(60);
  assert.match(view.lastFrame()!, /历史运行记录/);
  view.stdin.write('/logs'); await pause(35); view.stdin.write('\r'); await pause(60);
  assert.match(view.lastFrame()!, /工作台记录 · 0 条/);
  assert.match(view.lastFrame()!, /还没有工作台记录/);
});

test('the log panel exposes full multi-line notices while the record line shows only the summary', async () => {
  const {view, client} = mount();
  await pause(40);
  client.emit({v: 1, type: 'notice', id: null, message: '权限规则已更新\n第二行：session 精确规则 2 条'});
  await pause(40);
  assert.match(view.lastFrame()!, /记录：权限规则已更新/);
  assert.doesNotMatch(view.lastFrame()!, /第二行：session 精确规则/);
  view.stdin.write('/logs'); await pause(35); view.stdin.write('\r'); await pause(60);
  assert.match(view.lastFrame()!, /第二行：session 精确规则 2 条/);
});
