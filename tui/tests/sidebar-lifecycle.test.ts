import assert from 'node:assert/strict';
import test from 'node:test';
import {createHash, randomUUID} from 'node:crypto';
import {spawn} from 'node:child_process';
import {existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {isProcessAlive, killLinkedSidebar, readSidebarLink, reapStaleSidebars, removeSidebarLink, watchParentPid, writeSidebarLink} from '../src/sidebar/lifecycle.js';

test('closing one workbench leaves the other linked process alive in the same workspace', async () => {
  const workspace = `/tmp/sidebar-owner-processes-${randomUUID()}`;
  const a = {instanceId: randomUUID(), parentPid: process.pid};
  const b = {instanceId: randomUUID(), parentPid: process.pid};
  const children = [a, b].map(() => spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {stdio: 'ignore'}));
  try {
    const first = {pid: children[0]!.pid!, parentPid: process.pid, owner: a};
    const second = {pid: children[1]!.pid!, parentPid: process.pid, owner: b};
    writeSidebarLink(workspace, first); writeSidebarLink(workspace, second);
    const exited = new Promise<boolean>(resolve => children[0]!.once('exit', () => resolve(true)));
    assert.equal(killLinkedSidebar(workspace, {owner: a}), true);
    assert.equal(isProcessAlive(children[1]!.pid!), true);
    assert.equal(await Promise.race([exited, new Promise(resolve => setTimeout(() => resolve(false), 500))]), true);
    removeSidebarLink(workspace, children[0]!.pid, a);
    assert.equal(readSidebarLink(workspace, b)?.pid, children[1]!.pid);
  } finally {
    for (const child of children) child.kill('SIGKILL');
    removeSidebarLink(workspace, undefined, a);
    removeSidebarLink(workspace, undefined, b);
    removeSidebarLink(workspace);
  }
});

test('closing the workbench terminates its sidebar even while an excerpt is selected', () => {
  const workspace = `/tmp/sidebar-selected-${randomUUID()}`;
  const owner = {instanceId: randomUUID(), parentPid: process.pid};
  const killed: number[] = [];
  try {
    writeSidebarLink(workspace, {pid: process.pid, parentPid: process.pid, owner, hasSelection: true});
    assert.equal(killLinkedSidebar(workspace, {owner, kill: pid => {killed.push(pid); return true;}}), true);
    assert.deepEqual(killed, [process.pid]);
  } finally {removeSidebarLink(workspace, undefined, owner);}
});

test('stale sidebar links from dead parents are reaped while live workbench links stay', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'sidebar-reap-'));
  const workspace = `/tmp/sidebar-reap-${randomUUID()}`;
  const base = path.join(root, 'mini-harness-sidebar');
  mkdirSync(base, {recursive: true});
  const prefix = createHash('sha1').update(path.resolve(workspace)).digest('hex').slice(0, 16);
  const writeLink = (name: string, value: Record<string, unknown>) =>
    writeFileSync(path.join(base, `${prefix}-${name}.link.json`), JSON.stringify(value));
  try {
    writeLink('dead-parent', {pid: 111, parentPid: 222});
    writeLink('live-parent', {pid: 333, parentPid: 444});
    writeLink('dead-child', {pid: 555, parentPid: 666});
    const alive = (pid: number) => [111, 333, 444].includes(pid);
    const killed: number[] = [];
    const reaped = reapStaleSidebars(workspace, {root, alive, kill: (pid: number) => {killed.push(pid); return true;}});
    assert.deepEqual(killed, [111]);
    assert.deepEqual(reaped, [111]);
    assert.ok(existsSync(path.join(base, `${prefix}-live-parent.link.json`)), 'live workbench sidebar must be kept');
    assert.ok(!existsSync(path.join(base, `${prefix}-dead-parent.link.json`)));
    assert.ok(!existsSync(path.join(base, `${prefix}-dead-child.link.json`)));
    assert.equal(reapStaleSidebars(workspace, {root, alive: () => true, kill: () => true}).length, 0);
  } finally {rmSync(root, {recursive: true, force: true});}
});

test('process liveness treats ESRCH as gone and EPERM as alive', () => {
  assert.equal(isProcessAlive(process.pid), true);
  assert.equal(isProcessAlive(0), false);
  assert.equal(isProcessAlive(-1), false);
  assert.equal(isProcessAlive(1234, () => {throw Object.assign(new Error('gone'), {code: 'ESRCH'});}), false);
  assert.equal(isProcessAlive(1234, () => {throw Object.assign(new Error('denied'), {code: 'EPERM'});}), true);
  assert.equal(isProcessAlive(1234, () => true), true);
});

test('parent watcher fires once when the TUI process disappears and can be stopped', async () => {
  let alive = true;
  let gone = 0;
  const stop = watchParentPid(4242, () => {gone += 1;}, {intervalMs: 5, alive: () => alive});
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.equal(gone, 0);
  alive = false;
  await new Promise(resolve => setTimeout(resolve, 25));
  assert.equal(gone, 1);
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(gone, 1, 'must only fire once');
  stop();

  let stopped = 0;
  const stopImmediately = watchParentPid(4242, () => {stopped += 1;}, {intervalMs: 5, alive: () => false});
  stopImmediately();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(stopped, 0);
});

test('sidebar link file lets the TUI terminate only its own sidebar', () => {
  const workspace = `/tmp/mini-harness-test-${randomUUID()}`;
  try {
    const link = {pid: 1111, parentPid: 2222};
    writeSidebarLink(workspace, link);
    assert.deepEqual(readSidebarLink(workspace), link);

    const killed: number[] = [];
    const kill = (pid: number) => {killed.push(pid); return true;};
    assert.equal(killLinkedSidebar(workspace, {parentPid: 3333, alive: () => true, kill}), false);
    assert.deepEqual(killed, [], 'a different parent must not kill the sidebar');
    assert.equal(killLinkedSidebar(workspace, {parentPid: 2222, alive: () => true, kill}), true);
    assert.deepEqual(killed, [1111]);

    assert.equal(killLinkedSidebar(workspace, {parentPid: 2222, alive: () => false, kill}), false);
    assert.equal(readSidebarLink(workspace), null, 'a dead sidebar link is cleaned up');
  } finally {
    removeSidebarLink(workspace);
  }
});

test('killLinkedSidebar terminates a real linked process with SIGTERM', async () => {
  const workspace = `/tmp/mini-harness-test-${randomUUID()}`;
  const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {stdio: 'ignore'});
  try {
    writeSidebarLink(workspace, {pid: child.pid!, parentPid: process.pid});
    assert.equal(killLinkedSidebar(workspace), true);
    const signal = await new Promise<NodeJS.Signals | null>(resolve => child.once('exit', (_code, value) => resolve(value)));
    assert.equal(signal, 'SIGTERM');
  } finally {
    child.kill('SIGKILL');
    removeSidebarLink(workspace);
  }
});
