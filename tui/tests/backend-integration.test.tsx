import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import {mkdir, mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as pause} from 'node:timers/promises';
import {test} from 'node:test';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import {App} from '../src/app.js';
import {createBackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

async function waitFor(condition: () => boolean, timeoutMs = 15000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!condition()) {
    if (Date.now() > deadline) throw new Error('timed out waiting for condition');
    await pause(25);
  }
}

test('real Python backend answers plan/task commands through the TUI client', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-it-'));
  await mkdir(workspace, {recursive: true});
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    await waitFor(() => (client.capabilities?.() ?? []).length > 0);
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write('/plan list');
    await pause(50);
    stdin.write('\r');
    await pause(800);
    const frame = String(view.lastFrame());
    assert.match(frame, /当前会话没有计划产物。/, frame.slice(-800));
    assert.doesNotMatch(frame, /用 \/plan <任务> 生成/);
    stdin.write('/cost');
    await pause(50);
    stdin.write('\r');
    await pause(500);
    assert.match(String(view.lastFrame()), /session /);
  } finally {
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});

test('fake demo run requests approvals and completes through the real backend', async t => {
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  if (!existsSync(python) || !existsSync(path.join(repoRoot, 'run_vibe.sh'))) {
    t.skip('python backend unavailable');
    return;
  }
  const previous = process.env.VIBE_FAKE_DEMO;
  process.env.VIBE_FAKE_DEMO = '1';
  const workspace = await mkdtemp(path.join(tmpdir(), 'vibe-tui-approval-'));
  const events: ServerEvent[] = [];
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace]});
  client.subscribe(event => events.push(event));
  const view = render(<App client={client}/>);
  try {
    client.start();
    await waitFor(() => events.some(event => event.type === 'ready'));
    const stdin = view.stdin as unknown as {write(value: string): void};
    stdin.write('\r');
    let answered = 0;
    const deadline = Date.now() + 25000;
    while (true) {
      if (events.some(event => event.type === 'done')) break;
      const approvals = events.filter(event => event.type === 'approval_request').length;
      if (approvals > answered) {
        stdin.write('y');
        answered += 1;
        await pause(100);
        continue;
      }
      if (Date.now() > deadline) throw new Error(`run did not finish; approvals=${answered}`);
      await pause(50);
    }
    const done = events.find(event => event.type === 'done') as Extract<ServerEvent, {type: 'done'}>;
    assert.equal(done.result.status, 'done');
    assert.ok(answered >= 2, `expected >=2 approvals, got ${answered}`);
    const demo = await readFile(path.join(workspace, '.vibe', 'fake-demo.txt'), 'utf8');
    assert.match(demo, /fake demo/);
  } finally {
    if (previous === undefined) delete process.env.VIBE_FAKE_DEMO;
    else process.env.VIBE_FAKE_DEMO = previous;
    await client.shutdown();
    cleanup();
    await rm(workspace, {recursive: true, force: true});
  }
});
