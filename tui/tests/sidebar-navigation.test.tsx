import assert from 'node:assert/strict';
import test, {afterEach} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import {randomUUID} from 'node:crypto';
import React from 'react';
import stringWidth from 'string-width';
import {cleanup, render} from 'ink-testing-library';
import {SidebarApp} from '../src/sidebar/app.js';
import {createSidebarOwner} from '../src/sidebar/channel.js';
import {writeSidebarSignal, readSidebarSignal, removeSidebarSignal, type SidebarSignalInput} from '../src/sidebar/signal.js';
import {readPlanPageRequest, writePlanPageReply, removeSidebarRequests} from '../src/sidebar/requests.js';

afterEach(cleanup);
async function until<T>(read: () => T, timeout = 2000): Promise<NonNullable<T>> {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {const result = read(); if (result) return result; await pause(10);}
  assert.fail('condition not reached');
}
async function click(view: ReturnType<typeof render>, label: string, settleMs = 20) {
  await pause(20);
  const lines = view.lastFrame()!.split('\n'); const row = lines.findIndex(line => line.includes(label));
  assert.ok(row >= 0, view.lastFrame());
  const column = stringWidth(lines[row]!.slice(0, lines[row]!.indexOf(label))) + 1;
  view.stdin.write(`\u001b[<0;${column};${row + 1}M\u001b[<0;${column};${row + 1}m`);
  await pause(settleMs);
}
const target = {session_id: 's-nav', plan_task_id: 't-20261109-010000-abcdef', plan_id: 'pl-nav', plan_version: 1, plan_hash: 'a'.repeat(64)};
const readStatus = () => ({ok: true as const, status: {root: '/repo', branch: 'main', head: 'abc', ahead: 0, behind: 0,
  files: [{path: 'a.ts', status: 'modified' as const, staged: false, index: '.', worktree: 'M', additions: 1, deletions: 0, binary: false}]}});
const readDiff = () => ({ok: true as const, text: Array.from({length: 80}, (_, i) => `+DIFF${i} 中文代码`).join('\n'), truncated: false});
function signal(owner: ReturnType<typeof createSidebarOwner>): SidebarSignalInput {
  return {owner, sessionId: target.session_id, signature: 'first', files: [], active: false,
    plan: {target, relation: 'execution', problem: null, version: 1, status: 'approved', stale: false, objective: '完整计划', stepCount: 1, doneSteps: null},
    run: {requestId: 'r1', traceRunId: null, taskId: 't-exec', segment: 1, taskStatus: 'paused', status: 'finished', objective: '原任务'},
    verification: {requestId: 'r1', status: 'stopped', stopReason: '', verified: false, completionAllowed: false, completionMessages: [], omittedCommands: 0,
      commands: [{id: 'cmd', command: 'npm test', verification: true, pending: false, ok: true, execution: {status: 'failed', exit_code: 2},
        output: Array.from({length: 40}, (_, i) => `OUTPUT${i} 中文证据`).join('\n'), truncated: false}]}};
}

test('clicking inspection tabs keeps each page and reading position without repeating plan reads', async () => {
  const workspace = `/tmp/sidebar-nav-${randomUUID()}`; const owner = createSidebarOwner();
  writeSidebarSignal(workspace, signal(owner));
  assert.ok(readSidebarSignal(workspace, owner));
  const view = render(<SidebarApp workspace={workspace} owner={owner} refreshMs={0} signalPollMs={10}
    size={{columns: 40, rows: 18}} readStatus={readStatus} readDiff={readDiff}/>);
  try {
    await until(() => view.lastFrame()?.includes('+DIFF0'));
    assert.match(view.lastFrame()!.split('\n')[0]!, /›变更.*计划.*验证/);
    view.stdin.write('\t'); view.stdin.write('\u001b[6~'); await pause(30);
    const diffAnchor = view.lastFrame()!.match(/DIFF\d+/)![0]; assert.notEqual(diffAnchor, 'DIFF0');
    await click(view, '计划');
    const request = await until(() => readPlanPageRequest(workspace, owner)).catch(() => assert.fail(view.lastFrame()));
    const body = Array.from({length: 70}, (_, i) => `PLAN${i} 中文计划`).join('\n');
    writePlanPageReply({...request, outcome: 'page', body, nextCursor: null, message: ''});
    await until(() => view.lastFrame()?.includes('PLAN0'));
    assert.match(view.lastFrame()!.split('\n')[0]!, /变更.*›计划.*验证/);
    view.stdin.write('\u001b[6~'); await pause(30);
    const planAnchor = view.lastFrame()!.match(/PLAN\d+/)![0]; assert.notEqual(planAnchor, 'PLAN0');
    await click(view, '验证', 0); await until(() => view.lastFrame()?.includes('验证记录'));
    view.stdin.write('G'); await until(() => view.lastFrame()?.includes('OUTPUT39')).catch(() => assert.fail(view.lastFrame()));
    const outputAnchor = view.lastFrame()!.match(/OUTPUT\d+/)![0];
    await click(view, '计划', 0); await until(() => view.lastFrame()?.includes(planAnchor));
    assert.equal(readPlanPageRequest(workspace, owner), null);
    await click(view, '变更'); await until(() => view.lastFrame()?.includes(diffAnchor));
    await click(view, '验证'); await until(() => view.lastFrame()?.includes(outputAnchor));
    view.stdin.write('\u001b'); await until(() => view.lastFrame()?.includes(diffAnchor));
  } finally {view.unmount(); removeSidebarSignal(workspace, owner); removeSidebarRequests(workspace, owner);}
});

test('the first verification key after a tab click is handled without waiting for passive effects', async () => {
  const workspace = `/tmp/sidebar-first-key-${randomUUID()}`; const owner = createSidebarOwner();
  writeSidebarSignal(workspace, signal(owner));
  const view = render(<SidebarApp workspace={workspace} owner={owner} refreshMs={0} signalPollMs={10}
    size={{columns: 40, rows: 18}} readStatus={readStatus} readDiff={readDiff}/>);
  try {
    await until(() => view.lastFrame()?.includes('+DIFF0'));
    await click(view, '验证', 0); await until(() => view.lastFrame()?.includes('验证记录'));
    view.stdin.write('G');
    await until(() => view.lastFrame()?.includes('OUTPUT39')).catch(() => assert.fail(view.lastFrame()));
  } finally {view.unmount(); removeSidebarSignal(workspace, owner); removeSidebarRequests(workspace, owner);}
});

test('hidden verification stays pinned until the visible adopt action accepts the new request', async () => {
  const workspace = `/tmp/sidebar-nav-update-${randomUUID()}`; const owner = createSidebarOwner();
  const first = signal(owner); writeSidebarSignal(workspace, first);
  const view = render(<SidebarApp workspace={workspace} owner={owner} refreshMs={0} signalPollMs={10}
    size={{columns: 32, rows: 12}} readStatus={readStatus} readDiff={readDiff}/>);
  try {
    await until(() => view.lastFrame()?.includes('已结束'));
    await click(view, '验证'); await until(() => view.lastFrame()?.includes('原任务'));
    await click(view, '变更');
    writeSidebarSignal(workspace, {...first, signature: 'next', plan: undefined,
      run: {...first.run!, requestId: 'r2', objective: '新任务'},
      verification: {...first.verification!, requestId: 'r2', verified: null, commands: []}});
    await until(() => view.lastFrame()?.includes('新任务'));
    await click(view, '验证'); await until(() => view.lastFrame()?.includes('有更新'));
    assert.match(view.lastFrame()!, /原任务/); assert.doesNotMatch(view.lastFrame()!, /新任务/);
    assert.match(view.lastFrame()!.split('\n')[0]!, /变更.*计划.*验证.*接入/);
    await click(view, '接入'); await until(() => view.lastFrame()?.includes('新任务'));
    assert.doesNotMatch(view.lastFrame()!, /原任务|有更新/);
    assert.ok(view.lastFrame()!.split('\n').length <= 12);
    assert.ok(view.lastFrame()!.split('\n').every(line => stringWidth(line) <= 32));
  } finally {view.unmount(); removeSidebarSignal(workspace, owner); removeSidebarRequests(workspace, owner);}
});

test('an empty plan page explains missing data and opens the first plan when it arrives', async () => {
  const workspace = `/tmp/sidebar-nav-empty-${randomUUID()}`; const owner = createSidebarOwner();
  writeSidebarSignal(workspace, {owner, sessionId: target.session_id, signature: 'empty', files: [], active: false});
  const props = {workspace, owner, refreshMs: 0, signalPollMs: 10, readStatus, readDiff};
  const view = render(<SidebarApp {...props} size={{columns: 32, rows: 12}}/>);
  try {
    await until(() => view.lastFrame()?.includes('+DIFF0'));
    await click(view, '验证'); assert.match(view.lastFrame()!, /当前没有运行验证记录/);
    await click(view, '计划'); assert.match(view.lastFrame()!, /当前没有可读取的计划/);
    writeSidebarSignal(workspace, signal(owner));
    const request = await until(() => readPlanPageRequest(workspace, owner)).catch(() => assert.fail(view.lastFrame()));
    writePlanPageReply({...request, outcome: 'page', body: '新到达的完整计划', nextCursor: null, message: ''});
    await until(() => view.lastFrame()?.includes('新到达的完整计划'));
    await click(view, '变更');
    view.rerender(<SidebarApp {...props} size={{columns: 12, rows: 8}}/>); await pause(30);
    assert.match(view.lastFrame()!.split('\n')[0]!, /改.*计.*验/);
    assert.ok(view.lastFrame()!.split('\n').every(line => stringWidth(line) <= 12));
  } finally {view.unmount(); removeSidebarSignal(workspace, owner); removeSidebarRequests(workspace, owner);}
});

test('new activity on another file offers follow rather than an adopt action with no new diff', async () => {
  const workspace = `/tmp/sidebar-nav-follow-${randomUUID()}`; const owner = createSidebarOwner();
  const input = {owner, sessionId: target.session_id, signature: 'a', files: ['a.ts'], active: true};
  writeSidebarSignal(workspace, input);
  const status = readStatus(); status.status.files.push({...status.status.files[0]!, path: 'b.ts'});
  const view = render(<SidebarApp workspace={workspace} owner={owner} refreshMs={0} signalPollMs={10}
    size={{columns: 40, rows: 18}} readStatus={() => status} readDiff={(_root, file) => ({ok: true, text: `+${file.path}`, truncated: false})}/>);
  try {
    await until(() => view.lastFrame()?.includes('+a.ts'));
    await click(view, '计划'); await click(view, '变更');
    writeSidebarSignal(workspace, {...input, signature: 'b', files: ['b.ts', 'a.ts']});
    await until(() => view.lastFrame()?.includes('有更新'));
    assert.match(view.lastFrame()!, /\+a.ts/);
    assert.match(view.lastFrame()!.split('\n')[0]!, /验证.*跟随/);
    await click(view, '跟随'); await until(() => view.lastFrame()?.includes('+b.ts'));
    assert.doesNotMatch(view.lastFrame()!, /有更新/);
  } finally {view.unmount(); removeSidebarSignal(workspace, owner); removeSidebarRequests(workspace, owner);}
});

test('explicit result navigation replaces the whole scope while ordinary updates do not switch tabs', async () => {
  const workspace = `/tmp/sidebar-result-${randomUUID()}`; const owner = createSidebarOwner();
  const old = signal(owner); const latest: SidebarSignalInput = {...old, signature: 'latest', plan: undefined,
    run: {...old.run!, requestId: 'r2', objective: '当前任务'}, verification: {...old.verification!, requestId: 'r2', verified: null, commands: []}};
  writeSidebarSignal(workspace, latest);
  const view = render(<SidebarApp workspace={workspace} owner={owner} refreshMs={0} signalPollMs={10}
    size={{columns: 44, rows: 18}} readStatus={readStatus} readDiff={readDiff}/>);
  try {
    await until(() => view.lastFrame()?.includes('当前任务'));
    await click(view, '验证'); await until(() => view.lastFrame()?.includes('当前任务'));
    const navigation = {id: randomUUID(), page: 'plan' as const, mode: 'result' as const};
    writeSidebarSignal(workspace, {...old, signature: 'choose-old', navigation});
    const request = await until(() => readPlanPageRequest(workspace, owner)).catch(() => assert.fail(view.lastFrame()));
    assert.deepEqual(request.target, target);
    writePlanPageReply({...request, outcome: 'page', body: '旧结果的准确计划', nextCursor: null, message: ''});
    await until(() => view.lastFrame()?.includes('旧结果的准确计划'));
    assert.match(view.lastFrame()!, /结果快照/);
    await click(view, '验证'); await until(() => view.lastFrame()?.includes('原任务'));
    assert.doesNotMatch(view.lastFrame()!, /当前任务/);
    writeSidebarSignal(workspace, {...old, signature: 'ordinary-refresh', navigation}); await pause(50);
    assert.match(view.lastFrame()!, /验证记录/);
    writeSidebarSignal(workspace, {...latest, signature: 'back-current', navigation: {id: randomUUID(), page: 'changes', mode: 'current'}});
    await until(() => view.lastFrame()?.includes('+DIFF0'));
    await click(view, '验证'); await until(() => view.lastFrame()?.includes('当前任务'));
    assert.doesNotMatch(view.lastFrame()!, /原任务|结果快照/);
  } finally {view.unmount(); removeSidebarSignal(workspace, owner); removeSidebarRequests(workspace, owner);}
});

test('read marks bind to the displayed diff version and a changed version remains unread until explicitly marked', async () => {
  let version = 'a';
  const workspace = `/tmp/sidebar-read-${randomUUID()}`;
  const view = render(<SidebarApp workspace={workspace} refreshMs={10} signalPollMs={0} size={{columns: 32, rows: 16}}
    readStatus={readStatus} readDiff={() => ({ok: true, text: version === 'a' ? '+原始内容' : '+新的内容', revision: version.repeat(64), truncated: false})}/>);
  try {
    await until(() => view.lastFrame()?.includes('+原始内容'));
    view.stdin.write('f'); await pause(25);
    await click(view, '标已读'); await until(() => view.lastFrame()?.includes('[ 撤销 ]'));
    assert.match(view.lastFrame()!, /已读/); assert.doesNotMatch(view.lastFrame()!, /验证已通过/);
    version = 'b'; await until(() => view.lastFrame()?.includes('新版未读'));
    assert.match(view.lastFrame()!, /原始内容/);
    await click(view, '接入'); await until(() => view.lastFrame()?.includes('+新的内容'));
    assert.match(view.lastFrame()!, /未读/); assert.match(view.lastFrame()!, /\[ 标已读 \]/);
    await click(view, '标已读'); await until(() => view.lastFrame()?.includes('[ 撤销 ]'));
    await click(view, '撤销'); await until(() => view.lastFrame()?.includes('[ 标已读 ]'));
  } finally {view.unmount();}
});

test('truncated diffs cannot be marked as fully read', async () => {
  const view = render(<SidebarApp workspace='/tmp/read-truncated' refreshMs={0} signalPollMs={0} size={{columns: 12, rows: 12}}
    readStatus={readStatus} readDiff={() => ({ok: true, text: '+可见片段', revision: 'a'.repeat(64), truncated: true})}/>);
  await until(() => view.lastFrame()?.includes('已截断'));
  assert.doesNotMatch(view.lastFrame()!, /标已读|撤销/);
});
