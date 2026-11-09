import assert from 'node:assert/strict';
import test, {afterEach} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import {randomUUID} from 'node:crypto';
import React from 'react';
import stringWidth from 'string-width';
import {cleanup, render} from 'ink-testing-library';
import {SidebarApp} from '../src/sidebar/app.js';
import {createSidebarOwner} from '../src/sidebar/channel.js';
import {writeSidebarSignal, removeSidebarSignal} from '../src/sidebar/signal.js';
import {readPlanPageRequest, writePlanPageReply, removeSidebarRequests, requestPlanPage} from '../src/sidebar/requests.js';

afterEach(cleanup);
const target = {session_id: 's-reader', plan_task_id: 't-20261109-010000-abcdef', plan_id: 'pl-reader', plan_version: 1, plan_hash: 'a'.repeat(64)};
async function until<T>(read: () => T, timeout = 2000): Promise<NonNullable<T>> {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {const result = read(); if (result) return result; await pause(10);}
  assert.fail('condition not reached');
}

async function click(view: ReturnType<typeof render>, label: string) {
  await pause(20);
  const lines = view.lastFrame()!.split('\n'); const row = lines.findIndex(line => line.includes(`[ ${label} ]`));
  assert.ok(row >= 0, view.lastFrame());
  const column = stringWidth(lines[row]!.slice(0, lines[row]!.indexOf(label))) + 1;
  view.stdin.write(`\u001b[<0;${column};${row + 1}M\u001b[<0;${column};${row + 1}m`);
}


test('revision source reading remains fixed until the exact new artifact is explicitly accepted', async () => {
  const workspace = `/tmp/sidebar-revision-reader-${randomUUID()}`; const owner = createSidebarOwner();
  const run = {requestId: 'revision', traceRunId: null, taskId: 'revision-task', segment: 1, taskStatus: 'running', status: 'running' as const, objective: '修订'};
  const plan = {target, relation: 'revision-source' as const, problem: null, version: 1, status: 'proposed', stale: false, objective: '来源', stepCount: 1, doneSteps: null};
  writeSidebarSignal(workspace, {owner, sessionId: target.session_id, signature: 'source', files: [], active: true, run, plan});
  const view = render(<SidebarApp workspace={workspace} owner={owner} refreshMs={0} signalPollMs={10} size={{columns: 40, rows: 18}}
    readStatus={() => ({ok: true, status: {root: workspace, branch: 'main', head: 'abc', ahead: 0, behind: 0, files: []}})}/>);
  try {
    await until(() => view.lastFrame()?.includes('计划 v1')); view.stdin.write('p');
    const first = await until(() => readPlanPageRequest(workspace, owner));
    writePlanPageReply({...first, outcome: 'page', body: '旧计划正文', nextCursor: null, message: ''});
    await until(() => view.lastFrame()?.includes('旧计划正文')); assert.match(view.lastFrame()!, /修订来源/);
    const nextTarget = {...target, plan_version: 2, plan_hash: 'd'.repeat(64)};
    writeSidebarSignal(workspace, {owner, sessionId: target.session_id, signature: 'result', files: [], active: false,
      run: {...run, status: 'finished', taskStatus: 'completed'}, plan: {...plan, target: nextTarget, relation: 'revision-result', version: 2}});
    await until(() => view.lastFrame()?.includes('接入')); assert.match(view.lastFrame()!, /旧计划正文/); assert.doesNotMatch(view.lastFrame()!, /修订产物/);
    await pause(25);
    const tab = view.lastFrame()!.split('\n')[0]!; const column = stringWidth(tab.slice(0, tab.indexOf('接入'))) + 1;
    view.stdin.write(`\u001b[<0;${column};1M\u001b[<0;${column};1m`); const next = await until(() => {const value = readPlanPageRequest(workspace, owner); return value?.id !== first.id ? value : null;});
    assert.deepEqual(next.target, nextTarget);
    writePlanPageReply({...next, outcome: 'page', body: '新版正文', nextCursor: null, message: ''});
    await until(() => view.lastFrame()?.includes('新版正文')); assert.match(view.lastFrame()!, /修订产物/);
    assert.doesNotMatch(view.lastFrame()!, /旧计划正文/);
  } finally {view.unmount(); removeSidebarSignal(workspace, owner); removeSidebarRequests(workspace, owner);}
});

test('sidebar reads exact plan pages and keeps a page when the next read fails', async () => {
  const workspace = `/tmp/sidebar-plan-reader-${randomUUID()}`; const owner = createSidebarOwner();
  writeSidebarSignal(workspace, {owner, sessionId: target.session_id, signature: 'plan', files: [], active: false,
    plan: {target, relation: 'review', problem: null, version: 1, status: 'proposed', stale: false, objective: '查看完整计划', stepCount: 1, doneSteps: null}});
  const view = render(<SidebarApp workspace={workspace} owner={owner} refreshMs={0} signalPollMs={10} size={{columns: 32, rows: 12}}
    readStatus={() => ({ok: true, status: {root: workspace, branch: 'main', head: 'abc', ahead: 0, behind: 0, files: []}})}/>);
  try {
    await until(() => view.lastFrame()?.includes('计划 v1')); view.stdin.write('p');
    const first = await until(() => readPlanPageRequest(workspace, owner));
    assert.deepEqual(first.target, target); assert.equal(first.cursor, 0);
    writePlanPageReply({...first, outcome: 'page', body: '第一页正文🙂', nextCursor: 6, message: ''});
    await until(() => view.lastFrame()?.includes('第一页正文🙂'));
    assert.match(view.lastFrame()!, /第 1 页/);
    await click(view, '下页');
    const second = await until(() => {const request = readPlanPageRequest(workspace, owner); return request && request.id !== first.id ? request : null;});
    assert.equal(second.cursor, 6);
    writePlanPageReply({...second, outcome: 'error', body: '', nextCursor: null, message: '读取暂时失败'});
    await until(() => view.lastFrame()?.includes('读取暂时失败'));
    assert.match(view.lastFrame()!, /第一页正文🙂/);
    await click(view, '重试');
    const retry = await until(() => {const request = readPlanPageRequest(workspace, owner); return request && request.id !== second.id ? request : null;});
    assert.equal(retry.cursor, 6); assert.deepEqual(retry.target, target);
    writePlanPageReply({...retry, outcome: 'page', body: '第二页中文正文', nextCursor: null, message: ''});
    await until(() => view.lastFrame()?.includes('第二页中文正文'));
    assert.match(view.lastFrame()!, /第 2 页/);
    view.stdin.write('\u001b[D');
    const previous = await until(() => {const request = readPlanPageRequest(workspace, owner); return request && request.id !== retry.id ? request : null;});
    assert.equal(previous.cursor, 0);
  } finally {view.unmount(); removeSidebarSignal(workspace, owner); removeSidebarRequests(workspace, owner);}
});

test('page transport rejects mismatched and oversized replies and cancels without leaving its request', async () => {
  const workspace = `/tmp/sidebar-plan-protocol-${randomUUID()}`; const owner = createSidebarOwner();
  writeSidebarSignal(workspace, {owner, sessionId: target.session_id, signature: 'ready', files: [], active: false});
  try {
    for (const change of [{target: {...target, plan_hash: 'b'.repeat(64)}}, {body: '中'.repeat(16001)}, {nextCursor: 9}]) {
      const controller = new AbortController();
      const pending = requestPlanPage(workspace, owner, target.session_id, target, 0, controller.signal);
      const rejected = assert.rejects(pending, /不匹配/);
      const request = await until(() => readPlanPageRequest(workspace, owner));
      writePlanPageReply({...request, outcome: 'page', body: '原文', nextCursor: null, message: '', ...change});
      await rejected; assert.equal(readPlanPageRequest(workspace, owner), null);
    }
    const controller = new AbortController();
    const pending = requestPlanPage(workspace, owner, target.session_id, target, 0, controller.signal);
    const rejected = assert.rejects(pending, /取消/); controller.abort(); await rejected;
    assert.equal(readPlanPageRequest(workspace, owner), null);
  } finally {removeSidebarSignal(workspace, owner); removeSidebarRequests(workspace, owner);}
});

test('a new plan remains pending during a page read and a late old reply cannot replace the adopted plan', async () => {
  const workspace = `/tmp/sidebar-plan-update-${randomUUID()}`; const owner = createSidebarOwner();
  const input = {owner, sessionId: target.session_id, signature: 'first', files: [], active: false,
    plan: {target, relation: 'review' as const, problem: null, version: 1, status: 'proposed', stale: false, objective: '旧计划', stepCount: 1, doneSteps: null}};
  writeSidebarSignal(workspace, input);
  const view = render(<SidebarApp workspace={workspace} owner={owner} refreshMs={0} signalPollMs={10} size={{columns: 40, rows: 12}}
    readStatus={() => ({ok: true, status: {root: workspace, branch: 'main', head: 'abc', ahead: 0, behind: 0, files: []}})}/>);
  try {
    await until(() => view.lastFrame()?.includes('计划 v1')); view.stdin.write('p');
    const first = await until(() => readPlanPageRequest(workspace, owner));
    writePlanPageReply({...first, outcome: 'page', body: '旧版第一页', nextCursor: 5, message: ''});
    await until(() => view.lastFrame()?.includes('旧版第一页')); view.stdin.write('\u001b[C');
    const old = await until(() => {const request = readPlanPageRequest(workspace, owner); return request && request.id !== first.id ? request : null;});
    const newer = {...target, plan_version: 2, plan_hash: 'b'.repeat(64)};
    writeSidebarSignal(workspace, {...input, signature: 'new', plan: {...input.plan, version: 2, target: newer, objective: '新版计划'}});
    await until(() => view.lastFrame()?.includes('有更新'));
    assert.match(view.lastFrame()!, /旧版第一页/);
    view.stdin.write('r');
    const next = await until(() => {const request = readPlanPageRequest(workspace, owner); return request && request.id !== old.id ? request : null;});
    assert.deepEqual(next.target, newer); assert.equal(next.cursor, 0);
    writePlanPageReply({...old, outcome: 'page', body: '迟到旧版第二页', nextCursor: null, message: ''});
    await pause(80); assert.doesNotMatch(view.lastFrame()!, /迟到旧版第二页/);
    writePlanPageReply({...next, outcome: 'page', body: '准确新版正文', nextCursor: null, message: ''});
    await until(() => view.lastFrame()?.includes('准确新版正文'));
    await click(view, '返回'); await until(() => view.lastFrame()?.includes('工作区 vs HEAD'));
  } finally {view.unmount(); removeSidebarSignal(workspace, owner); removeSidebarRequests(workspace, owner);}
});

test('plan selection extends across page boundaries and preserves exact Unicode source offsets', async () => {
  const {draftPath, removeDraftChannel} = await import('../src/sidebar/draft-channel.js');
  const {readChannel} = await import('../src/sidebar/channel.js');
  const workspace = `/tmp/sidebar-plan-excerpt-${randomUUID()}`; const owner = createSidebarOwner();
  const copied: string[] = [];
  writeSidebarSignal(workspace, {owner, sessionId: target.session_id, signature: 'plan', files: [], active: false,
    plan: {target, relation: 'review', problem: null, version: 1, status: 'proposed', stale: false, objective: '计划选区', stepCount: 1, doneSteps: null}});
  const view = render(<SidebarApp workspace={workspace} owner={owner} refreshMs={0} signalPollMs={10} size={{columns: 40, rows: 18}}
    copyText={text => {copied.push(text); return 'test';}}
    readStatus={() => ({ok: true, status: {root: workspace, branch: 'main', head: 'abc', ahead: 0, behind: 0, files: []}})}/>);
  const drag = async (label: string) => {
    await pause(25); const lines = view.lastFrame()!.split('\n'); const row = lines.findIndex(line => line.includes(label));
    assert.ok(row >= 0, view.lastFrame()); const column = stringWidth(lines[row]!.slice(0, lines[row]!.indexOf(label))) + 1;
    view.stdin.write(`\u001b[<0;${column};${row + 1}M\u001b[<32;${column + stringWidth(label)};${row + 1}M\u001b[<0;${column + stringWidth(label)};${row + 1}m`);
  };
  try {
    await until(() => view.lastFrame()?.includes('计划 v1')); view.stdin.write('p');
    const first = await until(() => readPlanPageRequest(workspace, owner));
    const firstBody = '前缀🙂\t开始🙂甲\n中间'; const secondBody = '乙\n结束🙂\n尾部';
    writePlanPageReply({...first, outcome: 'page', body: firstBody, nextCursor: Array.from(firstBody).length, message: ''});
    await until(() => view.lastFrame()?.includes('开始🙂甲')); await drag('开始🙂甲');
    await until(() => view.lastFrame()?.includes('[ 续选 ]'));
    await click(view, '续选'); await click(view, '下页');
    const next = await until(() => {const value = readPlanPageRequest(workspace, owner); return value?.id !== first.id ? value : null;});
    writePlanPageReply({...next, outcome: 'page', body: secondBody, nextCursor: null, message: ''});
    await until(() => view.lastFrame()?.includes('结束🙂')); await drag('结束🙂');
    await click(view, '复制'); await until(() => copied.length);
    assert.match(copied[0]!, /开始🙂甲\n> 中间乙\n> 结束🙂/);
    assert.match(copied[0]!, /pl-reader v1/);
    await click(view, '附回');
    const request = await until(() => readChannel(draftPath(workspace, owner)));
    assert.equal(request.text, '开始🙂甲\n中间乙\n结束🙂');
    assert.deepEqual(request.source, {kind: 'plan', target, cursor: 4, length: 12, revision: target.plan_hash});
    assert.equal(request.requestId, null);
    assert.match(view.lastFrame()!, /第 2 页/);
  } finally {view.unmount(); removeSidebarSignal(workspace, owner); removeSidebarRequests(workspace, owner); removeDraftChannel(workspace, owner);}
});
