import assert from 'node:assert/strict';
import {mkdtempSync, writeFileSync, rmSync, readFileSync, realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {setTimeout as pause} from 'node:timers/promises';
import test, {afterEach} from 'node:test';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import {App} from '../src/app.js';
import type {BackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';
import {sidebarChannelPath, writeChannel} from '../src/sidebar/channel.js';
import {readFileDiff, readWorkspaceStatus} from '../src/sidebar/git.js';
import type {SidebarSignalInput} from '../src/sidebar/signal.js';

afterEach(cleanup);
async function until<T>(read: () => T): Promise<NonNullable<T>> {
  const deadline = Date.now() + 2500;
  while (Date.now() < deadline) {const value = read(); if (value) return value; await pause(10);}
  assert.fail('condition not reached');
}
class Client {
  listeners = new Set<(event: ServerEvent) => void>(); runs: string[] = [];
  subscribe(listener: (event: ServerEvent) => void) {this.listeners.add(listener); return () => this.listeners.delete(listener);}
  run(text: string) {this.runs.push(text); return `run-${this.runs.length}`;}
  capabilities() {return [];}
  async shutdown() {}
  emit(event: Record<string, unknown>) {for (const listener of this.listeners) listener({v: 1, id: null, ...event} as ServerEvent);}
}
function read(file: string): Record<string, unknown> | null {try {return JSON.parse(readFileSync(file, 'utf8'));} catch {return null;}}

test('a scoped diff excerpt appends once to the existing draft, preserves its cursor and never submits automatically', async () => {
  const workspace = realpathSync(mkdtempSync(path.join(tmpdir(), 'draft-quote-')));
  execFileSync('git', ['init', '-q', workspace]); writeFileSync(path.join(workspace, 'note.txt'), '中文证据\n');
  const status = readWorkspaceStatus(workspace); assert.ok(status.ok);
  const diff = readFileDiff(workspace, status.status.files[0]!, false); assert.ok(diff.ok && diff.revision);
  const line = diff.text.split('\n').findIndex(line => line === '+中文证据'); assert.ok(line >= 0);
  const client = new Client(); let signal: SidebarSignalInput | undefined;
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 100, rows: 40}}
    publishSidebarSignal={(_root, value) => {signal = value;}}/>);
  try {
    await pause(20); client.emit({type: 'ready', workspace, model: 'fake', session_id: 's-draft', capabilities: []});
    client.emit({type: 'status', phase: 'idle'}); await until(() => signal?.sessionId === 's-draft');
    view.stdin.write('原有中文草稿'); await pause(20); view.stdin.write('\u001b[D'); await pause(20);
    const owner = signal!.owner!;
    const file = sidebarChannelPath(workspace, 'signal', owner).replace('.signal.json', '.draft-request.json');
    const reply = file.replace('.draft-request.json', '.draft-reply.json');
    const request = {kind: 'append-excerpt', id: randomUUID(), workspace, owner, sessionId: 's-draft', requestId: null,
      createdAt: Date.now(), source: {kind: 'diff', path: 'note.txt', revision: diff.revision},
      start: {line, offset: 0}, end: {line, offset: 5}, text: '+中文证据'};
    writeChannel(file, request);
    await until(() => read(reply)?.outcome === 'appended');
    assert.equal(client.runs.length, 0);
    writeChannel(file, {...request, text: '伪造同 ID 内容'});
    await until(() => read(reply)?.outcome === 'error');
    writeChannel(file, request); await until(() => read(reply)?.outcome === 'appended');
    view.stdin.write('中'); await pause(20); view.stdin.write('\r'); await until(() => client.runs.length);
    assert.match(client.runs[0]!, /^原有中文草中稿\n\n\[侧栏引用/);
    assert.match(client.runs[0]!, /工作区 vs HEAD.*note.txt/);
    assert.equal(client.runs[0]!.split('> +中文证据').length, 2);
    assert.match(client.runs[0]!, new RegExp(diff.revision));
    assert.equal(read(reply)?.id, request.id);
    writeFileSync(path.join(workspace, 'note.txt'), 'changed after selection\n');
    const stale = {...request, id: randomUUID(), createdAt: Date.now()}; writeChannel(file, stale);
    await until(() => read(reply)?.id === stale.id); assert.equal(read(reply)?.outcome, 'error');
    assert.match(String(read(reply)?.message), /版本|选区/);
  } finally {view.unmount(); rmSync(workspace, {recursive: true, force: true});}
});

for (const columns of [32, 20, 12]) test(`sidebar drag selection and copy fallback remain usable at ${columns} columns`, async () => {
  const {SidebarApp} = await import('../src/sidebar/app.js');
  const {writeSidebarSignal, removeSidebarSignal} = await import('../src/sidebar/signal.js');
  const {createSidebarOwner} = await import('../src/sidebar/channel.js');
  const workspace = `/tmp/excerpt-${randomUUID()}`; const owner = createSidebarOwner();
  writeSidebarSignal(workspace, {owner, sessionId: 's-quote', signature: 'a', files: [], active: false});
  const copied: string[] = [];
  const view = render(<SidebarApp copyText={text => {copied.push(text); return 'test';}} workspace={workspace} owner={owner} size={{columns, rows: 14}} refreshMs={0} signalPollMs={10}
    readStatus={() => ({ok: true, status: {root: workspace, branch: 'main', head: 'abc', ahead: 0, behind: 0,
      files: [{path: 'note.txt', status: 'modified', staged: false, index: '.', worktree: 'M', additions: 1, deletions: 0, binary: false}]}})}
    readDiff={() => ({ok: true, text: '\n+中文证据\n+second', revision: 'a'.repeat(64), truncated: false})}/>);
  const requestFile = sidebarChannelPath(workspace, 'signal', owner).replace('.signal.json', '.draft-request.json');
  const replyFile = requestFile.replace('.draft-request.json', '.draft-reply.json');
  try {
    await until(() => view.lastFrame()?.includes('+中文证据')); await pause(30);
    const row = view.lastFrame()!.split('\n').findIndex(line => line.includes('+中文证据')) + 1;
    view.stdin.write(`\u001b[<0;2;${row}M\u001b[<32;11;${row}M\u001b[<0;11;${row}m`);
    await until(() => view.lastFrame()?.includes('[ 附回 ]')); await pause(25);
    assert.match(view.lastFrame()!, /\[ 清除 \]/);
    const rows = view.lastFrame()!.split('\n'); const buttonRow = rows.findIndex(line => line.includes('[ 附回 ]'));
    const {default: width} = await import('string-width');
    const column = width(rows[buttonRow]!.split('[ 附回 ]')[0]!) + 1;
    view.stdin.write(`\u001b[<0;${column};${buttonRow + 1}M\u001b[<0;${column};${buttonRow + 1}m`);
    const request = await until(() => read(requestFile));
    assert.equal(request.text, '+中文证据'); assert.equal(request.requestId, null);
    assert.deepEqual(request.source, {kind: 'diff', path: 'note.txt', revision: 'a'.repeat(64)});
    writeChannel(replyFile, {id: request.id, workspace, owner, sessionId: 's-quote', outcome: 'error', message: '版本已变化 · 选区保留'});
    await until(() => view.lastFrame()?.includes('版本已'));
    assert.match(view.lastFrame()!, /\[ 复制 \]/); await pause(25);
    assert.match(view.lastFrame()!, /中文证据/);
    const copyRows = view.lastFrame()!.split('\n'); const copyRow = copyRows.findIndex(line => line.includes('[ 复制 ]'));
    view.stdin.write(`\u001b[<0;2;${copyRow + 1}M\u001b[<0;2;${copyRow + 1}m`);
    await until(() => copied.length); assert.match(copied[0]!, /> \+中文证据/);
    assert.match(copied[0]!, /note.txt/);
    assert.ok(view.lastFrame()!.split('\n').length <= 14);
    assert.ok(view.lastFrame()!.split('\n').every(line => width(line) <= columns));
  } finally {view.unmount(); removeSidebarSignal(workspace, owner); rmSync(requestFile, {force: true}); rmSync(replyFile, {force: true});}
});

test('verification excerpts keep the old request identity and reject foreign sessions, unknown requests and edited evidence', async () => {
  const {createHash} = await import('node:crypto');
  const client = new Client(); let signal: SidebarSignalInput | undefined;
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns: 100, rows: 40}}
    publishSidebarSignal={(_root, value) => {signal = value;}}/>);
  try {
    await pause(20); client.emit({type: 'ready', workspace: '/tmp/verify-quote', model: 'fake', session_id: 's-v', capabilities: []});
    client.emit({type: 'status', phase: 'idle'}); await until(() => signal?.sessionId === 's-v');
    view.stdin.write('旧检查'); await pause(20); view.stdin.write('\r'); await until(() => client.runs.length);
    client.emit({type: 'done', id: 'run-1', result: {text: '检查失败', status: 'done', steps: 1, usage: {}, cost_usd: 0, run_id: 'trace-v', stop_reason: '', verified: false}});
    await until(() => signal?.run?.status === 'finished');
    const body = ['旧检查', '会话 s-v · 段 未绑定', '任务 未绑定 · 请求 run-1', '追踪 trace-v', '验证未通过',
      '运行状态 done', '完成判定：未知', '此请求尚无命令执行记录；不代表验证通过'].join('\n');
    const workspace = '/tmp/verify-quote'; const owner = signal!.owner!;
    const requestFile = sidebarChannelPath(workspace, 'signal', owner).replace('.signal.json', '.draft-request.json');
    const replyFile = requestFile.replace('.draft-request.json', '.draft-reply.json');
    const base = {kind: 'append-excerpt', id: randomUUID(), workspace, owner, sessionId: 's-v', requestId: 'run-1', createdAt: Date.now(),
      source: {kind: 'verification', revision: createHash('sha256').update(body).digest('hex')}, start: {line: 4, offset: 0}, end: {line: 4, offset: 5}, text: '验证未通过'};
    for (const invalid of [{sessionId: 'another-session'}, {requestId: 'another-run'}, {text: '验证已通过'},
      {source: {...base.source, revision: 'b'.repeat(64)}}]) {
      const request = {...base, ...invalid, id: randomUUID()}; writeChannel(requestFile, request);
      await until(() => read(replyFile)?.id === request.id);
      assert.equal(read(replyFile)?.outcome, 'error');
    }
    view.stdin.write('新任务'); await pause(20); view.stdin.write('\r'); await until(() => client.runs.length === 2);
    view.stdin.write('保留草稿'); await pause(20);
    writeChannel(requestFile, base); await until(() => read(replyFile)?.id === base.id);
    assert.equal(read(replyFile)?.outcome, 'appended');
    assert.equal(client.runs.length, 2);
    assert.match(view.lastFrame()!, /保留草稿/);
    client.emit({type: 'done', id: 'run-2', result: {text: '完成', status: 'done', steps: 1, usage: {}, cost_usd: 0, run_id: 'trace-new', stop_reason: ''}});
    await pause(30); view.stdin.write('\r'); await until(() => client.runs.length === 3);
    assert.match(client.runs[2]!, /验证记录.*请求 run-1/);
    assert.match(client.runs[2]!, /> 验证未通过/);
    assert.doesNotMatch(client.runs[2]!, /验证已通过|another-/);
  } finally {view.unmount();}
});

test('the draft channel rejects unowned, expired, oversized and out-of-workspace messages', async () => {
  const {draftPath, readDraftRequest, removeDraftChannel} = await import('../src/sidebar/draft-channel.js');
  const {createSidebarOwner} = await import('../src/sidebar/channel.js');
  const workspace = '/tmp/draft-bounds'; const owner = createSidebarOwner();
  const request = {kind: 'append-excerpt', id: randomUUID(), workspace, owner, sessionId: 's', requestId: null, createdAt: Date.now(),
    source: {kind: 'diff', path: 'note.txt', revision: 'a'.repeat(64)}, start: {line: 0, offset: 0}, end: {line: 0, offset: 1}, text: '+'};
  try {
    writeChannel(draftPath(workspace, owner), request); assert.deepEqual(readDraftRequest(workspace, owner), request);
    for (const bad of [{owner: createSidebarOwner()}, {kind: 'approve'}, {workspace: '/tmp/another'}, {createdAt: Date.now() - 31_000},
      {createdAt: Date.now() + 6000}, {source: {...request.source, path: '../outside'}}, {source: {...request.source, path: '/outside'}},
      {source: {...request.source, path: 'note\n.txt'}}, {text: '中'.repeat(11_000)}, {end: {line: -1, offset: 0}}, {extra: true}]) {
      writeChannel(draftPath(workspace, owner), {...request, ...bad}); assert.equal(readDraftRequest(workspace, owner), null, JSON.stringify(bad).slice(0, 80));
    }
  } finally {removeDraftChannel(workspace, owner);}
});
