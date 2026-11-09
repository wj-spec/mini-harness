import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {chmod, mkdir, mkdtemp, rm, writeFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import {createBackendClient, type BackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const identity = 'b'.repeat(64);
const target = {workspace: '/new', sessionId: '20261109-010203-123456', identity};

function observer(client: BackendClient) {
  const events: ServerEvent[] = [];
  const pending = new Set<{predicate: (event: ServerEvent) => boolean; resolve: (event: ServerEvent) => void; timer: ReturnType<typeof setTimeout>}>();
  client.subscribe(event => {
    events.push(event);
    for (const waiter of pending) if (waiter.predicate(event)) {clearTimeout(waiter.timer); pending.delete(waiter); waiter.resolve(event);}
  });
  return {events, wait(predicate: (event: ServerEvent) => boolean, timeout = 10000): Promise<ServerEvent> {
    const previous = events.find(predicate); if (previous) return Promise.resolve(previous);
    return new Promise((resolve, reject) => {
      const waiter = {predicate, resolve, timer: setTimeout(() => {pending.delete(waiter); reject(new Error(`event timeout: ${JSON.stringify(events)}`));}, timeout)};
      pending.add(waiter);
    });
  }};
}

async function fixture(extra = '') {
  const base = path.join(repoRoot, '.vibe', 'node-transport-tests');
  await mkdir(base, {recursive: true});
  const root = await mkdtemp(path.join(base, 'session-fixture-'));
  const source = `import readline from 'node:readline';
const argv = process.argv.slice(2);
const flag = (name) => {const index = argv.indexOf(name); return index >= 0 ? argv[index + 1] : null;};
const resumed = argv.includes('--session');
const ready = {v:1, type:'ready', id:null, workspace: flag('--workspace') ?? '/tmp', model: argv.join(' '),
  session_id: resumed ? flag('--session') : 'initial', resumed, capabilities:['session-navigation']};
const issued = flag('--session-identity');
if (issued) ready.session_identity = issued;
${extra}
console.log(JSON.stringify(ready));
readline.createInterface({input:process.stdin}).on('line', line => {
  const request = JSON.parse(line);
  if (request.type === 'shutdown') {console.log(JSON.stringify({v:1, type:'closed', id:request.id})); process.exit(0);}
});
`;
  await writeFile(path.join(root, 'fixture.mjs'), source);
  const launcher = path.join(root, 'run_vibe.sh');
  await writeFile(launcher, `#!/bin/sh\nexec '${process.execPath.replaceAll("'", "'\\''")}' "$(dirname "$0")/fixture.mjs" "$@"\n`);
  await chmod(launcher, 0o755);
  return {root, async close() {await rm(root, {recursive: true, force: true});}};
}

test('openSession replaces workspace and session args while keeping purpose flags, then verifies the receipt', async () => {
  const f = await fixture();
  const client = createBackendClient({repoRoot: f.root, args: ['--fake', '--continue', '--workspace', '/old'], shutdownTimeoutMs: 500});
  const state = observer(client);
  try {
    client.start(); await state.wait(event => event.type === 'ready');
    const opened = await client.openSession!(target);
    const child = observer(opened);
    const ready = await child.wait(event => event.type === 'ready');
    assert.equal(ready.type === 'ready' && ready.session_id, target.sessionId);
    assert.equal(ready.type === 'ready' && ready.session_identity, identity);
    assert.equal(ready.type === 'ready' && ready.resumed, true);
    const argv = ready.type === 'ready' ? ready.model : '';
    assert.match(argv, /--fake/);
    assert.match(argv, /--workspace \/new/);
    assert.match(argv, /--session 20261109-010203-123456/);
    assert.match(argv, new RegExp(`--session-identity ${identity}`));
    assert.doesNotMatch(argv, /--continue|\/old/);
    await opened.shutdown();
  } finally {await client.shutdown(); await f.close();}
});

test('openSession rejects a ready receipt for another session and never hands over a mismatched backend', async () => {
  const f = await fixture("ready.session_id = '20260101-000000-000000';");
  const client = createBackendClient({repoRoot: f.root, args: ['--fake'], shutdownTimeoutMs: 500});
  try {
    client.start(); await observer(client).wait(event => event.type === 'ready');
    await assert.rejects(client.openSession!(target), /回执|不匹配/);
  } finally {await client.shutdown(); await f.close();}
});

test('openSession requires the session-navigation capability before spawning a candidate', async () => {
  const f = await fixture('ready.capabilities = [];');
  const client = createBackendClient({repoRoot: f.root, args: ['--fake'], shutdownTimeoutMs: 500});
  let spawned = 0;
  try {
    const original = client.openSession;
    assert.ok(original);
    client.start(); await observer(client).wait(event => event.type === 'ready');
    await assert.rejects(client.openSession!(target), /不支持旧会话目录/);
    spawned += 1;
  } finally {assert.equal(spawned, 1); await client.shutdown(); await f.close();}
});

test('actual Python browses and opens a previewed session without running it', {skip: !existsSync(path.join(repoRoot, '.venv', 'bin', 'python3'))}, async () => {
  const base = path.join(repoRoot, '.vibe', 'node-transport-tests');
  await mkdir(base, {recursive: true});
  const workspace = await mkdtemp(path.join(base, 'session-open-'));
  const python = path.join(repoRoot, '.venv', 'bin', 'python3');
  const seeded: {a: string; b: string} = JSON.parse(execFileSync(python, ['-c', `
import json, sys
from app.session.store import SessionStore
from core.contracts import Message
workspace = sys.argv[1]
a = SessionStore.new(workspace, 'fake', purpose='test')
a.append_message(Message('user', '会话 A 目标'))
b = SessionStore.new(workspace, 'fake', purpose='test')
b.append_message(Message('user', '会话 B 目标'))
b.append_message(Message('assistant', '会话 B 已保存的结果'))
print(json.dumps({'a': a.id, 'b': b.id}))
`, workspace], {cwd: repoRoot, encoding: 'utf8'}));
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace], shutdownTimeoutMs: 1000});
  const state = observer(client);
  let opened: BackendClient | undefined;
  try {
    client.start(); await state.wait(event => event.type === 'status' && event.phase === 'idle');
    const commandId = client.command('sessions', {});
    const page = await state.wait(event => event.type === 'session_catalog' && event.id === commandId);
    assert.ok(page.type === 'session_catalog');
    if (page.type !== 'session_catalog') return;
    const row = page.records.find(record => record.session_id === seeded.b);
    assert.ok(row);
    opened = await client.openSession!({workspace, sessionId: seeded.b, identity: row.identity});
    const child = observer(opened);
    const ready = await child.wait(event => event.type === 'ready');
    assert.equal(ready.type === 'ready' && ready.session_id, seeded.b);
    assert.equal(ready.type === 'ready' && ready.session_identity, row.identity);
    assert.equal(ready.type === 'ready' && ready.resumed, true);
    await child.wait(event => event.type === 'history_message');
    assert.ok(child.events.some(event => event.type === 'history_message' && event.message.content === '会话 B 已保存的结果'));
    assert.equal(child.events.some(event => ['done', 'text_delta', 'tool_call', 'tool_result'].includes(event.type)), false);
    await opened.shutdown();
    await assert.rejects(client.openSession!({workspace, sessionId: seeded.b, identity: '0'.repeat(64)}), /所选会话|身份/);
    assert.equal(state.events.some(event => event.type === 'closed'), false);
  } finally {await client.shutdown(); await opened?.shutdown(); await rm(workspace, {recursive: true, force: true});}
});
