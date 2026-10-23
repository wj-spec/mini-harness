import assert from 'node:assert/strict';
import test from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {render} from 'ink-testing-library';
import {App} from '../src/app.js';
import type {BackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';

class Client {
  listeners = new Set<(event: ServerEvent) => void>();
  runs = 0;
  subscribe(listener: (event: ServerEvent) => void) {this.listeners.add(listener); return () => this.listeners.delete(listener);}
  run() {this.runs += 1; return `run-${this.runs}`;}
  cancel() {return 'cancel-1';}
  steer() {return 'steer-1';}
  approve() {}
  command() {return 'command-1';}
  shutdown() {return Promise.resolve();}
  emit(event: ServerEvent) {for (const listener of this.listeners) listener(event);}
}

const enter = async (stdin: {write: (chunk: string) => unknown}, text: string) => {
  stdin.write(text);
  stdin.write('\r');
  await pause(15);
};

async function mount() {
  const client = new Client();
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 100, rows: 30}}/>);
  await pause(20);
  client.emit({v: 1, type: 'ready', id: null, workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: [], session_purpose: 'user'} as unknown as ServerEvent);
  client.emit({v: 1, type: 'status', id: null, phase: 'idle'} as unknown as ServerEvent);
  await pause(20);
  return {client, ...view};
}

const done = (id: string, status: string, stopReason = ''): ServerEvent => ({
  v: 1, type: 'done', id,
  result: {text: '', steps: 1, status, usage: {}, cost_usd: 0, run_id: id, stop_reason: stopReason},
} as unknown as ServerEvent);

test('bench: footer prints one outcome label and keeps the stop reason', async () => {
  const {client, stdin, lastFrame} = await mount();
  await enter(stdin, 'first task');
  client.emit(done('run-1', 'done'));
  await pause(20);
  const doneFrame = lastFrame() ?? '';
  assert.match(doneFrame, /本轮结束/);
  assert.doesNotMatch(doneFrame, /本轮结束 · 本轮结束/);

  await enter(stdin, 'second task');
  client.emit(done('run-2', 'cancelled'));
  await pause(20);
  assert.doesNotMatch(lastFrame() ?? '', /已取消 · 已取消/);

  await enter(stdin, 'third task');
  client.emit(done('run-3', 'stopped', 'token_budget'));
  await pause(20);
  const stoppedFrame = lastFrame() ?? '';
  assert.match(stoppedFrame, /token_budget/);
  assert.doesNotMatch(stoppedFrame, /已停止 · 已停止/);
});
