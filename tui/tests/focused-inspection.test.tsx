import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import {mkdtempSync, realpathSync, rmSync, statSync, writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import path from 'node:path';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {App} from '../src/app.js';
import type {BackendClient} from '../src/backend.js';
import type {ServerEvent} from '../src/protocol.js';
import {initialState} from '../src/view-state.js';

afterEach(cleanup);
async function until(view: ReturnType<typeof render>, text: string) {
  const end = Date.now() + 4000;
  while (Date.now() < end) {if (view.lastFrame()?.includes(text)) return; await pause(10);}
  assert.fail(`missing ${text}\n${view.lastFrame()}`);
}
async function click(view: ReturnType<typeof render>, label: string, settle = 20) {
  await until(view, label); await pause(20);
  const lines = view.lastFrame()!.split('\n'); const row = lines.findIndex(line => line.includes(label));
  const col = stringWidth(lines[row]!.slice(0, lines[row]!.indexOf(label))) + 1;
  view.stdin.write(`\x1b[<0;${col};${row + 1}M\x1b[<0;${col};${row + 1}m`); await pause(settle);
}
function fixture() {
  const root = realpathSync(mkdtempSync(path.join(tmpdir(), 'focus-inspection-')));
  execFileSync('git', ['init', '-q', root]); writeFileSync(path.join(root, 'hello.txt'), 'FOCUS_ONLY\n');
  const info = statSync(root, {bigint: true});
  const listeners = new Set<(event: ServerEvent) => void>(); const runs: string[] = []; const cancellations: string[] = [];
  const commands: {command: string; args: unknown}[] = [];
  const client = {subscribe: (fn: (event: ServerEvent) => void) => {listeners.add(fn); return () => {listeners.delete(fn);};},
    capabilities: () => ['plan-review'], shutdown: async () => {}, run: (text: string) => {runs.push(text); return `run-${runs.length}`;},
    command: (command: string, args: unknown) => {commands.push({command, args}); return `query-${commands.length}`;},
    cancel: (id: string) => {cancellations.push(id); return 'cancel-1';}} as unknown as BackendClient;
  const emit = (event: ServerEvent) => {for (const listener of listeners) listener(event);};
  const ready = () => {emit({v: 1, type: 'ready', id: null, workspace: root, model: 'fake', session_id: 'focus-session', capabilities: [],
    file_source: {root, identity: `${info.dev}:${info.ino}`}}); emit({v: 1, type: 'status', id: null, phase: 'idle'});};
  return {root, client, emit, ready, runs, cancellations, commands};
}

test('focused inspection reads the same files and returns to the existing Chinese draft cursor', async () => {
  const f = fixture(); let launches = 0;
  const view = render(<App client={f.client} windowSize={{columns: 80, rows: 24}}
    launchSidebar={() => {launches++; return {outcome: 'started', message: '侧栏已打开'};}}/>);
  try {
    await pause(25); f.ready(); await until(view, '就绪');
    view.stdin.write('中文草稿'); await pause(20); view.stdin.write('\x1b[D'); await pause(20);
    await click(view, '专注检查'); await until(view, '+FOCUS_ONLY');
    assert.equal(launches, 0); assert.deepEqual(f.runs, []);
    view.stdin.write('q'); await until(view, '中文草稿');
    view.stdin.write('\x1b[200~粘\x1b[201~'); await until(view, '中文草粘稿');
    view.stdin.write('中'); await until(view, '中文草粘中稿');
    await click(view, '专注检查'); await until(view, '+FOCUS_ONLY');
    await click(view, '返回草稿'); await until(view, '中文草粘中稿');
    assert.deepEqual(f.runs, []);
  } finally {view.unmount(); rmSync(f.root, {recursive: true, force: true});}
});

test('focused plan reading keeps the exact version and retries a failed page without starting execution', async () => {
  const f = fixture();
  const target = {session_id: 'focus-session', plan_task_id: 't-20261109-010000-abcdef', plan_id: 'plan-focus', plan_version: 2, plan_hash: 'b'.repeat(64)};
  const state = {...initialState, workspace: f.root, sessionId: 'focus-session', phase: 'idle' as const,
    inspectionPlan: {target, relation: 'review' as const, objective: '精确计划', status: 'proposed', stepCount: 1, problem: null}};
  const view = render(<App client={f.client} initialState={state} windowSize={{columns: 80, rows: 24}}/>);
  const waitCommands = async (count: number) => {
    const end = Date.now() + 3000; while (f.commands.length < count && Date.now() < end) await pause(10);
    assert.equal(f.commands.length, count, view.lastFrame());
  };
  const reply = (id: string, body: string, next: number | null) => f.emit({v: 1, type: 'plan_review', id, outcome: 'ready', target,
    replacement_target: null, code: null, message: '', launch: null, body_page: body, next_cursor: next, available_actions: [],
    summary: {objective: '精确计划', status: 'proposed', executable: true, step_count: 1, criterion_count: 0, question_count: 0}});
  try {
    await pause(30); await click(view, '专注检查'); await click(view, '计划'); await waitCommands(1);
    assert.deepEqual(f.commands[0], {command: 'plan', args: {action: 'review', target, cursor: 0}});
    const first = '第一页中文计划'; reply('query-1', first, [...first].length); await until(view, first);
    await click(view, '下页'); await waitCommands(2);
    f.emit({v: 1, type: 'error', id: 'query-2', code: 'test_read_failure', message: '模拟正文读取失败'});
    await until(view, '模拟正文读取失败'); assert.match(view.lastFrame()!, /第一页中文计划/);
    await click(view, '重试'); await waitCommands(3);
    assert.deepEqual(f.commands[2], {command: 'plan', args: {action: 'review', target, cursor: [...first].length}});
    reply('query-3', '第二页中文计划', null); await until(view, '第二页中文计划');
    view.stdin.write('\x1b'); await until(view, '[ 专注检查 ]'); view.stdin.write('q'); await pause(20);
    await click(view, '专注检查'); await until(view, '第二页中文计划');
    assert.equal(f.commands.length, 3); assert.deepEqual(f.runs, []);
  } finally {view.unmount(); rmSync(f.root, {recursive: true, force: true});}
});

test('narrow focused inspection exposes a new approval without authorizing it', async () => {
  const f = fixture();
  const view = render(<App client={f.client} windowSize={{columns: 32, rows: 12}}/>);
  try {
    await pause(25); f.ready(); await until(view, '就绪');
    view.stdin.write('运行任务'); await pause(20); view.stdin.write('\r'); await pause(20);
    view.stdin.write('中文草稿'); await pause(20); view.stdin.write('\x1b[D'); await pause(20);
    await click(view, '[ 检查 ]'); await until(view, '专注检查');
    f.emit({v: 1, type: 'approval_request', id: 'run-1', approval_id: 'focus-approval',
      call: {id: 'write', name: 'write_file', args: {path: 'a.txt'}}, preview: '写入 a.txt', decisions: ['once', 'deny']});
    await until(view, '待审批');
    view.stdin.write('y'); await pause(20); assert.equal(f.commands.length, 0);
    assert.match(view.lastFrame()!, /专注检查/);
    assert.equal(view.lastFrame()!.split('\n').length, 12);
    for (const line of view.lastFrame()!.split('\n')) assert.ok(stringWidth(line) <= 32, line);
    await click(view, '返回草稿'); await until(view, '[ 允许本次 ]');
    view.stdin.write('中'); await until(view, '中文草中稿');
    assert.equal(f.runs.length, 1); assert.equal(f.commands.length, 0);
  } finally {view.unmount(); rmSync(f.root, {recursive: true, force: true});}
});

test('a focused diff selection appends to the preserved draft once and keeps its source', async () => {
  const f = fixture();
  const view = render(<App client={f.client} windowSize={{columns: 80, rows: 24}}/>);
  try {
    await pause(25); f.ready(); await until(view, '就绪');
    view.stdin.write('中文草稿'); await pause(20); view.stdin.write('\x1b[D'); await pause(20);
    await click(view, '专注检查'); await until(view, '+FOCUS_ONLY');
    const lines = view.lastFrame()!.split('\n'); const row = lines.findIndex(line => line.includes('+FOCUS_ONLY')) + 1;
    const col = stringWidth(lines[row - 1]!.split('+FOCUS_ONLY')[0]!) + 1;
    view.stdin.write(`\x1b[<0;${col};${row}M\x1b[<32;${col + 11};${row}M\x1b[<0;${col + 11};${row}m`);
    await click(view, '[ 附回 ]'); await until(view, '已附到主区草稿');
    await click(view, '[ 附回 ]'); await until(view, '已附到主区草稿');
    await click(view, '返回草稿'); view.stdin.write('中'); await until(view, '中文草中稿');
    assert.equal(f.runs.length, 0);
    view.stdin.write('\r'); await pause(30);
    assert.equal(f.runs.length, 1); assert.match(f.runs[0]!, /FOCUS_ONLY/); assert.match(f.runs[0]!, /hello.txt/);
    assert.ok(f.runs[0]!.includes(f.root)); assert.equal(f.runs[0]!.split('[侧栏引用').length - 1, 1);
  } finally {view.unmount(); rmSync(f.root, {recursive: true, force: true});}
});

test('an earlier result opens inside unsupported hosts and keeps the current cancellation target', async () => {
  const f = fixture();
  const view = render(<App client={f.client} windowSize={{columns: 100, rows: 40}}
    launchSidebar={() => ({outcome: 'unsupported', message: '此终端无法自动分屏'})}/>);
  try {
    await pause(25); f.ready(); await until(view, '就绪');
    view.stdin.write('旧任务'); await pause(20); view.stdin.write('\r'); await pause(20);
    f.emit({v: 1, type: 'tool_result', id: 'run-1', call: {id: 'check', name: 'bash', args: {command: 'old-check', verification: true}},
      ok: true, output: Array.from({length: 40}, (_, i) => `OLD_PROOF_${i}`).join('\n'), execution: {status: 'failed', exit_code: 2}});
    f.emit({v: 1, type: 'done', id: 'run-1', result: {text: '旧检查失败', status: 'done', steps: 1, usage: {}, cost_usd: 0, run_id: 'trace-old', stop_reason: '', verified: false}});
    await until(view, '旧检查失败'); view.stdin.write('新任务'); await pause(20); view.stdin.write('\r'); await pause(20);
    view.stdin.write('中文草稿'); await pause(20); view.stdin.write('\x1b[D'); await pause(20);
    await click(view, '[ 验证 ]'); await until(view, '验证记录');
    view.stdin.write('G'); await until(view, 'OLD_PROOF_39');
    assert.match(view.lastFrame()!, /结果快照/);
    const anchor = view.lastFrame()!.match(/OLD_PROOF_\d+/)![0];
    await click(view, '返回草稿', 0); await until(view, '中文草稿');
    view.stdin.write('g'); await until(view, '中文草g稿');
    await click(view, '专注检查'); await until(view, anchor);
    assert.doesNotMatch(view.lastFrame()!, /OLD_PROOF_0\b/);
    view.stdin.write('\x03'); await until(view, '正在停止');
    assert.deepEqual(f.cancellations, ['run-2']); assert.equal(f.runs.length, 2);
    assert.match(view.lastFrame()!, /中文草g稿/);
  } finally {view.unmount(); rmSync(f.root, {recursive: true, force: true});}
});
