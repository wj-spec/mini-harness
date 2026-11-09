import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import stringWidth from 'string-width';
import {cleanup, render} from 'ink-testing-library';
import {App} from '../src/app.js';
import type {BackendClient} from '../src/backend.js';
import type {PlanReviewResult, PlanRevisionResult, ReviewRef, ServerEvent} from '../src/protocol.js';
import type {SidebarSignalInput} from '../src/sidebar/signal.js';
import {randomUUID} from 'node:crypto';
import {readFileSync, rmSync} from 'node:fs';
import {sidebarChannelPath, writeChannel} from '../src/sidebar/channel.js';

const target: ReviewRef = {session_id: 'test', plan_task_id: 't-plan', plan_id: 'p-a', plan_version: 1, plan_hash: 'a'.repeat(64)};
const receipt = (overrides: Partial<PlanReviewResult> = {}): PlanReviewResult => ({
  target, outcome: 'ready', replacement_target: null, code: null, message: '计划可审阅', launch: null,
  summary: {objective: '修复中文输入', status: 'proposed', executable: false, step_count: 2, criterion_count: 1, question_count: 0},
  body_page: '# 修复中文输入\n\n完整约束与步骤', next_cursor: null,
  available_actions: ['approve-execute', 'approve-only', 'dismiss'], ...overrides,
} as PlanReviewResult);

class Client {
  listeners = new Set<(event: ServerEvent) => void>();
  commands: {command: string; args?: Record<string, unknown>}[] = [];
  runs: string[] = [];
  cancellations: string[] = [];
  approvals: string[] = [];
  capabilities() {return ['plan-review', 'plan-revision', 'tasks'];}
  subscribe(listener: (event: ServerEvent) => void) {this.listeners.add(listener); return () => this.listeners.delete(listener);}
  command(command: string, args?: Record<string, unknown>) {this.commands.push({command, args}); return `command-${this.commands.length}`;}
  run(text: string) {this.runs.push(text); return `run-${this.runs.length}`;}
  cancel(target: string) {this.cancellations.push(target); return 'cancel-1';}
  approve(id: string) {this.approvals.push(id); return 'approval-1';}
  async shutdown() {}
  emit(event: Record<string, unknown>) {for (const listener of this.listeners) listener({v: 1, id: null, ...event} as ServerEvent);}
  review(id: string, result = receipt()) {this.emit({type: 'plan_review', id, ...result});}
}

const revisionReceipt = (overrides: Partial<PlanRevisionResult> = {}): PlanRevisionResult => ({
  target, feedback: '补充测试', outcome: 'running', revision_id: 'c'.repeat(64), code: null,
  message: '修订任务已启动', result_target: null,
  task: {task_id: 't-revision', status: 'running', segment: 1, run_id: 'command-2', run_started: true}, ...overrides,
});


async function waitFor(check: () => boolean) {
  const deadline = Date.now() + 3000;
  while (!check() && Date.now() < deadline) await pause(5);
  assert.ok(check(), 'observable revision state did not arrive');
}

test('revision inspection binds its source and exact produced plan without inheriting execution acceptance', async () => {
  const signals: SidebarSignalInput[] = [];
  const {client, enter, key, lastFrame} = await mount(100, 40, undefined, (_workspace, signal) => signals.push(signal));
  await enter('/plan'); client.review('command-1'); await pause(25);
  await key('中文草稿'); await key('\u001b[D');
  await key('\u0012'); await key('r'); await key('\r'); await key('补充测试'); await key('\r'); await key('\r');
  client.emit({type: 'plan_revision', id: 'command-2', ...revisionReceipt()}); await pause(25);
  const deadline = Date.now() + 2000;
  while (signals.at(-1)?.run?.requestId !== 'command-2' && Date.now() < deadline) await pause(5);
  assert.equal(signals.at(-1)?.run?.requestId, 'command-2');
  assert.equal(signals.at(-1)?.run?.taskId, 't-revision');
  assert.deepEqual(signals.at(-1)?.plan?.target, target);
  assert.equal(signals.at(-1)?.plan?.relation, 'revision-source');
  const newer = {...target, plan_version: 2, plan_hash: 'd'.repeat(64)};
  client.emit({type: 'plan_revision', id: 'command-2', ...revisionReceipt({outcome: 'submitted', result_target: newer,
    task: {task_id: 't-revision', status: 'completed', segment: 1, run_id: 'command-2', run_started: false}})});
  client.emit({type: 'done', id: 'command-2', result: {text: '修订完成', status: 'done', steps: 1, usage: {}, cost_usd: 0, run_id: 'trace-revision', stop_reason: 'plan_submitted'}});
  await waitFor(() => client.commands[2]?.args?.action === 'review');
  client.review('command-3', receipt({target: newer, summary: {...receipt().summary!, objective: '修订后的准确目标', step_count: 3}}));
  await waitFor(() => signals.at(-1)?.plan?.objective === '修订后的准确目标');
  assert.equal(signals.at(-1)?.plan?.relation, 'revision-result');
  assert.deepEqual(signals.at(-1)?.plan?.target, newer);
  assert.equal(signals.at(-1)?.plan?.objective, '修订后的准确目标');
  assert.equal(signals.at(-1)?.plan?.acceptance, null);
  assert.equal(signals.at(-1)?.run?.traceRunId, 'trace-revision');
  await key('中'); assert.match(lastFrame()!, /中文草中稿/);
  await key('\u001b[F'); await key('\u0015'); await enter('/plan');
  assert.equal(client.commands[3]?.args?.action, 'review');
  client.review('command-4', receipt({target: {...target, plan_id: 'other'}, body_page: '另一计划'})); await pause(25);
  assert.deepEqual(signals.at(-1)?.plan?.target, newer);
  assert.equal(client.approvals.length, 0);
});

test('a late revision query fills the original result while a different run remains active', async () => {
  const signals: SidebarSignalInput[] = [];
  const {client, enter, key, lastFrame} = await mount(100, 40, undefined, (_workspace, signal) => signals.push(signal),
    () => ({outcome: 'started', message: 'test host'}));
  await enter('/plan'); client.review('command-1'); await pause(25);
  await key('\u0012'); await key('r'); await key('\r'); await key('补充测试'); await key('\r'); await key('\r');
  client.emit({type: 'plan_revision', id: 'command-2', ...revisionReceipt()}); await pause(25);
  client.emit({type: 'done', id: 'command-2', result: {text: '等待回执', status: 'done', steps: 1, usage: {}, cost_usd: 0, run_id: 'trace-old'}});
  await waitFor(() => client.commands[2]?.args?.action === 'query-revision');
  assert.equal(client.commands[2]?.args?.action, 'query-revision');
  await key('\u001b'); await enter('新任务'); await key('保留草稿');
  assert.equal(client.runs.length, 1, lastFrame());
  const newer = {...target, plan_version: 2, plan_hash: 'd'.repeat(64)};
  client.emit({type: 'plan_revision', id: 'command-3', ...revisionReceipt({outcome: 'submitted', result_target: newer,
    task: {task_id: 't-revision', status: 'completed', segment: 1, run_id: 'command-2', run_started: false}})});
  await waitFor(() => client.commands[3]?.args?.action === 'review');
  client.review('command-4', receipt({target: newer})); await pause(25);
  assert.equal(signals.at(-1)?.run?.requestId, 'run-1'); assert.equal(signals.at(-1)?.plan, undefined);
  const lines = lastFrame()!.split('\n'); const row = lines.findIndex(line => line.includes('[ 计划 ]')); assert.ok(row >= 0, lastFrame());
  const column = stringWidth(lines[row]!.slice(0, lines[row]!.indexOf('[ 计划 ]'))) + 2;
  await key(`\u001b[<0;${column};${row + 1}M\u001b[<0;${column};${row + 1}m`);
  await waitFor(() => signals.at(-1)?.navigation?.mode === 'result' && signals.at(-1)?.plan?.target?.plan_hash === newer.plan_hash);
  assert.equal(signals.at(-1)?.navigation?.mode, 'result');
  assert.equal(signals.at(-1)?.run?.requestId, 'command-2');
  assert.deepEqual(signals.at(-1)?.plan?.target, newer);
  assert.equal(signals.at(-1)?.plan?.relation, 'revision-result');
  assert.match(lastFrame()!, /保留草稿/); await key('\u0003'); assert.deepEqual(client.cancellations, ['run-1']);
});

test('plan excerpts recheck the exact version without changing the active run, review card or draft cursor', async () => {
  const signals: SidebarSignalInput[] = [];
  const {client, enter, key, lastFrame, unmount} = await mount(100, 40, undefined, (_workspace, signal) => signals.push(signal));
  const exact: ReviewRef = {...target, plan_task_id: 't-20261109-010000-abcdef', plan_id: 'pl-excerpt'};
  const until = async (read: () => boolean) => {const end = Date.now() + 2000; while (!read() && Date.now() < end) await pause(10); assert.ok(read());};
  const read = (file: string) => {try {return JSON.parse(readFileSync(file, 'utf8')) as Record<string, unknown>;} catch {return null;}};
  await enter('/plan'); client.review('command-1', receipt({target: exact})); await pause(25);
  await enter('继续当前任务'); await key('原草稿'); await key('\u001b[D');
  const workspace = '/tmp/review-test'; const owner = signals.at(-1)!.owner!;
  const file = sidebarChannelPath(workspace, 'signal', owner).replace('.signal.json', '.draft-request.json');
  const replyFile = file.replace('.draft-request.json', '.draft-reply.json');
  const request = {kind: 'append-excerpt', id: randomUUID(), workspace, owner, sessionId: 'test', requestId: null, createdAt: Date.now(),
    source: {kind: 'plan', target: exact, cursor: 5, length: 5, revision: exact.plan_hash}, start: {line: 0, offset: 0}, end: {line: 0, offset: 6}, text: '中文🙂原文'};
  try {
    writeChannel(file, request); await until(() => client.commands.length === 2);
    assert.deepEqual(client.commands[1], {command: 'plan', args: {action: 'review', target: exact, cursor: 5}});
    client.review('command-2', receipt({target: exact, body_page: '中文🙂原文还有', next_cursor: null}));
    await until(() => read(replyFile)?.outcome === 'appended');
    await key('中'); assert.match(lastFrame()!, /原草中稿/);
    await key('\u0003'); assert.deepEqual(client.cancellations, ['run-1']);
    await key('\u0012'); await key('b'); await key('\r');
    assert.match(lastFrame()!, /完整约束与步骤/); assert.doesNotMatch(lastFrame()!, /中文🙂原文还有/);
    await key('\u001b');
    const stale = {...request, id: randomUUID()}; writeChannel(file, stale); await until(() => client.commands.length === 3);
    client.review('command-3', receipt({target: exact, outcome: 'unavailable', code: 'plan_stale', summary: null, body_page: '', message: '原版本已失效'}));
    await until(() => read(replyFile)?.id === stale.id);
    assert.equal(read(replyFile)?.outcome, 'error'); assert.match(String(read(replyFile)?.message), /失效/);
    assert.equal(client.runs.length, 1); assert.equal(client.approvals.length, 0);
  } finally {unmount(); rmSync(file, {force: true}); rmSync(replyFile, {force: true});}
});

test('long plan excerpts recheck every source page and duplicate delivery does not requery or append again', async () => {
  const signals: SidebarSignalInput[] = [];
  const {client, enter, key, unmount} = await mount(100, 40, undefined, (_workspace, signal) => signals.push(signal));
  const exact: ReviewRef = {...target, plan_task_id: 't-20261109-010000-abcdef', plan_id: 'pl-long-excerpt'};
  const until = async (read: () => boolean) => {const end = Date.now() + 2000; while (!read() && Date.now() < end) await pause(10); assert.ok(read());};
  const workspace = '/tmp/review-test';
  await enter('/plan'); client.review('command-1', receipt({target: exact})); await pause(25);
  const owner = signals.at(-1)!.owner!;
  const file = sidebarChannelPath(workspace, 'signal', owner).replace('.signal.json', '.draft-request.json');
  const replyFile = file.replace('.draft-request.json', '.draft-reply.json');
  const text = 'A'.repeat(16000) + '终🙂';
  const request = {kind: 'append-excerpt', id: randomUUID(), workspace, owner, sessionId: 'test', requestId: null, createdAt: Date.now(),
    source: {kind: 'plan', target: exact, cursor: 4, length: 16002, revision: exact.plan_hash}, start: {line: 0, offset: 0}, end: {line: 0, offset: text.length}, text};
  const read = () => {try {return JSON.parse(readFileSync(replyFile, 'utf8')) as Record<string, unknown>;} catch {return null;}};
  try {
    writeChannel(file, request); await until(() => client.commands.length === 2);
    client.review('command-2', receipt({target: exact, body_page: 'A'.repeat(16000), next_cursor: 16004}));
    await until(() => client.commands.length === 3);
    assert.deepEqual(client.commands[2], {command: 'plan', args: {action: 'review', target: exact, cursor: 16004}});
    assert.equal(client.runs.length, 0);
    client.review('command-3', receipt({target: exact, body_page: '终🙂后续', next_cursor: null}));
    await until(() => read()?.outcome === 'appended');
    writeChannel(file, request); await pause(150); assert.equal(client.commands.length, 3);
    await key('\r'); await until(() => client.runs.length === 1);
    assert.equal(client.runs[0]!.split('[侧栏引用').length, 2);
    assert.ok(client.runs[0]!.includes('> ' + text)); assert.ok(!client.runs[0]!.includes('后续'));
  } finally {unmount(); rmSync(file, {force: true}); rmSync(replyFile, {force: true});}
});

test('sidebar plan pages use a separate read request and leave the composer and review card untouched', async () => {
  const signals: SidebarSignalInput[] = [];
  const {client, enter, key, lastFrame, unmount} = await mount(80, 24, undefined, (_workspace, signal) => signals.push(signal));
  const exact: ReviewRef = {...target, plan_task_id: 't-20261109-010000-abcdef', plan_id: 'pl-reader'};
  await enter('/plan'); client.review('command-1', receipt({target: exact})); await pause(25);
  await key('保留中文草稿');
  const owner = signals.at(-1)!.owner!;
  const base = sidebarChannelPath('/tmp/review-test', 'signal', owner);
  const requestFile = base.replace('.signal.json', '.request.json');
  const replyFile = base.replace('.signal.json', '.reply.json');
  const request = {kind: 'plan-page', id: randomUUID(), workspace: '/tmp/review-test', owner, sessionId: 'test',
    target: exact, cursor: 16000, createdAt: Date.now()};
  try {
    writeChannel(requestFile, request);
    const deadline = Date.now() + 1500;
    while (client.commands.length < 2 && Date.now() < deadline) await pause(10);
    assert.deepEqual(client.commands[1], {command: 'plan', args: {action: 'review', target: exact, cursor: 16000}});
    client.review('command-2', receipt({target: exact, body_page: '第二页中文正文', next_cursor: null}));
    let reply: Record<string, unknown> | undefined;
    for (let i = 0; i < 100 && !reply; i++) {
      try {reply = JSON.parse(readFileSync(replyFile, 'utf8'));} catch {await pause(10);}
    }
    assert.equal(reply?.id, request.id); assert.equal(reply?.outcome, 'page');
    assert.equal(reply?.body, '第二页中文正文');
    assert.equal(reply?.cursor, 16000); assert.equal(reply?.nextCursor, null);
    await pause(250); assert.equal(client.commands.length, 2, 'one request must not trigger repeated backend reads');
    assert.match(lastFrame()!, /保留中文草稿/);
    await key('\u0012'); await key('b'); await key('\r');
    assert.match(lastFrame()!, /完整约束与步骤/);
    assert.doesNotMatch(lastFrame()!, /第二页中文正文/);
    assert.deepEqual(client.runs, []); assert.deepEqual(client.approvals, []);
  } finally {unmount(); rmSync(requestFile, {force: true}); rmSync(replyFile, {force: true});}
});

test('sidebar read requests reject foreign plans and never replace an active run cancellation target', async () => {
  const signals: SidebarSignalInput[] = [];
  const {client, enter, key, unmount} = await mount(80, 24, undefined, (_workspace, signal) => signals.push(signal));
  const exact: ReviewRef = {...target, plan_task_id: 't-20261109-010000-abcdef', plan_id: 'pl-active'};
  await enter('/plan'); client.review('command-1', receipt({target: exact})); await pause(25);
  const owner = signals.at(-1)!.owner!;
  const requestFile = sidebarChannelPath('/tmp/review-test', 'request', owner);
  const replyFile = sidebarChannelPath('/tmp/review-test', 'reply', owner);
  const request = {kind: 'plan-page', id: randomUUID(), workspace: '/tmp/review-test', owner, sessionId: 'other',
    target: {...exact, session_id: 'other'}, cursor: 0, createdAt: Date.now()};
  const reply = () => {try {return JSON.parse(readFileSync(replyFile, 'utf8'));} catch {return null;}};
  try {
    writeChannel(requestFile, request);
    for (let i = 0; i < 150 && reply()?.id !== request.id; i++) await pause(10);
    assert.equal(reply()?.outcome, 'error'); assert.equal(client.commands.length, 1);
    await enter('继续当前任务');
    const own = {...request, id: randomUUID(), sessionId: 'test', target: exact}; writeChannel(requestFile, own);
    for (let i = 0; i < 150 && client.commands.length < 2; i++) await pause(10);
    assert.equal(client.commands.length, 2);
    client.review('command-2', receipt({target: {...exact, plan_hash: 'b'.repeat(64)}, body_page: '不应接受的正文'}));
    for (let i = 0; i < 100 && reply()?.id !== own.id; i++) await pause(10);
    assert.equal(reply()?.outcome, 'error'); assert.match(reply()?.message, /不匹配/);
    await key('\u0003'); assert.deepEqual(client.cancellations, ['run-1']);
    client.emit({type: 'closed'});
    const closed = {...own, id: randomUUID()}; writeChannel(requestFile, closed);
    for (let i = 0; i < 100 && reply()?.id !== closed.id; i++) await pause(10);
    assert.equal(reply()?.outcome, 'error'); assert.match(reply()?.message, /断开/);
    assert.equal(client.commands.length, 2);
  } finally {unmount(); rmSync(requestFile, {force: true}); rmSync(replyFile, {force: true});}
});

test('revision history restores sent feedback and only explicitly resumes the original task', async () => {
  const {client, key, enter, lastFrame} = await mount();
  client.capabilities = () => ['plan-review', 'plan-revision', 'plan-revision-history', 'tasks'];
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('普通草稿'); await key('\u0012'); await key('h'); await key('\r');
  assert.deepEqual(client.commands[1], {command: 'plan', args: {action: 'list-revisions', target}});
  const saved = revisionReceipt({outcome: 'paused', message: '修订已暂停',
    task: {task_id: 't-revision', status: 'paused', segment: 2, run_id: 'old-run', run_started: false}});
  client.emit({type: 'plan_revision_history', id: 'command-2', target, records: [saved], next_cursor: null, code: null, message: '已读取修订记录'});
  await pause(20);
  assert.match(lastFrame()!, /修订记录/); assert.match(lastFrame()!, /补充测试/);
  assert.match(lastFrame()!, /普通草稿/); assert.equal(client.commands.length, 2);
  await key('\t'); await key('\r');
  assert.match(lastFrame()!, /续做/); assert.equal(client.commands.length, 2);
  await key('\t'); await key('\u001b[C'); await key('\r');
  assert.deepEqual(client.commands[2], {command: 'plan', args: {action: 'resume-revision', target, feedback: '补充测试', expected_segment: 2}});
});

test('saved feedback selection never overwrites an unsent draft for the same source', async () => {
  const {client, key, enter, lastFrame} = await mount();
  client.capabilities = () => ['plan-review', 'plan-revision', 'plan-revision-history', 'tasks'];
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('\u0012'); await key('r'); await key('\r'); await key('还未发送的反馈'); await key('\u001b');
  await key('\u0012'); await key('h'); await key('\r');
  const saved = revisionReceipt({outcome: 'paused', task: {task_id: 't-old', status: 'paused', segment: 1, run_id: 'old', run_started: false}});
  client.emit({type: 'plan_revision_history', id: 'command-2', target,
    records: [saved, {...saved, feedback: '另一条已发送反馈', revision_id: 'd'.repeat(64)}], next_cursor: null, code: null, message: '已读取修订记录'});
  await pause(20); await key('\u001b[B'); await key('\t'); await key('\r');
  assert.match(lastFrame()!, /另一条已发送反馈/);
  await key('\t'); await key('\r');
  assert.deepEqual(client.commands[2], {command: 'plan', args: {action: 'query-revision', target, feedback: '另一条已发送反馈'}});
  await key('\u001b'); await key('\u0012'); await key('r'); await key('\r');
  assert.match(lastFrame()!, /还未发送的反馈/);
  assert.equal(client.commands.length, 3);
});

test('a late history response cannot clear a confirmation selected after leaving history', async () => {
  const {client, key, enter, lastFrame} = await mount();
  client.capabilities = () => ['plan-review', 'plan-revision', 'plan-revision-history', 'tasks'];
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('\u0012'); await key('h'); await key('\r'); await key('\u001b');
  await key('\u0012'); await key('r'); await key('\r'); await key('新的反馈'); await key('\r');
  assert.match(lastFrame()!, /› 确认修订 ‹/);
  client.emit({type: 'plan_revision_history', id: 'command-2', target, records: [], next_cursor: null, code: null, message: '无记录'});
  await pause(20);
  assert.match(lastFrame()!, /› 确认修订 ‹/); assert.match(lastFrame()!, /新的反馈/);
  assert.equal(client.commands.length, 2);
});

test('explicitly viewing a restored result opens that version even if another card arrived meanwhile', async () => {
  const {client, key, enter, lastFrame} = await mount();
  client.capabilities = () => ['plan-review', 'plan-revision', 'plan-revision-history', 'tasks'];
  await enter('/plan'); client.review('command-1'); await pause(20);
  await enter('/plan'); await key('\u0012'); await key('h'); await key('\r');
  const other = {...target, plan_id: 'p-b', plan_task_id: 't-b'};
  client.review('command-2', receipt({target: other, body_page: '另一份计划正文'}));
  const newer = {...target, plan_version: 2, plan_hash: 'd'.repeat(64)};
  client.emit({type: 'plan_revision_history', id: 'command-3', target, records: [revisionReceipt({outcome: 'submitted', result_target: newer,
    task: {task_id: 't-revision', status: 'completed', segment: 1, run_id: 'old', run_started: false}})], next_cursor: null, code: null, message: '已读取'});
  await pause(20); await key('\t'); await key('\r'); await key('\t'); await key('\u001b[C'); await key('\r');
  assert.deepEqual(client.commands[3], {command: 'plan', args: {action: 'review', target: newer}});
  client.review('command-4', receipt({target: newer, body_page: '明确选择的新版本正文'})); await pause(20);
  await key('\u0012'); await key('b'); await key('\r');
  assert.match(lastFrame()!, /明确选择的新版本正文/); assert.doesNotMatch(lastFrame()!, /另一份计划正文/);
});

test('revision history pages and retries are read-only and keep the displayed family at 32 columns', async () => {
  const {client, key, enter, lastFrame} = await mount(32, 12);
  client.capabilities = () => ['plan-review', 'plan-revision', 'plan-revision-history', 'tasks'];
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('\u0012'); await key('h'); await key('\r');
  const saved = revisionReceipt({outcome: 'paused', task: {task_id: 't-old', status: 'paused', segment: 1, run_id: 'old', run_started: false}});
  client.emit({type: 'plan_revision_history', id: 'command-2', target,
    records: Array.from({length: 10}, (_, index) => ({...saved, feedback: `反馈 ${index}`})), next_cursor: 'd'.repeat(64), code: null, message: '已读取'});
  await pause(20); await key('\u001b[6~');
  assert.deepEqual(client.commands[2], {command: 'plan', args: {action: 'list-revisions', target, cursor: 'd'.repeat(64)}});
  client.emit({type: 'error', id: 'command-3', code: 'transport_error', message: '读取失败'}); await pause(20);
  assert.match(lastFrame()!, /修订记录未读取/); assert.match(lastFrame()!, /重试/);
  await key('\t'); await key('\r');
  assert.deepEqual(client.commands[3], client.commands[2]);
  client.emit({type: 'plan_revision_history', id: 'command-4', target, records: [{...saved, feedback: '第二页反馈'}], next_cursor: null, code: null, message: '已读取'});
  await pause(20); assert.match(lastFrame()!, /第 2 页/); assert.match(lastFrame()!, /第二页反馈/);
  assert.ok(lastFrame()!.split('\n').length <= 12);
  assert.ok(lastFrame()!.split('\n').every(line => stringWidth(line) <= 32));
  await key('\u001b[5~');
  assert.deepEqual(client.commands[4], {command: 'plan', args: {action: 'list-revisions', target}});
  assert.equal(client.runs.length, 0);
});

test('a published revision with a paused task offers its new version and never offers resume', async () => {
  const {client, key, enter, lastFrame} = await mount(32, 12);
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('\u0012'); await key('r'); await key('\r'); await key('补充测试'); await key('\r'); await key('\r');
  client.emit({type: 'plan_revision', id: 'command-2', ...revisionReceipt({outcome: 'submitted',
    result_target: {...target, plan_version: 2, plan_hash: 'b'.repeat(64)},
    task: {task_id: 't-revision', status: 'paused', segment: 1, run_id: 'old', run_started: false}})}); await pause(20);
  assert.doesNotMatch(lastFrame()!, /\[ 续做 \]/); assert.match(lastFrame()!, /新版/);
  assert.ok(lastFrame()!.split('\n').every(line => stringWidth(line) <= 32));
  await key('\t'); await key('\u001b[C');
  assert.match(lastFrame()!, /› 新版 ‹/);
});

afterEach(cleanup);
async function mount(columns = 80, rows = 24, copyText?: (text: string) => string | null, publishSidebarSignal?: (workspace: string, signal: SidebarSignalInput) => void, launchSidebar?: React.ComponentProps<typeof App>['launchSidebar']) {
  const client = new Client();
  const view = render(<App client={client as unknown as BackendClient} windowSize={{columns, rows}} copyText={copyText} publishSidebarSignal={publishSidebarSignal} launchSidebar={launchSidebar}/>);
  await pause(20);
  client.emit({type: 'ready', workspace: '/tmp/review-test', model: 'fake', session_id: 'test', capabilities: client.capabilities()});
  client.emit({type: 'status', phase: 'idle'});
  await pause(20);
  const key = async (value: string) => {view.stdin.write(value); await pause(20);};
  const enter = async (value: string) => {await key(value); await key('\r');};
  return {client, ...view, key, enter};
}

test('sidebar keeps the exact reviewed plan through status updates and binds only its execution', async () => {
  const signals: SidebarSignalInput[] = [];
  const {client, enter, key} = await mount(80, 24, undefined, (_workspace, signal) => signals.push(signal));
  await enter('/plan'); client.review('command-1'); await pause(25);
  assert.deepEqual(Reflect.get(signals.at(-1)?.plan ?? {}, 'target'), target);
  assert.equal(Reflect.get(signals.at(-1)?.plan ?? {}, 'relation'), 'review');
  client.emit({type: 'notice', message: '不相关状态刷新'}); await pause(25);
  assert.deepEqual(Reflect.get(signals.at(-1)?.plan ?? {}, 'target'), target);
  await key('\u0012'); await key('e'); await key('\r');
  client.review('command-2', receipt({outcome: 'launched', available_actions: [],
    launch: {task_id: 't-execution', run_id: 'command-2', segment: 1, status: 'running', run_started: true}}));
  await pause(25);
  assert.equal(signals.at(-1)?.run?.taskId, 't-execution');
  assert.equal(Reflect.get(signals.at(-1)?.plan ?? {}, 'relation'), 'execution');
  client.emit({type: 'done', id: 'command-2', result: {text: 'done', status: 'done', steps: 1, usage: {}, cost_usd: 0, run_id: 'trace-exec', stop_reason: '', verified: true}});
  await pause(25);
  assert.equal(signals.at(-1)?.plan?.acceptance, true);
  assert.deepEqual(Reflect.get(signals.at(-1)?.plan ?? {}, 'target'), target);
  await enter('/resume');
  client.emit({type: 'task_state', id: 'command-3', task: {task_id: 't-execution', kind: 'run', status: 'running', segment: 2,
    objective: '继续执行', goal_id: null, updated_at: 'now', plan_id: target.plan_id, plan_version: target.plan_version}});
  await pause(25);
  assert.deepEqual(Reflect.get(signals.at(-1)?.plan ?? {}, 'target'), target);
  assert.equal(signals.at(-1)?.plan?.acceptance, null, 'resuming a new segment cannot reuse prior verification');
  assert.equal(signals.at(-1)?.run?.segment, 2);
  client.emit({type: 'done', id: 'command-3', result: {text: 'done', status: 'done', steps: 1, usage: {}, cost_usd: 0, run_id: 'trace-resumed', stop_reason: ''}});
  await pause(25);
  await enter('独立的新任务');
  assert.equal(signals.at(-1)?.plan, undefined);
  assert.deepEqual(signals.at(-1)?.files, []);
  await enter('/plan'); client.review('command-4'); await pause(25);
  assert.equal(signals.at(-1)?.plan, undefined, 'an old plan query cannot bind an unrelated running task');
});

test('plan discovery shows an exact card without stealing the draft or authorizing bare letters', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan');
  assert.deepEqual(client.commands, [{command: 'plan', args: {action: 'review'}}]);
  await key('下一轮草稿');
  client.review('command-1');
  await pause(20);
  assert.match(lastFrame()!, /计划 v1/);
  assert.match(lastFrame()!, /修复中文输入/);
  assert.match(lastFrame()!, /Ctrl\+R审阅/);
  await key('ad');
  assert.match(lastFrame()!, /下一轮草稿ad/);
  assert.equal(client.commands.length, 1);
  assert.equal(client.runs.length, 0);
});

test('revision feedback stays bound to the displayed plan and leaves the ordinary draft untouched', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await enter('/plan');
  await key('普通中文草稿'); await key('\u001b[D');
  await key('\u0012'); await key('r'); await key('\r');
  client.review('command-2', receipt({target: {...target, plan_id: 'p-b', plan_task_id: 't-b'},
    summary: {...receipt().summary!, objective: '另一份计划'}})); await pause(20);
  assert.match(lastFrame()!, /修订反馈.*v1/);
  assert.match(lastFrame()!, /修复中文输入/);
  assert.doesNotMatch(lastFrame()!, /审批按钮/);
  await key('补充空输入校验');
  assert.equal(client.commands.length, 2);
  await key('\u001b');
  await key('保留');
  assert.match(lastFrame()!, /普通中文草保留稿/);
  await key('\u0012'); await key('\u001b[D'); await key('r'); await key('\r');
  assert.match(lastFrame()!, /补充空输入校验/);
  await key('\r');
  assert.match(lastFrame()!, /确认修订/);
  assert.equal(client.commands.length, 2);
  await key('\r'); await key('\r');
  assert.deepEqual(client.commands[2], {command: 'plan', args: {action: 'request-changes', target, feedback: '补充空输入校验'}});
  assert.equal(client.commands.length, 3);
  assert.match(lastFrame()!, /普通中文草保留稿/);
});

test('Tab from revision text selects the first visible action exactly once', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('\u0012'); await key('r'); await key('\r'); await key('补充测试');
  await key('\t');
  assert.match(lastFrame()!, /› 下一步 ‹/);
  await key('\r');
  assert.match(lastFrame()!, /确认修订/);
  assert.equal(client.commands.length, 1);
});

test('a submitted revision discovers its exact new card once and cancellation targets the revision run', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('普通草稿'); await key('\u0012'); await key('r'); await key('\r'); await key('补充测试'); await key('\r'); await key('\r');
  client.emit({type: 'plan_revision', id: 'command-2', ...revisionReceipt()}); await pause(20);
  await key('\u0003');
  assert.deepEqual(client.cancellations, ['command-2']);
  const newer = {...target, plan_version: 2, plan_hash: 'd'.repeat(64)};
  client.emit({type: 'plan_revision', id: 'command-2', ...revisionReceipt({outcome: 'submitted', result_target: newer,
    task: {task_id: 't-revision', status: 'completed', segment: 1, run_id: 'command-2', run_started: false}})});
  client.emit({type: 'done', id: 'command-2', result: {text: '修订已提交', status: 'done', steps: 1, usage: {}, cost_usd: 0, run_id: 'command-2', stop_reason: 'plan_submitted'}});
  await pause(20);
  assert.deepEqual(client.commands.slice(2), [{command: 'plan', args: {action: 'review', target: newer}}]);
  client.review('command-3', receipt({target: newer})); await pause(20);
  assert.match(lastFrame()!, /计划 v2/); assert.match(lastFrame()!, /普通草稿/);
});

test('a completed revision leaves another focused plan body and its draft in place', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('\u0012'); await key('r'); await key('\r'); await key('补充测试'); await key('\r'); await key('\r');
  client.emit({type: 'plan_revision', id: 'command-2', ...revisionReceipt()}); await pause(20);
  await enter('/plan');
  const other = {...target, plan_id: 'p-b', plan_task_id: 't-b'};
  client.review('command-3', receipt({target: other, body_page: '另一份计划的正文'})); await pause(20);
  await key('另一份草稿'); await key('\u0012'); await key('b'); await key('\r');
  const newer = {...target, plan_version: 2, plan_hash: 'd'.repeat(64)};
  client.emit({type: 'plan_revision', id: 'command-2', ...revisionReceipt({outcome: 'submitted', result_target: newer,
    task: {task_id: 't-revision', status: 'completed', segment: 1, run_id: 'command-2', run_started: false}})});
  await pause(20); client.review('command-4', receipt({target: newer, body_page: '新版正文'})); await pause(20);
  assert.match(lastFrame()!, /另一份计划的正文/); assert.match(lastFrame()!, /另一份草稿/);
  assert.doesNotMatch(lastFrame()!, /新版正文/);
  await key('a'); await key('r'); await key('\r'); await key('修改另一份'); await key('\r'); await key('\r');
  assert.equal(client.commands.length, 4);
  assert.match(lastFrame()!, /当前任务未结束/);
});

test('a tool approval interrupts feedback editing without consuming either draft', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('正在执行'); await enter('/plan'); client.review('command-1'); await pause(20);
  await key('普通草稿'); await key('\u0012'); await key('r'); await key('\r'); await key('反馈草稿');
  client.emit({type: 'approval_request', id: 'run-1', approval_id: 'write-1',
    call: {id: 'write-1', name: 'write_file', args: {path: '.vibe/test.txt'}}, preview: 'write test', decisions: ['once', 'deny']});
  await pause(20); await key('yspn');
  assert.match(lastFrame()!, /普通草稿yspn/); assert.equal(client.approvals.length, 0);
  client.emit({type: 'approval_resolved', id: 'run-1', approval_id: 'write-1'}); await pause(20);
  await key('\u0012'); await key('r'); await key('\r');
  assert.match(lastFrame()!, /反馈草稿/); assert.match(lastFrame()!, /普通草稿yspn/);
  assert.equal(client.commands.length, 1);
});

test('unknown revision results retain the source and feedback and only offer a read-only query', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('\u0012'); await key('r'); await key('\r'); await key('补充测试'); await key('\r'); await key('\r');
  client.emit({type: 'error', id: 'command-2', code: 'transport_error', message: '连接中断'}); await pause(20);
  assert.match(lastFrame()!, /修订结果待确认/); assert.match(lastFrame()!, /补充测试/);
  assert.doesNotMatch(lastFrame()!, /\[ 确认修订 \]|\[ 续做 \]|\[ 编辑 \]/);
  await key('\t'); await key('\r');
  assert.deepEqual(client.commands[2], {command: 'plan', args: {action: 'query-revision', target, feedback: '补充测试'}});
});

test('returning to the ordinary draft while a revision receipt is pending explains the blocked send', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('普通草稿'); await key('\u0012'); await key('r'); await key('\r');
  await key('补充测试'); await key('\r'); await key('\r'); await key('\u001b'); await key('\r');
  assert.match(lastFrame()!, /修订请求处理中/); assert.match(lastFrame()!, /普通草稿/);
  assert.doesNotMatch(lastFrame()!, /未发送：等待审批/);
  assert.equal(client.commands.length, 2); assert.equal(client.runs.length, 0);
});

for (const status of ['paused', 'interrupted'] as const) test(`a ${status} revision resumes only after explicit action with the observed segment`, async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('\u0012'); await key('r'); await key('\r'); await key('补充测试'); await key('\r'); await key('\r');
  client.emit({type: 'plan_revision', id: 'command-2', ...revisionReceipt({outcome: 'paused', message: '修订已暂停',
    task: {task_id: 't-revision', status, segment: 3, run_id: 'old-run', run_started: false}})}); await pause(20);
  assert.match(lastFrame()!, /续做/);
  await key('\t'); await key('\u001b[C');
  assert.equal(client.commands.length, 2);
  await key('\r'); await key('\r');
  assert.deepEqual(client.commands[2], {command: 'plan', args: {action: 'resume-revision', target, feedback: '补充测试', expected_segment: 3}});
  assert.equal(client.commands.length, 3);
});

test('a cancelled terminal revision does not offer an unsupported resume action', async () => {
  const {client, key, enter, lastFrame} = await mount(32, 12);
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('\u0012'); await key('r'); await key('\r'); await key('补充测试'); await key('\r'); await key('\r');
  client.emit({type: 'plan_revision', id: 'command-2', ...revisionReceipt({outcome: 'incomplete',
    task: {task_id: 't-revision', status: 'cancelled', segment: 1, run_id: 'old-run', run_started: false}})});
  await pause(20);
  assert.match(lastFrame()!, /查询结果/); assert.doesNotMatch(lastFrame()!, /\[ 续做 \]/);
  assert.ok(lastFrame()!.split('\n').every(line => stringWidth(line) <= 32));
});

test('oversized revision feedback is retained verbatim and cannot start a model request', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('\u0012'); await key('r'); await key('\r');
  await key('\u001b[200~' + '中'.repeat(4001) + '\u001b[201~'); await key('\r');
  assert.match(lastFrame()!, /4001\/4000/); assert.match(lastFrame()!, /1–4000/);
  assert.equal(client.commands.length, 1);
  await key('\u001b'); await key('\u0012'); await key('r'); await key('\r');
  assert.match(lastFrame()!, /4001\/4000/);
});

for (const [columns, rows] of [[32, 12], [80, 24], [120, 40]]) {
  test(`revision feedback can be confirmed by mouse at ${columns}x${rows}`, async () => {
    const {client, key, enter, lastFrame} = await mount(columns, rows);
    const click = async (label: string) => {
      const lines = lastFrame()!.split('\n'); const index = lines.findIndex(line => line.includes(label));
      assert.ok(index >= 0, lastFrame());
      const column = stringWidth(lines[index]!.slice(0, lines[index]!.indexOf(label))) + 1;
      await key(`\u001b[<0;${column};${index + 1}M\u001b[<0;${column};${index + 1}m`);
    };
    await enter('/plan'); client.review('command-1'); await pause(20);
    await key('\u0012'); await key('r'); await key('\r'); await key('补充测试');
    await click('下一步');
    assert.match(lastFrame()!, /修订 v1/);
    assert.ok(lastFrame()!.split('\n').length <= rows);
    assert.ok(lastFrame()!.split('\n').every(line => stringWidth(line) <= columns));
    await click('确认修订');
    assert.deepEqual(client.commands[1], {command: 'plan', args: {action: 'request-changes', target, feedback: '补充测试'}});
  });
}

test('a backend without revision capability has no revision action', async () => {
  const {client, key, enter, lastFrame} = await mount();
  client.capabilities = () => ['plan-review', 'tasks'];
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('\u0012'); await key('r'); await key('\r');
  assert.doesNotMatch(lastFrame()!, /修订反馈/);
  assert.equal(client.commands.length, 1);
});

test('plan queries during a run preserve the original cancellation target', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('正在执行的任务');
  await enter('/plan');
  client.review('command-1');
  await pause(20);
  assert.match(lastFrame()!, /计划 v1/);
  await key('\u0003');
  assert.deepEqual(client.cancellations, ['run-1']);
});

test('review requires selection then Enter, suppresses duplicate confirmation and follows only its new run', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('保留这段草稿');
  await key('\u0012'); await key('\r');
  assert.equal(client.commands.length, 1);
  assert.match(lastFrame()!, /先选择/);
  await key('e');
  assert.equal(client.commands.length, 1);
  assert.match(lastFrame()!, /批准并执行/);
  await key('\r'); await key('\r');
  assert.deepEqual(client.commands[1], {command: 'plan', args: {action: 'approve-execute', target}});
  assert.equal(client.commands.length, 2);
  client.review('command-2', receipt({outcome: 'launched', available_actions: [],
    launch: {task_id: 't-execution', run_id: 'command-2', segment: 1, status: 'running', run_started: true}}));
  client.emit({type: 'text_delta', id: 'command-2', delta: '执行已进入 Agent'});
  await pause(20);
  assert.match(lastFrame()!, /保留这段草稿/);
  assert.match(lastFrame()!, /执行已进入 Agent/);
  await key('\u0003');
  assert.deepEqual(client.cancellations, ['command-2']);
  assert.equal(client.runs.length, 0);
});

test('an unsolicited or replayed launch receipt never changes the active run', async () => {
  const {client, key, enter} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await enter('当前任务');
  client.review('old-command', receipt({outcome: 'launched', available_actions: [],
    launch: {task_id: 't-old', run_id: 'old-command', segment: 1, status: 'running', run_started: true}}));
  await pause(20); await key('\u0003');
  assert.deepEqual(client.cancellations, ['run-1']);
});

test('approve-only and dismiss use the displayed target and leave the draft intact', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('草稿'); await key('\u0012'); await key('a'); await key('\r');
  assert.deepEqual(client.commands[1], {command: 'plan', args: {action: 'approve', target}});
  client.review('command-2', receipt({outcome: 'approved', message: '已批准，尚未执行',
    summary: {...receipt().summary!, status: 'approved', executable: true}, available_actions: ['approve-execute', 'dismiss']}));
  await pause(20); await key('d'); await key('\r');
  assert.deepEqual(client.commands[2], {command: 'plan', args: {action: 'dismiss', target}});
  client.review('command-3', receipt({outcome: 'dismissed', message: '已关闭审阅'}));
  await pause(20);
  assert.match(lastFrame()!, /草稿/);
  assert.equal(client.runs.length, 0);
});

test('body pages and copies stay on the reviewed target and keep the draft', async () => {
  const copies: string[] = [];
  const {client, key, enter, lastFrame} = await mount(80, 24, text => {copies.push(text); return 'test';});
  await enter('/plan'); client.review('command-1', receipt({body_page: '第一页中文😀', next_cursor: 7})); await pause(20);
  await key('草稿中文'); await key('\u0012'); await key('b'); await key('\r');
  assert.match(lastFrame()!, /第一页中文/);
  await key('\u0019');
  assert.deepEqual(copies, ['第一页中文😀']);
  await key('\u001b[C');
  assert.deepEqual(client.commands[1], {command: 'plan', args: {action: 'review', target, cursor: 7}});
  client.review('command-2', receipt({body_page: '第二页：完整末尾', next_cursor: null})); await pause(20);
  assert.match(lastFrame()!, /第二页：完整末尾/);
  await key('\u0019');
  assert.equal(copies[1], '第二页：完整末尾');
  await key('\u001b[D');
  assert.deepEqual(client.commands[2], {command: 'plan', args: {action: 'review', target, cursor: 0}});
  client.review('command-3', receipt({body_page: '第一页中文😀', next_cursor: 7})); await pause(20);
  await key('\u001b'); await key('继续');
  assert.match(lastFrame()!, /草稿中文继续/);
});

test('tool approval interrupts plan review and clears its hidden selection', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await enter('工作任务'); await key('草稿'); await key('\u0012'); await key('e');
  client.emit({type: 'approval_request', id: 'run-1', approval_id: 'gate-1', call: {id: 'call-1', name: 'write_file', args: {}}, preview: '写入文件', decisions: ['once', 'deny']});
  await pause(20); await key('a');
  assert.match(lastFrame()!, /草稿a/);
  await key('\u0012'); await key('n'); await key('\r');
  assert.deepEqual(client.approvals, ['gate-1']);
  await key('\u0012'); await key('\r');
  assert.match(lastFrame()!, /先选择/);
  assert.equal(client.commands.length, 1);
});

for (const [columns, rows] of [[32, 12], [80, 24], [120, 40]]) {
  test(`review keeps version, selection and escape visible at ${columns}x${rows}`, async () => {
    const {client, key, enter, lastFrame} = await mount(columns, rows);
    await enter('/plan'); client.review('command-1', receipt({summary: {...receipt().summary!, objective: '超长中文标题'.repeat(50)}})); await pause(20);
    await key('\u0012'); await key('e');
    const frame = lastFrame()!;
    assert.match(frame, /计划 v1/); assert.match(frame, /批准并执行/);
    assert.match(frame, /Enter确认/); assert.match(frame, /Esc草稿/);
    assert.ok(frame.split('\n').every(line => stringWidth(line) <= columns));
    assert.ok(frame.split('\n').length <= rows);
    assert.equal(client.commands.length, 1);
  });
}

test('submitted plan discovery never interrupts the next draft', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('请生成计划'); await key('下一轮草稿');
  client.emit({type: 'done', id: 'run-1', result: {text: '计划已提交', status: 'done', steps: 1, usage: {}, cost_usd: 0, run_id: 'run-1', stop_reason: 'plan_submitted'}});
  await pause(20);
  assert.deepEqual(client.commands, [{command: 'plan', args: {action: 'review'}}]);
  client.review('command-1'); await pause(20); await key('ad');
  assert.match(lastFrame()!, /下一轮草稿ad/);
  assert.equal(client.commands.length, 1);
});

test('a failed decision requires a query and never falls back to legacy execution', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('仍然保留'); await key('\u0012'); await key('e'); await key('\r');
  client.emit({type: 'error', id: 'command-2', code: 'connection_error', message: '响应中断'});
  await pause(20);
  assert.match(lastFrame()!, /结果待确认/);
  await key('e'); await key('\r');
  assert.equal(client.commands.length, 2);
  await key('q'); await key('\r');
  assert.deepEqual(client.commands[2], {command: 'plan', args: {action: 'review', target, cursor: 0}});
  client.review('command-3', receipt({outcome: 'launched', available_actions: [],
    launch: {task_id: 't-already', run_id: 'command-2', segment: 1, status: 'interrupted', run_started: false}}));
  await pause(20);
  assert.match(lastFrame()!, /t-already/);
  assert.doesNotMatch(lastFrame()!, /批准并执行|重试启动/);
  await key('\u001b');
  assert.match(lastFrame()!, /仍然保留/);
  assert.equal(client.runs.length, 0);
});

test('new discovery cannot replace a plan while its action is selected', async () => {
  const {client, key, enter} = await mount();
  await enter('/plan'); await enter('/plan');
  client.review('command-1'); await pause(20);
  await key('\u0012'); await key('e');
  const other = {...target, plan_id: 'p-b', plan_hash: 'b'.repeat(64)};
  client.review('command-2', receipt({target: other})); await pause(20);
  await key('\r');
  assert.deepEqual(client.commands[2], {command: 'plan', args: {action: 'approve-execute', target}});
});

test('superseded page keeps the original body and requires explicit discovery of its replacement', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1', receipt({body_page: '原版第一页', next_cursor: 6})); await pause(20);
  await key('\u0012'); await key('b'); await key('\r'); await key('\u001b[C');
  const replacement = {...target, plan_version: 2, plan_hash: 'b'.repeat(64)};
  client.review('command-2', receipt({outcome: 'unavailable', code: 'superseded', message: '已有新版，请重新审阅', replacement_target: replacement, available_actions: [], body_page: ''}));
  await pause(20); assert.match(lastFrame()!, /原版第一页/);
  await key('a'); await key('e'); await key('\r');
  assert.equal(client.commands.length, 2);
  await key('v'); await key('\r');
  assert.deepEqual(client.commands[2], {command: 'plan', args: {action: 'review', target: replacement}});
});

test('missing decision receipt becomes unknown and permits only an exact query', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('\u0012'); await key('e'); await key('\r');
  await pause(15_050);
  assert.match(lastFrame()!, /结果待确认/);
  await key('e'); await key('\r');
  assert.equal(client.commands.length, 2);
  await key('q'); await key('\r');
  assert.deepEqual(client.commands[2], {command: 'plan', args: {action: 'review', target, cursor: 0}});
  client.review('command-3', receipt({outcome: 'approved_not_started', message: '已批准，尚未启动',
    launch: {task_id: 't-reserved', run_id: null, segment: 0, status: 'created', run_started: false}, available_actions: ['approve-execute', 'dismiss']}));
  await pause(20);
  assert.match(lastFrame()!, /继续执行此计划/);
  assert.equal(client.runs.length, 0);
});

test('query receipts preserve history position and body copy preserves its scroll position', async () => {
  const {client, key, enter, lastFrame} = await mount(80, 24, () => 'test');
  await enter('阅读任务');
  client.emit({type: 'done', id: 'run-1', result: {text: Array.from({length: 40}, (_, i) => `历史行 ${i}`).join('\n'), status: 'done', steps: 1, usage: {}, cost_usd: 0, run_id: 'run-1', stop_reason: ''}});
  await pause(20); await key('\u001b[5~');
  const before = lastFrame()!.split('\n').filter(line => line.includes('历史行')).map(line => line.match(/历史行 \d+/)![0]);
  assert.ok(before.length);
  await enter('/plan');
  client.review('command-1', receipt({body_page: Array.from({length: 40}, (_, i) => `正文行 ${i}`).join('\n')})); await pause(20);
  assert.deepEqual(lastFrame()!.split('\n').filter(line => line.includes('历史行')).map(line => line.match(/历史行 \d+/)![0]), before);
  await key('\u0012'); await key('b'); await key('\r'); await key('\u001b[6~');
  const body = lastFrame()!.split('\n').filter(line => line.includes('正文行')).map(line => line.match(/正文行 \d+/)![0]);
  assert.ok(body.length); await key('\u0019');
  assert.deepEqual(lastFrame()!.split('\n').filter(line => line.includes('正文行')).map(line => line.match(/正文行 \d+/)![0]), body);
});

test('disconnect during a decision preserves the draft and cannot accept a late launch', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('断线草稿'); await key('\u0012'); await key('e'); await key('\r');
  client.emit({type: 'closed', code: 1, signal: null}); await pause(20);
  client.review('command-2', receipt({outcome: 'launched', available_actions: [],
    launch: {task_id: 't-late', run_id: 'command-2', segment: 1, status: 'running', run_started: true}}));
  await pause(20);
  assert.match(lastFrame()!, /断线草稿/);
  assert.match(lastFrame()!, /结果待确认/);
  assert.doesNotMatch(lastFrame()!, /t-late/);
  assert.equal(client.runs.length, 0);
});

test('a launched card follows its task status without leaving a stale running receipt', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('\u0012'); await key('e'); await key('\r');
  client.review('command-2', receipt({outcome: 'launched', message: '已建立计划运行', available_actions: [],
    launch: {task_id: 't-execution', run_id: 'command-2', segment: 1, status: 'running', run_started: true}}));
  client.emit({type: 'task_state', id: 'command-2', task: {task_id: 't-execution', status: 'paused'}});
  client.emit({type: 'done', id: 'command-2', result: {text: '', status: 'stopped', steps: 1, usage: {}, cost_usd: 0, run_id: 'command-2', stop_reason: 'plan_incomplete'}});
  await pause(20); await key('\u0012');
  assert.match(lastFrame()!, /t-execution · paused/);
  assert.doesNotMatch(lastFrame()!, /已建立计划运行|t-execution · running/);
});

test('long plan objectives keep the binding and result visible on the actions screen', async () => {
  const {client, key, enter, lastFrame} = await mount(80, 24);
  await enter('/plan');
  client.review('command-1', receipt({outcome: 'launched', available_actions: [], message: '已有运行，未重复启动',
    summary: {...receipt().summary!, objective: '非常长的目标和基线事实。'.repeat(100)},
    launch: {task_id: 't-original', run_id: 'r-original', segment: 1, status: 'paused', run_started: false}}));
  await pause(20); await key('\u0012');
  assert.match(lastFrame()!, /t-original · paused/);
  assert.match(lastFrame()!, /已有运行，未重复启动/);
  assert.match(lastFrame()!, /查看正文/);
});

test('opening and leaving the plan body clears obsolete confirmation feedback', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan'); client.review('command-1'); await pause(20);
  await key('\u0012'); await key('b'); await key('\r');
  assert.doesNotMatch(lastFrame()!, /已选择：查看正文/);
  await key('a'); await key('e'); await key('\u001b'); await key('\u0012');
  assert.doesNotMatch(lastFrame()!, /已选择：批准并执行/);
  await key('\r');
  assert.equal(client.commands.length, 1);
  assert.match(lastFrame()!, /先选择/);
});

test('a resumed plan task updates its card without taking over the active run', async () => {
  const {client, key, enter, lastFrame} = await mount();
  await enter('/plan');
  client.review('command-1', receipt({outcome: 'launched', available_actions: [],
    launch: {task_id: 't-original', run_id: 'r-original', segment: 1, status: 'paused', run_started: false}}));
  await pause(20); await enter('当前输入任务');
  client.emit({type: 'task_state', id: 'r-resume', task: {task_id: 't-original', status: 'completed', segment: 2}});
  client.emit({type: 'task_state', id: 'r-original', task: {task_id: 't-original', status: 'paused', segment: 1}});
  await pause(20); await key('\u0012');
  assert.match(lastFrame()!, /t-original · completed/);
  await key('\u0003');
  assert.deepEqual(client.cancellations, ['run-1']);
});
