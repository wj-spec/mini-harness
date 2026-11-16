import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import {App} from '../src/app.js';
import {initialState} from '../src/view-state.js';
import stringWidth from 'string-width';
import type {BackendClient} from '../src/backend.js';
import type {ServerEvent, CommandArgs} from '../src/protocol.js';
afterEach(cleanup);

class HomeClient implements BackendClient {
  listeners = new Set<(event: ServerEvent) => void>();
  runs: string[] = [];
  commands: {name: string; args?: CommandArgs}[] = [];
  capabilities() {return ['run', 'cancel', 'commands', 'workbench-settings', 'model-settings'];}
  start() {}
  subscribe(fn: (event: ServerEvent) => void) {this.listeners.add(fn); return () => {this.listeners.delete(fn);};}
  emit(event: unknown) {this.listeners.forEach(fn => fn(event as ServerEvent));}
  command(name: string, args?: CommandArgs) {
    this.commands.push({name, args});
    const id = 'c' + this.commands.length;
    if (args && 'action' in args && args.action === 'list') {
      setTimeout(() => this.emit({v: 1, type: 'settings_overview', id, limits: {max_steps: 50, max_run_seconds: 1800, max_run_tokens: 0, max_run_cost_usd: 0},
        goal_limits: {max_segments: 4, max_seconds: 7200, max_tokens: 0, max_cost_usd: 0},
        sources: {}, goal_sources: {}, revision: 'a'.repeat(64), path: '/repo/.env', saved: {}, message: '',
        workspace: '/repo/mini-harness', session_id: 'session-123'}), 5);
    }
    return id;
  }
  run(text: string) {this.runs.push(text); return 'r1';}
  cancel() {return 'cancel';}
  approve() {return 'approve';}
  steer() {return 'steer';}
  async shutdown() {}
}

const metrics = {used_tokens: 123, limit_tokens: 5000, context_window: 64000, context_tokens: 800,
  done: 0, planned: 0, estimated: true, cost_usd: 0.02};

function mount() {
  const client = new HomeClient();
  const v = render(<App client={client} initialState={{...initialState, phase: 'idle' as const,
    workspace: '/repo/mini-harness', model: 'deepseek-chat', provider: 'openai', sessionId: 'session-123', metrics}}
    windowSize={{columns: 80, rows: 24}} publishSidebarSignal={() => {}}/>);
  const key = async (s: string) => {v.stdin.write(s); await pause(35);};
  return {v, client, key};
}

test('configured home leads with project and model status, not technical identifiers', async () => {
  const {v} = mount();
  await pause(50);
  const frame = v.lastFrame()!;
  assert.match(frame, /今天准备做点什么/);
  assert.match(frame, /项目  mini-harness/);
  assert.match(frame, /deepseek-chat · 已配置 · 尚未检测/);
  assert.match(frame, /了解这个项目/);
  assert.match(frame, /定位一个问题/);
  assert.match(frame, /实现一个修改/);
  assert.match(frame, /\[ 设置 \]/);
  assert.match(frame, /\[ 指引 \]/);
  assert.doesNotMatch(frame, /session-123/);
  assert.doesNotMatch(frame, /openai/);
  assert.doesNotMatch(frame, /\/repo\/mini-harness/);
  assert.doesNotMatch(frame, /任务累计/);
});

test('number keys fill examples into the draft and never send or overwrite it', async () => {
  const {v, client, key} = mount();
  await pause(50);
  await key('1');
  assert.match(v.lastFrame()!, /读一下这个仓库/);
  assert.deepEqual(client.runs, []);
  await key('2');
  assert.match(v.lastFrame()!, /读一下这个仓库/);
  assert.deepEqual(client.runs, []);
  await key('\r');
  assert.equal(client.runs.length, 1);
  assert.match(client.runs[0]!, /读一下这个仓库.*README/);
});

test('settings close restores the typed draft and keeps the cursor at the end', async () => {
  const {v, key} = mount();
  await pause(50);
  await key('中文草稿');
  await key('\x14');
  for (let i = 0; i < 4; i += 1) await key('\x1b[C');
  await key('\r');
  await pause(60);
  assert.match(v.lastFrame()!, /设置中心/);
  await key('\x1b');
  await pause(40);
  assert.match(v.lastFrame()!, /中文草稿/);
  await key('好');
  assert.match(v.lastFrame()!, /中文草稿好/);
});

test('window size changes keep the typed home draft', async () => {
  const {v, client, key} = mount();
  await pause(50);
  await key('中文草稿');
  v.rerender(<App client={client} initialState={{...initialState, phase: 'idle' as const,
    workspace: '/repo/mini-harness', model: 'deepseek-chat', provider: 'openai', sessionId: 'session-123', metrics}}
    windowSize={{columns: 100, rows: 30}} publishSidebarSignal={() => {}}/>);
  await pause(50);
  assert.match(v.lastFrame()!, /中文草稿/);
  assert.match(v.lastFrame()!, /今天准备做点什么/);
});

test('settings close keeps the transcript reading position', async () => {
  const client = new HomeClient();
  const entries = [
    {id: 0, kind: 'task' as const, text: '读一下这个仓库'},
    {id: 1, kind: 'assistant' as const, text: Array.from({length: 40}, (_, i) => `第 ${i + 1} 行说明`).join('\n')},
  ];
  const v = render(<App client={client} initialState={{...initialState, phase: 'idle' as const,
    workspace: '/repo/mini-harness', model: 'deepseek-chat', provider: 'openai', sessionId: 'session-123',
    entries, nextEntry: 2, hasStartedRun: true}}
    windowSize={{columns: 80, rows: 24}} publishSidebarSignal={() => {}}/>);
  const key = async (s: string) => {v.stdin.write(s); await pause(35);};
  await pause(50);
  await key('\x10');
  assert.match(v.lastFrame()!, /回看/);
  await key('\x14');
  for (let i = 0; i < 4; i += 1) await key('\x1b[C');
  await key('\r');
  await pause(50);
  await key('\x1b');
  await pause(40);
  assert.match(v.lastFrame()!, /回看/);
});

test('home example buttons fill the draft on click and never overwrite it', async () => {
  const {v, client, key} = mount();
  await pause(50);
  const lines = v.lastFrame()!.split('\n');
  const row = lines.findIndex(line => line.includes('[ 了解这个项目 ]'));
  assert.ok(row >= 0);
  const column = stringWidth(lines[row]!.slice(0, lines[row]!.indexOf('[ 了解这个项目 ]'))) + 2;
  await key(`\x1b[<0;${column};${row + 1}M\x1b[<0;${column};${row + 1}m`);
  assert.match(v.lastFrame()!, /读一下这个仓库/);
  assert.deepEqual(client.runs, []);
  const second = v.lastFrame()!.split('\n');
  const secondRow = second.findIndex(line => line.includes('[ 定位一个问题 ]'));
  const secondColumn = stringWidth(second[secondRow]!.slice(0, second[secondRow]!.indexOf('[ 定位一个问题 ]'))) + 2;
  await key(`\x1b[<0;${secondColumn};${secondRow + 1}M\x1b[<0;${secondColumn};${secondRow + 1}m`);
  assert.match(v.lastFrame()!, /读一下这个仓库/);
  assert.match(v.lastFrame()!, /已有草稿/);
  assert.deepEqual(client.runs, []);
});

test('project and model summary buttons reveal the full path and open model settings', async () => {
  const {v, key} = mount();
  await pause(50);
  const click = async (label: string) => {
    const lines = v.lastFrame()!.split('\n');
    const row = lines.findIndex(line => line.includes(`[ ${label} ]`));
    assert.ok(row >= 0, label);
    const column = stringWidth(lines[row]!.slice(0, lines[row]!.indexOf(`[ ${label} ]`))) + 2;
    await key(`\x1b[<0;${column};${row + 1}M\x1b[<0;${column};${row + 1}m`);
  };
  assert.doesNotMatch(v.lastFrame()!, /\/repo\/mini-harness/);
  await click('查看项目');
  assert.match(v.lastFrame()!, /项目  mini-harness  \/repo\/mini-harness/);
  await click('收起路径');
  assert.doesNotMatch(v.lastFrame()!, /\/repo\/mini-harness/);
  await click('更改');
  assert.match(v.lastFrame()!, /模型设置/);
});
