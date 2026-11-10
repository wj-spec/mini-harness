import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, realpathSync, statSync, writeFileSync, readFileSync, renameSync, mkdirSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {test, afterEach} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import {App} from '../src/app.js';
import {SidebarApp} from '../src/sidebar/app.js';
import {createSidebarOwner, writeChannel} from '../src/sidebar/channel.js';
import {writeSidebarSignal, removeSidebarSignal, type SidebarSignalInput} from '../src/sidebar/signal.js';
import {readWorkspaceStatus, readFileDiff} from '../src/sidebar/git.js';
import {draftPath} from '../src/sidebar/draft-channel.js';
import type {BackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';

afterEach(cleanup);
async function until(read: () => unknown) {
  const end = Date.now() + 4000;
  while (Date.now() < end) {if (read()) return; await pause(20);}
  assert.fail('worktree condition not reached');
}
const source = (root: string) => {const stat = statSync(root, {bigint: true}); return {root, identity: `${stat.dev}:${stat.ino}`};};
function fixture() {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), 'inspect-origin-')));
  const git = (...args: string[]) => execFileSync('git', ['-C', root, ...args], {stdio: 'pipe'});
  git('init', '-q'); writeFileSync(path.join(root, 'same.txt'), 'BASE\n'); git('add', 'same.txt');
  git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'baseline');
  const task = path.join(root, '任务 目录'); git('worktree', 'add', '-qb', 'task', task);
  writeFileSync(path.join(root, 'same.txt'), 'PROJECT_ONLY\n'); writeFileSync(path.join(task, 'same.txt'), 'TASK_ONLY\n');
  return {root, task};
}

test('bound sidebar reads its worktree, switches same-name files by origin and refuses replaced directories', async () => {
  const {root, task} = fixture(); const owner = createSidebarOwner();
  const publish = (fileSource: ReturnType<typeof source> | null) => writeSidebarSignal(root,
    {owner, sessionId: 'origin', signature: randomUUID(), files: ['same.txt'], active: false, fileSource});
  publish(source(task));
  const view = render(<SidebarApp workspace={root} owner={owner} size={{columns: 80, rows: 22}} refreshMs={50} signalPollMs={10}/>);
  try {
    await until(() => view.lastFrame()?.includes('+TASK_ONLY'));
    assert.doesNotMatch(view.lastFrame()!, /PROJECT_ONLY/);
    assert.match(view.lastFrame()!, /任务目录/);
    publish(source(root)); await until(() => view.lastFrame()?.includes('+PROJECT_ONLY'));
    assert.doesNotMatch(view.lastFrame()!, /TASK_ONLY/);
    publish(source(task)); await until(() => view.lastFrame()?.includes('+TASK_ONLY'));
    renameSync(task, task + '-old'); mkdirSync(task); writeFileSync(path.join(task, 'same.txt'), 'REPLACED\n');
    await until(() => view.lastFrame()?.includes('来源目录'));
    assert.doesNotMatch(view.lastFrame()!, /PROJECT_ONLY|REPLACED/);
    publish(null); await until(() => view.lastFrame()?.includes('来源未知'));
  } finally {view.unmount(); removeSidebarSignal(root, owner); rmSync(root, {recursive: true, force: true});}
});

test('worktree diff excerpts are rechecked at the saved origin and cannot claim the project directory', async () => {
  const {root, task} = fixture(); const fileSource = source(task);
  const status = readWorkspaceStatus(task); assert.ok(status.ok);
  const diff = readFileDiff(task, status.status.files.find(file => file.path === 'same.txt')!); assert.ok(diff.ok && diff.revision);
  const line = diff.text.split('\n').indexOf('+TASK_ONLY'); assert.ok(line >= 0);
  const listeners = new Set<(event: ServerEvent) => void>(); let signal: SidebarSignalInput | undefined;
  const client = {subscribe: (fn: (event: ServerEvent) => void) => {listeners.add(fn); return () => listeners.delete(fn);},
    capabilities: () => [], shutdown: async () => {}, run: () => {throw new Error('unexpected run');}} as unknown as BackendClient;
  const view = render(<App client={client} windowSize={{columns: 100, rows: 40}} publishSidebarSignal={(_root, value) => {signal = value;}}/>);
  try {
    await pause(30);
    for (const fn of listeners) {fn({v: 1, type: 'ready', id: null, workspace: root, model: 'fake', session_id: 'origin', capabilities: [], file_source: fileSource});
      fn({v: 1, type: 'status', id: null, phase: 'idle'});}
    await until(() => signal?.sessionId === 'origin'); assert.deepEqual(signal?.fileSource, fileSource);
    view.stdin.write('中文草稿'); await pause(30); view.stdin.write('\x1b[D'); await pause(30);
    const owner = signal!.owner!;
    const request = {kind: 'append-excerpt', id: randomUUID(), workspace: root, owner, sessionId: 'origin', requestId: null,
      createdAt: Date.now(), source: {kind: 'diff', path: 'same.txt', revision: diff.revision, fileSource},
      start: {line, offset: 0}, end: {line, offset: 10}, text: '+TASK_ONLY'};
    const reply = () => {try {return JSON.parse(readFileSync(draftPath(root, owner, true), 'utf8'));} catch {return null;}};
    writeChannel(draftPath(root, owner), request); await until(() => reply()?.id === request.id);
    assert.equal(reply().outcome, 'appended', reply().message);
    view.stdin.write('中'); await pause(40); assert.match(view.lastFrame()!, /字符中/); assert.match(view.lastFrame()!, /中文草稿/);
    const forged = {...request, id: randomUUID(), source: {...request.source, fileSource: source(root)}};
    writeChannel(draftPath(root, owner), forged); await until(() => reply()?.id === forged.id);
    assert.equal(reply().outcome, 'error');
    assert.match(view.lastFrame()!, /\[附回\] 工作区 vs HEAD · same\.txt · 第 \d+ 行 · 10 字符/);
    assert.doesNotMatch(view.lastFrame()!, /TASK_ONLY/);
  } finally {view.unmount(); rmSync(root, {recursive: true, force: true});}
});
