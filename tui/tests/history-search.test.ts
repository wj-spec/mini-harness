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

async function fixture(capabilities: string[]) {
  const base = path.join(repoRoot, '.vibe', 'node-transport-tests');
  await mkdir(base, {recursive: true});
  const root = await mkdtemp(path.join(base, 'history-fixture-'));
  const source = `import readline from 'node:readline';
console.log(JSON.stringify({v:1, type:'ready', id:null, workspace:'/workspace', model:'fake', session_id:'session', capabilities:${JSON.stringify(capabilities)}}));
readline.createInterface({input:process.stdin}).on('line', line => {
  const request = JSON.parse(line);
  if (request.type === 'shutdown') {console.log(JSON.stringify({v:1, type:'closed', id:request.id})); process.exit(0);}
  console.log(JSON.stringify({v:1, type:'notice', id:null, message:JSON.stringify(request)}));
});
`;
  await writeFile(path.join(root, 'fixture.mjs'), source);
  const launcher = path.join(root, 'run_vibe.sh');
  await writeFile(launcher, `#!/bin/sh\nexec '${process.execPath.replaceAll("'", "'\\''")}' "$(dirname "$0")/fixture.mjs" "$@"\n`);
  await chmod(launcher, 0o755);
  return {root, async close() {await rm(root, {recursive: true, force: true});}};
}

test('search and rounds transport structured args and reject invalid usage before sending', async () => {
  const f = await fixture(['history-search', 'rounds-ledger']);
  const client = createBackendClient({repoRoot: f.root, args: ['--fake'], shutdownTimeoutMs: 500});
  const state = observer(client);
  try {
    client.start(); await state.wait(event => event.type === 'ready');
    client.command('search', {query: '跨会话目标'});
    const search = await state.wait(event => event.type === 'notice' && event.message.includes('"command":"search"'));
    assert.match(search.type === 'notice' ? search.message : '', /"args":\{"query":"跨会话目标"\}/);
    client.command('rounds', {});
    const rounds = await state.wait(event => event.type === 'notice' && event.message.includes('"command":"rounds"'));
    assert.match(rounds.type === 'notice' ? rounds.message : '', /"command":"rounds"/);
    assert.throws(() => client.command('search', {query: ''}), /检索关键词/);
    assert.throws(() => client.command('search', {query: 'x'.repeat(201)}), /检索关键词/);
    assert.throws(() => (client.command as unknown as (command: string, args?: unknown) => string)('rounds', {extra: 1}), /轮次账本参数无效/);
  } finally {await client.shutdown(); await f.close();}
});

test('search and rounds require their capabilities', async () => {
  const f = await fixture([]);
  const client = createBackendClient({repoRoot: f.root, args: ['--fake'], shutdownTimeoutMs: 500});
  try {
    client.start(); await observer(client).wait(event => event.type === 'ready');
    assert.throws(() => client.command('search', {query: 'x'}), /不支持跨会话检索/);
    assert.throws(() => client.command('rounds', {}), /不支持轮次账本/);
  } finally {await client.shutdown(); await f.close();}
});

test('actual Python serves cross-session search results and the current round ledger', {skip: !existsSync(path.join(repoRoot, '.venv', 'bin', 'python3'))}, async () => {
  const base = path.join(repoRoot, '.vibe', 'node-transport-tests');
  await mkdir(base, {recursive: true});
  const workspace = await mkdtemp(path.join(base, 'history-workspace-'));
  const seeded: {other: string} = JSON.parse(execFileSync(path.join(repoRoot, '.venv', 'bin', 'python3'), ['-c', `
import json, sys
from app.session.store import SessionStore
from core.contracts import Message
workspace = sys.argv[1]
other = SessionStore.new(workspace, 'fake', purpose='test')
other.append_message(Message('user', '旧会话里的跨会话目标与结论'))
print(json.dumps({'other': other.id}))
`, workspace], {cwd: repoRoot, encoding: 'utf8'}));
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace], shutdownTimeoutMs: 1000});
  const state = observer(client);
  try {
    client.start(); await state.wait(event => event.type === 'status' && event.phase === 'idle');
    const id = client.command('search', {query: '跨会话目标'});
    const page = await state.wait(event => event.type === 'history_search' && event.id === id);
    assert.equal(page.type, 'history_search');
    if (page.type !== 'history_search') return;
    assert.equal(page.workspace, workspace);
    const match = page.matches.find(item => item.session_id === seeded.other);
    assert.ok(match); assert.equal(match!.identity.length, 64);
    assert.match(match!.snippet, /跨会话目标/);
    const ledgerId = client.command('rounds', {});
    const ledger = await state.wait(event => event.type === 'rounds_ledger' && event.id === ledgerId);
    assert.equal(ledger.type, 'rounds_ledger');
    if (ledger.type !== 'rounds_ledger') return;
    assert.equal(ledger.total, 0); assert.deepEqual(ledger.rounds, []);
  } finally {await client.shutdown(); await rm(workspace, {recursive: true, force: true});}
});
