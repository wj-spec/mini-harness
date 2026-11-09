import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {App} from '../src/app.js';
import {initialState} from '../src/view-state.js';
import type {BackendClient, SessionOpenTarget} from '../src/backend.js';
import type {BackendCommand, CommandArgs, ServerEvent, SessionCatalogRow} from '../src/protocol.js';

afterEach(cleanup);
const identity = 'a'.repeat(64);
const catalogRow = (id: string, title: string, preview: string): SessionCatalogRow => ({session_id: id, identity, title,
  updated_at: '2026-11-09T00:01:00Z', message_count: 2, preview, truncated: false});

class Client implements BackendClient {
  listeners = new Set<(event: ServerEvent) => void>();
  commands: {id: string; command: BackendCommand; args?: CommandArgs}[] = [];
  runs: string[] = [];
  opens: {target: SessionOpenTarget; signal?: AbortSignal; resolve: (client: BackendClient) => void; reject: (error: Error) => void}[] = [];
  caps: string[] = ['session-navigation']; shutdowns = 0;
  capabilities() {return this.caps;} start() {}
  openSession(target: SessionOpenTarget, signal?: AbortSignal) {return new Promise<BackendClient>((resolve, reject) => this.opens.push({target, signal, resolve, reject}));}
  subscribe(fn: (event: ServerEvent) => void) {this.listeners.add(fn); return () => {this.listeners.delete(fn);};}
  run(text: string) {this.runs.push(text); return `run-${this.runs.length}`;}
  command(command: BackendCommand, args?: CommandArgs) {const id = `command-${this.commands.length + 1}`; this.commands.push({id, command, args}); return id;}
  cancel() {return 'cancel';} steer() {return 'steer';} approve() {return 'approval';}
  async shutdown() {this.shutdowns += 1;}
  emit(event: unknown) {for (const fn of this.listeners) fn(event as ServerEvent);}
  page(id: string, records: SessionCatalogRow[], next: string | null = null) {
    this.emit({v: 1, type: 'session_catalog', id, workspace: '/workspace', session_id: 'session', purpose: 'test', records, next_cursor: next, skipped: 0});
  }
}

async function mount(options: {caps?: string[]; onSessionSwitch?: (client: BackendClient) => Promise<void> | void; columns?: number; rows?: number} = {}) {
  const client = new Client(); if (options.caps) client.caps = options.caps;
  const view = render(<App client={client} onSessionSwitch={options.onSessionSwitch}
    initialState={{...initialState, phase: 'idle', workspace: '/workspace', sessionId: 'session'}}
    windowSize={{columns: options.columns ?? 80, rows: options.rows ?? 24}} publishSidebarSignal={() => {}}/>);
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

test('browsing old sessions is read-only, pages by cursor, and returning resumes composing', async () => {
  const view = await mount();
  await view.key('/sessions'); await view.key('\r');
  assert.deepEqual(view.client.commands[0], {id: 'command-1', command: 'sessions', args: {}});
  view.client.page('command-1', [catalogRow('20261109-010203-123456', '旧会话标题', '你：旧会话目标\n\n助手：上次已保存的结果')], 'cursor-next');
  await pause(40);
  assert.match(view.lastFrame()!, /旧会话标题/);
  assert.match(view.lastFrame()!, /上次已保存的结果/);
  assert.match(view.lastFrame()!, /打开不会自动运行/);
  await view.click('[ 更早 ]');
  assert.deepEqual(view.client.commands[1]!.args, {cursor: 'cursor-next'});
  view.client.page('command-2', [catalogRow('20261109-020304-654321', '更早的会话', '你：更早的目标')]); await pause(40);
  assert.match(view.lastFrame()!, /更早的会话/);
  await view.click('[ 较新 ]');
  assert.deepEqual(view.client.commands[2]!.args, {});
  view.client.page('command-3', [catalogRow('20261109-010203-123456', '旧会话标题', '你：旧会话目标')]); await pause(40);
  await view.click('[ 返回 ]'); await view.key('新草稿');
  assert.match(view.lastFrame()!, /新草稿/); assert.deepEqual(view.client.runs, []);
});

test('opening a selected session hands the new client over without shutting down or starting a run', async () => {
  let handed: BackendClient | null = null;
  const view = await mount({onSessionSwitch: async client => {handed = client;}});
  await view.key('/sessions'); await view.key('\r');
  view.client.page('command-1', [catalogRow('20261109-010203-123456', '要打开的旧会话', '你：旧目标')]); await pause(40);
  await view.click('[ 打开会话 ]');
  assert.deepEqual(view.client.opens[0]!.target, {workspace: '/workspace', sessionId: '20261109-010203-123456', identity});
  assert.match(view.lastFrame()!, /正在打开/);
  const next = new Client(); view.client.opens[0]!.resolve(next); await pause(60);
  assert.equal(handed, next); assert.deepEqual(view.client.runs, []); assert.equal(view.client.shutdowns, 0);
});

test('opening is refused while a run is active and the current session stays untouched', async () => {
  const view = await mount({onSessionSwitch: async () => {}});
  await view.key('当前运行'); await view.key('\r');
  await view.key('/sessions'); await view.key('\r');
  view.client.page('command-1', [catalogRow('20261109-010203-123456', '候选旧会话', '你：旧目标')]); await pause(40);
  await view.click('[ 打开会话 ]'); await pause(40);
  assert.equal(view.client.opens.length, 0); assert.match(view.lastFrame()!, /运行中|结束后/);
  await view.key('\x03');
  view.client.emit({v: 1, type: 'done', id: 'run-1', result: {status: 'cancelled', summary: '', steps: 1, input_tokens: 0, output_tokens: 0, cost: 0}});
  await pause(60);
  await view.click('[ 打开会话 ]');
  assert.deepEqual(view.client.opens[0]!.target, {workspace: '/workspace', sessionId: '20261109-010203-123456', identity});
});

test('a failed or stale open keeps the current session and reports the reason', async () => {
  const view = await mount({onSessionSwitch: async () => {throw new Error('不应切换');}});
  await view.key('/sessions'); await view.key('\r');
  view.client.page('command-1', [catalogRow('20261109-010203-123456', '旧会话 A', '读到的预览')], 'cursor-next'); await pause(40);
  await view.click('[ 刷新 ]');
  view.client.emit({v: 1, type: 'session_catalog', id: 'command-2', workspace: '/foreign', session_id: 'session', purpose: 'test',
    records: [catalogRow('20261109-030405-999999', '别的工作区', '其他来源')], next_cursor: null, skipped: 0});
  await pause(40);
  assert.match(view.lastFrame()!, /回执不匹配/); assert.doesNotMatch(view.lastFrame()!, /别的工作区/);
  await view.click('[ 打开会话 ]');
  view.client.opens[0]!.reject(new Error('所选会话已删除、替换或用途变化')); await pause(60);
  assert.match(view.lastFrame()!, /打开失败|所选会话已删除/); assert.match(view.lastFrame()!, /旧会话 A/);
});

test('old sessions stay browsable during a run only when the backend supports structured navigation', async () => {
  const plain = await mount({caps: []}); await plain.key('/sessions'); await plain.key('\r');
  assert.equal(plain.client.commands.length, 0); assert.match(plain.lastFrame()!, /不支持|未连接/);
  await plain.key('\x1b[D'); // 草稿光标测试入口不依赖后端能力
  const busy = await mount(); await busy.key('当前运行'); await busy.key('\r');
  await busy.key('/sessions'); await busy.key('\r');
  assert.deepEqual(busy.client.commands[0], {id: 'command-1', command: 'sessions', args: {}});
});
