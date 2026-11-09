import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {App} from '../src/app.js';
import {initialState, type Entry} from '../src/view-state.js';
import type {BackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';

afterEach(cleanup);

class Client implements BackendClient {
  listeners = new Set<(event: ServerEvent) => void>(); commands: string[] = [];
  capabilities() {return [];} start() {}
  subscribe(fn: (event: ServerEvent) => void) {this.listeners.add(fn); return () => {this.listeners.delete(fn);};}
  run(text: string) {return `run-${this.commands.push(text)}`;}
  command(command: string) {return `command-${this.commands.push(command)}`;}
  cancel() {return 'cancel';} steer() {return 'steer';} approve() {return 'approval';} async shutdown() {}
}

const text = (id: number, kind: Entry['kind'], value: string, extra: Partial<Entry> = {}): Entry => ({id, kind, text: value, ...extra});

function historyEntries(): Entry[] {
  const entries: Entry[] = [];
  for (let index = 0; index < 10; index += 1) entries.push(text(index, 'assistant', `早期回复 ${index}：用于填充阅读区。`));
  entries.push(text(10, 'task', '第三轮：排查关键结论所在位置', {runId: 'run-3', runStatus: 'error'}));
  entries.push(text(11, 'tool', '工具 › bash', {call: {id: 'c-11', name: 'bash', args: {command: 'npm test'}}, output: '1 failing test: 关键结论断言失败', ok: false, tier: 'exec'}));
  entries.push(text(12, 'assistant', '最终结论：关键结论 已经确认。'));
  for (let index = 13; index < 53; index += 1) entries.push(text(index, 'assistant', `后续回复 ${index}：把定位目标挤出当前视口。`));
  return entries;
}

async function mount(columns = 80, rows = 24) {
  const entries = historyEntries();
  const client = new Client();
  const view = render(<App client={client} initialState={{...initialState, phase: 'idle', workspace: '/workspace', sessionId: 'session',
    entries, nextEntry: entries.length, hasStartedRun: true}} windowSize={{columns, rows}} publishSidebarSignal={() => {}}/>);
  const key = async (value: string) => {view.stdin.write(value); await pause(35);};
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

test('find searches the transcript and jumps the reading view to the matching line', async () => {
  const view = await mount();
  assert.doesNotMatch(view.lastFrame()!, /关键结论/);
  await view.key('/find 关键结论'); await view.key('\r');
  assert.match(view.lastFrame()!, /定位 · 查找 关键结论 · \d+ 处/);
  await view.click('[ 定位 ]');
  assert.match(view.lastFrame()!, /第三轮：排查关键结论所在位置/);
  assert.match(view.lastFrame()!, /回看/);
  assert.doesNotMatch(view.lastFrame()!, /定位 · 查找 关键结论/);
});

test('rounds locate jumps to the start of the selected round', async () => {
  const view = await mount(); await view.key('/rounds'); await view.key('\r');
  assert.match(view.lastFrame()!, /按轮定位 · 1 轮/);
  assert.match(view.lastFrame()!, /第 1 轮 · error/);
  await view.click('[ 定位 ]');
  assert.match(view.lastFrame()!, /第三轮：排查关键结论所在位置/);
});

test('errors locate jumps to a failed tool call while keeping its raw output available', async () => {
  const view = await mount(); await view.key('/errors'); await view.key('\r');
  assert.match(view.lastFrame()!, /错误定位 · 2 处/);
  assert.match(view.lastFrame()!, /工具错误 · bash · 1 failing test/);
  assert.match(view.lastFrame()!, /运行 error · 第三轮/);
  await view.click('[ 下一条 ]');
  await view.click('[ 定位 ]');
  assert.match(view.lastFrame()!, /1 failing test/);
});

test('empty searches keep the transcript and explain how to narrow the query', async () => {
  const view = await mount(); await view.key('/find 不存在的词'); await view.key('\r');
  assert.match(view.lastFrame()!, /没有找到“不存在的词”/);
  assert.doesNotMatch(view.lastFrame()!, /定位 ·/);
  await view.key('/find'); await view.key('\r');
  assert.match(view.lastFrame()!, /用法：\/find <关键词>/);
});

test('returning from the locator restores composer focus without touching the transcript', async () => {
  const view = await mount(); await view.key('/errors'); await view.key('\r');
  await view.key('\x1b');
  await view.key('新草稿');
  assert.match(view.lastFrame()!, /新草稿/);
});
