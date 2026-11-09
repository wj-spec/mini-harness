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

async function fixture(capabilities: string[] = ['permission-panel']) {
  const base = path.join(repoRoot, '.vibe', 'node-transport-tests');
  await mkdir(base, {recursive: true});
  const root = await mkdtemp(path.join(base, 'permission-fixture-'));
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

test('the permissions command transports structured args and rejects invalid grants before sending', async () => {
  const f = await fixture();
  const client = createBackendClient({repoRoot: f.root, args: ['--fake'], shutdownTimeoutMs: 500});
  const state = observer(client);
  try {
    client.start(); await state.wait(event => event.type === 'ready');
    client.command('permissions', {action: 'list'});
    const notice = await state.wait(event => event.type === 'notice');
    assert.equal(notice.type, 'notice');
    if (notice.type !== 'notice') return;
    assert.match(notice.message, /"command":"permissions"/);
    assert.match(notice.message, /"action":"list"/);
    assert.throws(() => client.command('permissions', {action: 'revoke', grant_id: 'not-a-grant'}), /权限参数/);
    assert.throws(() => client.command('permissions', {action: 'nope'} as never), /权限参数/);
  } finally {await client.shutdown(); await f.close();}
});

test('the permissions command refuses a backend without the permission-panel capability', async () => {
  const f = await fixture([]);
  const client = createBackendClient({repoRoot: f.root, args: ['--fake'], shutdownTimeoutMs: 500});
  try {
    client.start(); await observer(client).wait(event => event.type === 'ready');
    assert.throws(() => client.command('permissions', {action: 'list'}), /不支持权限面板/);
  } finally {await client.shutdown(); await f.close();}
});

test('actual Python serves the permission overview and a structured revoke error', {skip: !existsSync(path.join(repoRoot, '.venv', 'bin', 'python3'))}, async () => {
  const base = path.join(repoRoot, '.vibe', 'node-transport-tests');
  await mkdir(base, {recursive: true});
  const workspace = await mkdtemp(path.join(base, 'permission-workspace-'));
  const client = createBackendClient({repoRoot, args: ['--fake', '--workspace', workspace], shutdownTimeoutMs: 1000});
  const state = observer(client);
  try {
    client.start(); await state.wait(event => event.type === 'status' && event.phase === 'idle');
    const id = client.command('permissions', {action: 'list'});
    const overview = await state.wait(event => event.type === 'permission_overview' && event.id === id);
    assert.equal(overview.type, 'permission_overview');
    if (overview.type !== 'permission_overview') return;
    assert.equal(overview.workspace, workspace);
    assert.match(overview.session_id, /^[-A-Za-z0-9_]{1,128}$/);
    assert.equal(overview.autonomy.level, 'ask');
    assert.equal(overview.revoked, null);
    assert.ok(Array.isArray(overview.grants) && Array.isArray(overview.persistent));
    const missing = client.command('permissions', {action: 'revoke', grant_id: 'ss-' + '0'.repeat(12)});
    const error = await state.wait(event => event.type === 'error' && event.id === missing);
    assert.equal(error.type === 'error' ? error.code : '', 'permission_grant_not_found');
    const legacy = client.command('permissions');
    const text = await state.wait(event => event.type === 'command_result' && event.id === legacy);
    assert.match(text.type === 'command_result' ? text.text : '', /workspace 授权/);
  } finally {await client.shutdown(); await rm(workspace, {recursive: true, force: true});}
});
