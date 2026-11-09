import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {App} from '../src/app.js';
import {initialState} from '../src/view-state.js';
import type {BackendClient} from '../src/backend.js';
import type {BackendCommand, CommandArgs, PermissionOverview, ServerEvent} from '../src/protocol.js';

afterEach(cleanup);
const WS = 'ws-' + 'a'.repeat(12);
const SS = 'ss-' + 'b'.repeat(12);
const overview = (overrides: Partial<PermissionOverview> = {}): PermissionOverview => ({
  autonomy: {level: 'edits', label: '自动改文件（工作区）', source: 'cli', requested: 'edits', cap: null},
  grants: [
    {id: WS, kind: 'workspace', scope: 'workspace', tool: null, target: '/workspace', workspace: '/workspace', validity: 'process', revocable: true},
    {id: SS, kind: 'session', scope: 'session', tool: 'bash', target: 'npm test', workspace: '/workspace', validity: 'process', revocable: true},
  ],
  persistent: [
    {id: 'ur-' + 'c'.repeat(12), source: 'user', path: '/home/u/.vibe/permissions.json', rule: '- [user] bash prefix=pytest → allow（本地测试）', validity: 'file', revocable: true, reason: '用户策略可在此撤销（会写入用户策略文件）'},
    {id: 'pr-' + 'd'.repeat(12), source: 'project', path: '/workspace/.vibe/permissions.json', rule: '- [project] bash prefix=rm → once', validity: 'file', revocable: false, reason: '项目策略只能收紧；撤销会放宽权限，请修改仓库文件'},
  ],
  error: null, revoked: null, revoked_rule: null, workspace: '/workspace', session_id: 'session', ...overrides,
});

class Client implements BackendClient {
  listeners = new Set<(event: ServerEvent) => void>();
  commands: {id: string; command: BackendCommand; args?: CommandArgs}[] = [];
  caps: string[] = ['permission-panel']; runs: string[] = [];
  capabilities() {return this.caps;} start() {}
  subscribe(fn: (event: ServerEvent) => void) {this.listeners.add(fn); return () => {this.listeners.delete(fn);};}
  run(text: string) {this.runs.push(text); return `run-${this.runs.length}`;}
  command(command: BackendCommand, args?: CommandArgs) {const id = `command-${this.commands.length + 1}`; this.commands.push({id, command, args}); return id;}
  cancel() {return 'cancel';} steer() {return 'steer';} approve() {return 'approval';} async shutdown() {}
  emit(event: unknown) {for (const fn of this.listeners) fn(event as ServerEvent);}
  page(id: string, value: PermissionOverview) {this.emit({v: 1, type: 'permission_overview', id, ...value});}
}

async function mount(caps?: string[]) {
  const client = new Client(); if (caps) client.caps = caps;
  const view = render(<App client={client} initialState={{...initialState, phase: 'idle', workspace: '/workspace', sessionId: 'session'}}
    windowSize={{columns: 100, rows: 30}} publishSidebarSignal={() => {}}/>);
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

test('the permission panel shows sources, validity and persistent rules without changing approval rules', async () => {
  const view = await mount(); await view.key('/permissions'); await view.key('\r');
  assert.deepEqual(view.client.commands[0], {id: 'command-1', command: 'permissions', args: {action: 'list'}});
  view.client.page('command-1', overview()); await pause(40);
  const frame = view.lastFrame()!;
  assert.match(frame, /权限/);
  assert.match(frame, /档位：edits/);
  assert.match(frame, /来源：本进程 CLI/);
  assert.match(frame, /进程临时授权 · 2 条/);
  assert.match(frame, /workspace · \/workspace/);
  assert.match(frame, /session · bash · npm test/);
  assert.match(frame, /持久策略 · 2 条/);
  assert.match(frame, /\[user\] bash prefix=pytest/);
  assert.match(frame, /不可撤销/);
  assert.match(frame, /退出后失效/);
});

test('revoking one grant refreshes the panel with the remaining process grants', async () => {
  const view = await mount(); await view.key('/permissions'); await view.key('\r');
  view.client.page('command-1', overview()); await pause(40);
  await view.click('[ 撤销所选 ]');
  assert.deepEqual(view.client.commands[1], {id: 'command-2', command: 'permissions', args: {action: 'revoke', grant_id: WS}});
  view.client.page('command-2', overview({grants: [overview().grants[1]!], revoked: {id: WS, kind: 'workspace', scope: 'workspace', tool: null, target: '/workspace'}}));
  await pause(40);
  const frame = view.lastFrame()!;
  assert.match(frame, /已撤销 workspace/);
  assert.match(frame, /剩余 1 条/);
  assert.match(frame, /session · bash · npm test/);
  assert.doesNotMatch(frame, /1\. workspace/);
});

test('a failed revoke keeps the original grants and shows the backend reason', async () => {
  const view = await mount(); await view.key('/permissions'); await view.key('\r');
  view.client.page('command-1', overview()); await pause(40);
  await view.click('[ 撤销所选 ]');
  view.client.emit({v: 1, type: 'error', id: 'command-2', code: 'permission_grant_not_found', message: '没有可撤销的进程内授权'});
  await pause(40);
  assert.match(view.lastFrame()!, /没有可撤销的进程内授权/);
  assert.match(view.lastFrame()!, /进程临时授权 · 2 条/);
});

test('a stale overview for another scope is ignored and the page is kept', async () => {
  const view = await mount(); await view.key('/permissions'); await view.key('\r');
  view.client.page('command-1', overview()); await pause(40);
  await view.click('[ 刷新 ]');
  view.client.page('command-2', overview({workspace: '/foreign', grants: []})); await pause(40);
  assert.match(view.lastFrame()!, /回执不匹配|保留/);
  assert.match(view.lastFrame()!, /进程临时授权 · 2 条/);
});

test('without the permission-panel capability the command keeps the legacy text result', async () => {
  const view = await mount([]); await view.key('/permissions'); await view.key('\r');
  assert.deepEqual(view.client.commands[0], {id: 'command-1', command: 'permissions', args: undefined});
  view.client.emit({v: 1, type: 'command_result', id: 'command-1', command: 'permissions', text: 'workspace 授权：未开启；session 精确规则：0 条。重启后重新审批。'});
  await pause(40);
  assert.match(view.lastFrame()!, /workspace 授权：未开启/);
});


test('revoking a persistent user rule requires confirmation and reports the remaining policy', async () => {
  const view = await mount(); await view.key('/permissions'); await view.key('\r');
  view.client.page('command-1', overview()); await pause(40);
  await view.click('[ 后一条 ]'); await view.click('[ 后一条 ]');
  assert.match(view.lastFrame()!, /\[user\] bash prefix=pytest/);
  assert.match(view.lastFrame()!, /可撤销/);
  await view.click('[ 撤销所选 ]');
  assert.match(view.lastFrame()!, /再次确认/);
  assert.equal(view.client.commands.length, 1);
  await view.click('[ 确认撤销规则 ]');
  const ruleId = 'ur-' + 'c'.repeat(12);
  assert.deepEqual(view.client.commands[1], {id: 'command-2', command: 'permissions', args: {action: 'revoke-rule', rule_id: ruleId}});
  view.client.page('command-2', overview({persistent: [overview().persistent[1]!],
    revoked_rule: {id: ruleId, source: 'user', path: '/home/u/.vibe/permissions.json', rule: '- [user] bash prefix=pytest → allow（本地测试）'}}));
  await pause(40);
  assert.match(view.lastFrame()!, /已撤销持久规则/);
  assert.match(view.lastFrame()!, /剩余 0 条用户策略/);
  assert.doesNotMatch(view.lastFrame()!, /bash prefix=pytest → allow（本地测试） · 可撤销/);
});
