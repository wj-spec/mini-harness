import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import {App} from '../src/app.js';
import {initialState} from '../src/view-state.js';
import type {BackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';
import stringWidth from 'string-width';

afterEach(cleanup);
class Client implements BackendClient {
  listeners = new Set<(event: ServerEvent) => void>(); runs: string[] = []; commands: string[] = [];
  cancellations: string[] = []; approvals: string[] = [];
  capabilities() {return [];} start() {}
  subscribe(fn: (event: ServerEvent) => void) {this.listeners.add(fn); return () => {this.listeners.delete(fn);};}
  run(text: string) {this.runs.push(text); return 'run-1';}
  command(name: string) {this.commands.push(name); return 'command-1';}
  cancel(id: string) {this.cancellations.push(id); return 'cancel';}
  approve(id: string) {this.approvals.push(id); return 'approval';}
  steer() {return 'steer';} async shutdown() {}
  emit(event: unknown) {this.listeners.forEach(fn => fn(event as ServerEvent));}
}
async function mount(columns = 80, rows = 24) {
  const client = new Client();
  const view = render(<App client={client} initialState={{...initialState, phase: 'idle', workspace: '/workspace', sessionId: 'guide-session'}}
    windowSize={{columns, rows}} publishSidebarSignal={() => {}}/>);
  const key = async (text: string) => {view.stdin.write(text); await pause(35);};
  const until = async (pattern: RegExp) => {
    const end = Date.now() + 2000;
    while (!pattern.test(view.lastFrame() ?? '') && Date.now() < end) await pause(10);
    assert.match(view.lastFrame()!, pattern);
  };
  await pause(40);
  return {...view, client, key, until};
}
test('guide preserves a Chinese draft and cursor and refuses to overwrite it', async () => {
  const v = await mount(); await v.key('中文草稿'); await v.key('\x1b[D'); await v.key('\x14');
  await v.until(/功能区指引/);
  await v.key('1'); await v.until(/已有草稿/);
  await v.key('\x1b'); await v.key('中'); await v.until(/中文草中稿/);
  assert.deepEqual(v.client.runs, []); assert.deepEqual(v.client.commands, []);
});
test('examples only prefill and tips can be disabled and reopened through commands', async () => {
  const v = await mount(); await v.key('/guide off'); await v.key('\r');
  await v.key('\x14'); await v.until(/开提示/);
  await v.key('2'); await v.until(/我想修改的功能/); assert.deepEqual(v.client.runs, []);
  await v.key('\x15'); // clear current line, remaining multiline draft is retained
  await v.key('\x14'); await v.until(/功能区指引/);
  await v.key('\t'); await v.key('\r'); await v.until(/提示已开启/);
  assert.deepEqual(v.client.commands, []);
});
test('guide yields to incoming approval and never approves on its own', async () => {
  const v = await mount(32, 12); await v.key('执行任务'); await v.key('\r'); await v.key('保留草稿');
  await v.key('\x14'); await v.until(/功能区指引/);
  v.client.emit({v: 1, type: 'approval_request', id: 'run-1', approval_id: 'a1',
    call: {id: 'w', name: 'write_file', args: {path: 'a', content: 'b'}}, preview: '写入 a', decisions: ['once', 'deny']});
  await v.until(/等待审批/); assert.doesNotMatch(v.lastFrame()!, /功能区指引/);
  assert.deepEqual(v.client.approvals, []); assert.match(v.lastFrame()!, /保留草稿/);
  await v.key('\x03'); assert.deepEqual(v.client.cancellations, ['run-1']);
});
test('guide does not turn an example into a steering message while running', async () => {
  const v = await mount(); await v.key('执行任务'); await v.key('\r');
  await v.key('\x14'); await v.until(/功能区指引/); await v.key('1'); await v.until(/运行中/);
  await v.key('\x03'); assert.deepEqual(v.client.cancellations, ['run-1']);
  assert.deepEqual(v.client.runs, ['执行任务']);
});
test('guide is discoverable by mouse and unavailable plan navigation stays absent', async () => {
  const v = await mount(); const lines = v.lastFrame()!.split('\n');
  const row = lines.findIndex(line => line.includes('[ 指引 ]')); assert.ok(row >= 0);
  const column = stringWidth(lines[row]!.slice(0, lines[row]!.indexOf('[ 指引 ]'))) + 2;
  await v.key(`\x1b[<0;${column};${row + 1}M\x1b[<0;${column};${row + 1}m`);
  await v.until(/功能区指引/); await v.key('\x1b[C');
  assert.doesNotMatch(v.lastFrame()!, /看计划/); assert.deepEqual(v.client.commands, []);
});

test('opening guidance while reading history returns to the same position', async () => {
  const client = new Client();
  const entries = Array.from({length: 70}, (_, id) => ({id, kind: 'assistant' as const, text: `历史回复 ${id}：保留原来的阅读位置。`}));
  const view = render(<App client={client} initialState={{...initialState, phase: 'idle', entries, nextEntry: entries.length, hasStartedRun: true}}
    windowSize={{columns: 80, rows: 24}} publishSidebarSignal={() => {}}/>);
  const key = async (text: string) => {view.stdin.write(text); await pause(40);};
  await pause(50); await key('\x12');
  for (let i = 0; i < 4; i++) await key('\x1b[A');
  const before = view.lastFrame()!.match(/回看 · 下方 \d+ 行/)?.[0]; assert.ok(before);
  await key('\x14'); assert.match(view.lastFrame()!, /功能区指引/);
  await key('\x1b'); assert.equal(view.lastFrame()!.match(/回看 · 下方 \d+ 行/)?.[0], before);
  assert.deepEqual(client.commands, []); assert.deepEqual(client.runs, []);
});
