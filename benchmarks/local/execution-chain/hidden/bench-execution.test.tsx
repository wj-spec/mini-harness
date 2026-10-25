import assert from 'node:assert/strict';
import test, {afterEach} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import {App} from '../src/app.js';
import type {BackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';

afterEach(cleanup);

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

const call = {id: 'c1', name: 'bash', args: {command: 'false'}};

async function mount() {
  const client = new Client();
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 100, rows: 30}}/>);
  await pause(20);
  client.emit({v: 1, type: 'ready', id: null, workspace: '/tmp/repo', model: 'fake', session_id: 'test', capabilities: [], session_purpose: 'user'} as unknown as ServerEvent);
  client.emit({v: 1, type: 'status', id: null, phase: 'idle'} as unknown as ServerEvent);
  await pause(20);
  return {client, ...view};
}

test('bench: tool cards show execution status and exit code', async () => {
  const {client, stdin, lastFrame} = await mount();
  stdin.write('do it');
  await pause(15);
  stdin.write('\r');
  await pause(25);
  client.emit({v: 1, type: 'tool_call', id: 'run-1', call} as unknown as ServerEvent);
  await pause(15);
  client.emit({v: 1, type: 'tool_result', id: 'run-1', call, ok: false, output: 'exit=2',
    execution: {status: 'failed', exit_code: 2}} as unknown as ServerEvent);
  await pause(20);
  assert.match(lastFrame() ?? '', /非零退出 · exit=2/);

  const second = {id: 'c2', name: 'bash', args: {command: 'true'}};
  client.emit({v: 1, type: 'tool_call', id: 'run-1', call: second} as unknown as ServerEvent);
  await pause(15);
  client.emit({v: 1, type: 'tool_result', id: 'run-1', call: second, ok: true, output: 'ok',
    execution: {status: 'succeeded', exit_code: 0}} as unknown as ServerEvent);
  await pause(20);
  assert.match(lastFrame() ?? '', /零退出 · exit=0/);
});
