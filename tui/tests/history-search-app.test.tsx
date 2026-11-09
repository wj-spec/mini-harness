import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {App} from '../src/app.js';
import {initialState, type Entry} from '../src/view-state.js';
import type {BackendClient, SessionOpenTarget} from '../src/backend.js';
import type {BackendCommand, CommandArgs, ServerEvent} from '../src/protocol.js';

afterEach(cleanup);
const identity = 'c'.repeat(64);

class Client implements BackendClient {
  listeners = new Set<(event: ServerEvent) => void>();
  commands: {id: string; command: BackendCommand; args?: CommandArgs}[] = [];
  opens: {target: SessionOpenTarget; resolve: (client: BackendClient) => void; reject: (error: Error) => void}[] = [];
  caps: string[] = ['history-search', 'rounds-ledger']; runs: string[] = [];
  capabilities() {return this.caps;} start() {}
  openSession(target: SessionOpenTarget) {return new Promise<BackendClient>((resolve, reject) => this.opens.push({target, resolve, reject}));}
  subscribe(fn: (event: ServerEvent) => void) {this.listeners.add(fn); return () => {this.listeners.delete(fn);};}
  run(text: string) {this.runs.push(text); return `run-${this.runs.length}`;}
  command(command: BackendCommand, args?: CommandArgs) {const id = `command-${this.commands.length + 1}`; this.commands.push({id, command, args}); return id;}
  cancel() {return 'cancel';} steer() {return 'steer';} approve() {return 'approval';} async shutdown() {}
  emit(event: unknown) {for (const fn of this.listeners) fn(event as ServerEvent);}
}

const entries: Entry[] = [
  {id: 0, kind: 'assistant', text: '开头说明'},
  {id: 1, kind: 'task', text: '账本第一轮目标', runId: 'run-1', runStatus: 'done'},
  {id: 2, kind: 'assistant', text: '第一轮结论：关键内容'},
  {id: 3, kind: 'task', text: '账本第二轮目标', runId: 'run-2', runStatus: 'error'},
  {id: 4, kind: 'assistant', text: '第二轮结论'},
];

async function mount(options: {caps?: string[]; initialFocus?: string; onSessionSwitch?: (client: BackendClient, extra?: {focusQuery?: string}) => void | Promise<void>} = {}) {
  const client = new Client(); if (options.caps) client.caps = options.caps;
  const view = render(<App client={client} initialFocus={options.initialFocus} onSessionSwitch={options.onSessionSwitch}
    initialState={{...initialState, phase: 'idle', workspace: '/workspace', sessionId: 'session', entries, nextEntry: entries.length, hasStartedRun: true}}
    windowSize={{columns: 80, rows: 24}} publishSidebarSignal={() => {}}/>);
  const key = async (text: string) => {view.stdin.write(text); await pause(35);};
  const click = async (label: string) => {
    const until = Date.now() + 1500;
    while (!view.lastFrame()?.includes(label) && Date.now() < until) await pause(5);
    await pause(30); const lines = view.lastFrame()!.split('\n'); const index = lines.findIndex(line => line.includes(label));
    assert.ok(index >= 0, `missing ${label}: ${view.lastFrame()}`);
    const column = stringWidth(lines[index]!.slice(0, lines[index]!.indexOf(label))) + 2;
    await key(`\x1b[<0;${column};${index + 1}M\x1b[<0;${column};${index + 1}m`);
  };
  await pause(50); return {...view, client, key, click};
}

const searchPage = (overrides: Record<string, unknown> = {}) => ({v: 1, type: 'history_search', id: 'command-1', workspace: '/workspace', session_id: 'session',
  purpose: 'test', query: '结论', matches: [
    {session_id: '20261108-111111-000001', identity, title: '旧会话甲', updated_at: '2026-11-08T10:00:00Z', role: 'assistant',
      snippet: '…旧会话甲中的跨会话结论…', ts: '2026-11-08T10:00:01Z', message_index: 2},
    {session_id: '20261108-222222-000002', identity, title: '旧会话乙', updated_at: '2026-11-08T09:00:00Z', role: 'user',
      snippet: '…另一个结论…', ts: null, message_index: 0},
  ], next_cursor: null, skipped: 0, truncated: false, ...overrides});

test('history search lists old-session matches and opens the selected session with the query as focus', async () => {
  let handed: {client: BackendClient; extra?: {focusQuery?: string}} | null = null;
  const view = await mount({onSessionSwitch: (client, extra) => {handed = {client, extra};}});
  await view.key('/history 结论'); await view.key('\r');
  assert.deepEqual(view.client.commands[0], {id: 'command-1', command: 'search', args: {query: '结论'}});
  assert.match(view.lastFrame()!, /正在检索会话记录/);
  view.client.emit(searchPage()); await pause(40);
  assert.match(view.lastFrame()!, /历史检索 · 结论 · 2 条/);
  assert.match(view.lastFrame()!, /旧会话甲/); assert.match(view.lastFrame()!, /旧会话乙/);
  await view.click('[ 下一条 ]'); await view.click('[ 打开会话 ]');
  assert.deepEqual(view.client.opens[0]!.target, {workspace: '/workspace', sessionId: '20261108-222222-000002', identity});
  const next = new Client(); view.client.opens[0]!.resolve(next); await pause(60);
  assert.equal((handed as {client: BackendClient} | null)?.client, next);
  assert.deepEqual((handed as {extra?: {focusQuery?: string}} | null)?.extra, {focusQuery: '结论'});
});

test('a stale search page for another scope keeps the current matches and reports the mismatch', async () => {
  const view = await mount(); await view.key('/history 结论'); await view.key('\r');
  view.client.emit(searchPage({next_cursor: 'cursor-2'})); await pause(40);
  await view.click('[ 更多 ]');
  assert.deepEqual(view.client.commands[1]!.args, {query: '结论', cursor: 'cursor-2'});
  view.client.emit(searchPage({id: 'command-2', workspace: '/foreign'})); await pause(40);
  assert.match(view.lastFrame()!, /回执不匹配/);
  assert.match(view.lastFrame()!, /旧会话甲/);
});

test('rounds ledger shows verification state and locates rounds present in the transcript', async () => {
  const view = await mount(); await view.key('/rounds'); await view.key('\r');
  assert.deepEqual(view.client.commands[0], {id: 'command-1', command: 'rounds', args: {}});
  assert.match(view.lastFrame()!, /正在读取轮次账本/);
  view.client.emit({v: 1, type: 'rounds_ledger', id: 'command-1', workspace: '/workspace', session_id: 'session', purpose: 'test',
    total: 3, skipped: 0, truncated: false, rounds: [
      {index: 3, run_id: 'run-3', goal: '已归档旧轮次', goal_truncated: false, task_kind: 'run', status: 'error', stop_reason: null,
        steps: 2, cost_usd: 0.001, verified: false, saved_at: '2026-11-09T10:00:00Z'},
      {index: 2, run_id: 'run-2', goal: '账本第二轮目标', goal_truncated: false, task_kind: 'plan', status: 'done', stop_reason: null,
        steps: 3, cost_usd: 0.002, verified: true, saved_at: '2026-11-09T09:00:00Z'},
      {index: 1, run_id: 'run-1', goal: '账本第一轮目标', goal_truncated: false, task_kind: 'run', status: 'done', stop_reason: null,
        steps: 1, cost_usd: 0.0, verified: null, saved_at: '2026-11-09T08:00:00Z'},
    ]});
  await pause(40);
  assert.match(view.lastFrame()!, /轮次账本 · 3 轮/);
  assert.match(view.lastFrame()!, /第 3 轮 · error · 验证未通过 · 已归档旧轮次/);
  assert.match(view.lastFrame()!, /第 2 轮 · done · 已验证 · 账本第二轮目标/);
  await view.click('[ 下一条 ]'); await view.click('[ 下一条 ]'); await view.click('[ 定位 ]');
  assert.match(view.lastFrame()!, /回看/); assert.match(view.lastFrame()!, /账本第一轮目标/);
  await view.key('/rounds'); await view.key('\r');
  view.client.emit({v: 1, type: 'rounds_ledger', id: 'command-2', workspace: '/workspace', session_id: 'session', purpose: 'test',
    total: 1, skipped: 0, truncated: false, rounds: [
      {index: 1, run_id: 'run-old', goal: '不在正文的旧轮次', goal_truncated: false, task_kind: 'run', status: 'done', stop_reason: null,
        steps: 1, cost_usd: 0.0, verified: false, saved_at: null},
    ]});
  await pause(40); await view.click('[ 定位 ]');
  assert.match(view.lastFrame()!, /不在当前正文/);
});

test('a new session opened from a search result focuses the matching line', async () => {
  const view = await mount({initialFocus: '关键内容'});
  assert.match(view.lastFrame()!, /回看/);
  assert.match(view.lastFrame()!, /第一轮结论：关键内容/);
});

test('rounds falls back to transcript rounds when the ledger capability is missing', async () => {
  const view = await mount({caps: []}); await view.key('/rounds'); await view.key('\r');
  assert.equal(view.client.commands.length, 0);
  assert.match(view.lastFrame()!, /按轮定位 · 2 轮/);
});
