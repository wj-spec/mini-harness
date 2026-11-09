import assert from 'node:assert/strict';
import test, {afterEach} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {SidebarApp} from '../src/sidebar/app.js';
import type {ChangedFile, DiffResult, StatusResult} from '../src/sidebar/git.js';
import {createSidebarOwner} from '../src/sidebar/channel.js';
import {writeSidebarSignal, removeSidebarSignal} from '../src/sidebar/signal.js';
import {randomUUID} from 'node:crypto';
import type {SidebarSignal} from '../src/sidebar/signal.js';

test('verification reading shows scoped failure facts and keeps the old run until explicitly refreshed', async () => {
  const workspace = `/tmp/sidebar-verification-${randomUUID()}`;
  const owner = createSidebarOwner();
  const input = {owner, sessionId: 's-one', signature: 'first', files: [], active: false,
    run: {requestId: 'request-1', traceRunId: 'trace-1', taskId: 'task-1', segment: 1, taskStatus: 'paused', status: 'finished' as const, objective: '验证中文标题'},
    verification: {requestId: 'request-1', status: 'stopped' as const, stopReason: 'verification_failed', verified: false,
      completionAllowed: false, completionMessages: ['命令未通过'], omittedCommands: 0,
      commands: [{id: 'c1', command: 'npm test', verification: true, pending: false, ok: true,
        execution: {status: 'failed' as const, exit_code: 2}, output: 'ALL TESTS PASSED', truncated: false}]}};
  writeSidebarSignal(workspace, input);
  const view = render(<SidebarApp workspace={workspace} owner={owner} refreshMs={0} signalPollMs={10}
    size={{columns: 60, rows: 22}} readStatus={() => status} readDiff={() => diff}/>);
  try {
    await waitForFrame(view.lastFrame, /已结束/); view.stdin.write('v');
    assert.match(await waitForFrame(view.lastFrame, /验证未通过/), /exit=2/);
    assert.match(view.lastFrame() ?? '', /npm test/);
    assert.match(view.lastFrame() ?? '', /ALL TESTS PASSED/);
    writeSidebarSignal(workspace, {...input, signature: 'second', run: {...input.run, requestId: 'request-2', objective: '新的任务'},
      verification: {...input.verification, requestId: 'request-2', status: null, verified: null, stopReason: '', completionAllowed: null, completionMessages: [], commands: []}});
    assert.match(await waitForFrame(view.lastFrame, /有更新/), /验证未通过/);
    assert.doesNotMatch(view.lastFrame() ?? '', /新的任务/);
    view.stdin.write('r');
    assert.match(await waitForFrame(view.lastFrame, /新的任务/), /验证结果未知/);
    assert.doesNotMatch(view.lastFrame() ?? '', /npm test|exit=2/);
    view.stdin.write('\u001b'); assert.match(await waitForFrame(view.lastFrame, /工作区 vs HEAD/), /工作区 vs HEAD/);
  } finally {view.unmount(); removeSidebarSignal(workspace, owner);}
});

test('reading a plan pins its snapshot until the user accepts an update or returns to changes', async () => {
  const owner = createSidebarOwner();
  let signal: SidebarSignal = {scope: {workspace: '/repo', owner, sessionId: 'session'}, revision: 1,
    version: '1', files: [], active: false, updatedAt: 1,
    plan: {version: 1, status: 'proposed', stale: false, objective: '保留正在阅读的计划', stepCount: 1, doneSteps: null}};
  const {lastFrame, stdin} = render(<SidebarApp workspace="/repo" owner={owner} refreshMs={0} signalPollMs={10}
    size={{columns: 60, rows: 18}} readStatus={() => status} readDiff={() => diff} readSignal={() => signal}/>);
  await waitForFrame(lastFrame, /计划 v1/);
  stdin.write('p'); await pause(30);
  signal = {...signal, revision: 2, version: '2', plan: {...signal.plan!, version: 2, objective: '这是新计划'}};
  assert.match(await waitForFrame(lastFrame, /有更新/), /保留正在阅读的计划/);
  assert.doesNotMatch(lastFrame() ?? '', /这是新计划/);
  stdin.write('r');
  assert.match(await waitForFrame(lastFrame, /这是新计划/), /计划 v2/);
  signal = {...signal, revision: 3, version: '3', plan: undefined};
  assert.match(await waitForFrame(lastFrame, /当前无计划/), /这是新计划/);
  stdin.write('r'); await pause(30);
  assert.match(lastFrame() ?? '', /这是新计划/);
  stdin.write('\u001b');
  assert.match(await waitForFrame(lastFrame, /工作区 vs HEAD/), /工作区 vs HEAD/);
});

test('the same plan on a resumed segment still prompts before replacing the reading scope', async () => {
  const owner = createSidebarOwner();
  let signal: SidebarSignal = {scope: {workspace: '/repo', owner, sessionId: 'session'}, revision: 1, version: '1', files: [], active: false, updatedAt: 1,
    run: {requestId: 'first', traceRunId: null, taskId: 'task', segment: 1, taskStatus: 'paused', status: 'finished', objective: '原目标'},
    plan: {version: 1, status: 'approved', stale: false, objective: '同一计划', stepCount: 1, doneSteps: null}};
  const {lastFrame, stdin} = render(<SidebarApp workspace="/repo" owner={owner} refreshMs={0} signalPollMs={10}
    size={{columns: 60, rows: 18}} readStatus={() => status} readDiff={() => diff} readSignal={() => signal}/>);
  await waitForFrame(lastFrame, /已结束/); stdin.write('p'); await pause(25);
  signal = {...signal, revision: 2, version: '2', run: {...signal.run!, requestId: 'second', segment: 2, status: 'running'}};
  assert.match(await waitForFrame(lastFrame, /有更新/), /段 1/);
  stdin.write('r'); assert.match(await waitForFrame(lastFrame, /段 2/), /段 2/);
});

test('verification reading wraps Chinese output and preserves its source line when resized', async () => {
  const owner = createSidebarOwner();
  const signal: SidebarSignal = {scope: {workspace: '/repo', owner, sessionId: 'session'}, revision: 1, version: 'v1', files: [], active: false, updatedAt: 1,
    run: {requestId: 'req', traceRunId: 'trace', taskId: null, segment: null, taskStatus: null, status: 'finished', objective: '长输出'},
    verification: {requestId: 'req', status: 'stopped', stopReason: '', verified: null, completionAllowed: null, completionMessages: [], omittedCommands: 0,
      commands: [{id: 'c', command: 'test', verification: false, pending: false, ok: true, execution: null,
        output: Array.from({length: 40}, (_, i) => `ROW${String(i).padStart(2, '0')} 中文输出一二三四五六七八九十`).join('\n'), truncated: true}]}};
  const props = {workspace: '/repo', owner, refreshMs: 0, signalPollMs: 10, readStatus: () => status, readDiff: () => diff, readSignal: () => signal};
  const {lastFrame, stdin, rerender} = render(<SidebarApp {...props} size={{columns: 60, rows: 18}}/>);
  await waitForFrame(lastFrame, /已结束/); stdin.write('v'); await waitForFrame(lastFrame, /验证记录/);
  stdin.write('\u001b[6~'); await pause(25);
  const anchor = (lastFrame() ?? '').match(/ROW\d+/)?.[0]; assert.ok(anchor);
  rerender(<SidebarApp {...props} size={{columns: 32, rows: 12}}/>); await pause(30);
  assert.match(lastFrame() ?? '', new RegExp(anchor));
  assert.ok((lastFrame() ?? '').split('\n').length <= 12);
  assert.ok((lastFrame() ?? '').split('\n').every(line => stringWidth(line) <= 32));
  stdin.write('G'); assert.match(await waitForFrame(lastFrame, /输出已截断/), /输出已截断/);
  stdin.write('g'); assert.match(await waitForFrame(lastFrame, /验证结果未知/), /验证结果未知/);
});

test('a bound sidebar follows only its workbench while an unbound sidebar stays workspace-only', async () => {
  const workspace = `/tmp/sidebar-view-${randomUUID()}`;
  const a = createSidebarOwner(); const b = createSidebarOwner();
  writeSidebarSignal(workspace, {owner: a, sessionId: 'a', signature: 'a1', files: [files[0]!.path], active: true});
  writeSidebarSignal(workspace, {owner: b, sessionId: 'b', signature: 'b1', files: ['new.ts'], active: true});
  writeSidebarSignal(workspace, {signature: 'old', files: ['new.ts'], active: true});
  const props = {workspace, refreshMs: 0, signalPollMs: 10, size: {columns: 60, rows: 18}, readStatus: () => status, readDiff: () => diff};
  const boundProps = {...props, owner: a};
  const bound = render(<SidebarApp {...boundProps}/>);
  const unbound = render(<SidebarApp {...props}/>);
  try {
    assert.match(await waitForFrame(bound.lastFrame, /●M tui/), /●M tui/);
    assert.doesNotMatch(bound.lastFrame() ?? '', /●A new/);
    await pause(30);
    assert.doesNotMatch(unbound.lastFrame() ?? '', /●|本轮触及/);
    assert.match(unbound.lastFrame() ?? '', /未绑定主会话/);
  } finally {
    bound.unmount(); unbound.unmount();
    removeSidebarSignal(workspace, a); removeSidebarSignal(workspace, b); removeSidebarSignal(workspace);
  }
});

afterEach(cleanup);

test('scoped sidebar identifies the selected run and shows an exact plan without inventing step progress', async () => {
  const owner = createSidebarOwner();
  const signal = {scope: {workspace: '/repo', owner, sessionId: 'session'}, revision: 1, version: 'v1', files: ['new.ts'], active: false, updatedAt: 1,
    run: {requestId: 'req-2', traceRunId: 'trace-9', taskId: 't-exec', segment: 2, taskStatus: 'completed', status: 'finished' as const, objective: '更新中文标题'},
    plan: {target: {session_id: 'session', plan_task_id: 't-plan', plan_id: 'p-exact', plan_version: 3, plan_hash: 'a'.repeat(64)},
      relation: 'execution' as const, problem: null, version: 3, status: 'approved', stale: false, objective: '更新中文标题', stepCount: 2, doneSteps: null, acceptance: true}};
  const {lastFrame, stdin} = render(<SidebarApp workspace="/repo" owner={owner} refreshMs={0} signalPollMs={10}
    size={{columns: 60, rows: 18}} readStatus={() => status} readDiff={() => diff} readSignal={() => signal}/>);
  await waitForFrame(lastFrame, /已结束/);
  assert.match(lastFrame() ?? '', /已结束.*段 2.*更新中文标题/);
  stdin.write('p'); await pause(30);
  assert.match(lastFrame() ?? '', /更新中文标题/);
  assert.match(lastFrame() ?? '', /p-exact/);
  assert.match(lastFrame() ?? '', /aaaaaaaa/);
  assert.match(lastFrame() ?? '', /进度未知/);
  assert.doesNotMatch(lastFrame() ?? '', /null\/2|0\/2/);
});

test('a bound sidebar ignores a late revision and an update from another owner', async () => {
  const owner = createSidebarOwner();
  let signal = {scope: {workspace: '/repo', owner, sessionId: 'session'}, revision: 2, version: 'v2', files: ['new.ts'], active: true, updatedAt: 1};
  const {lastFrame} = render(<SidebarApp workspace="/repo" owner={owner} refreshMs={0} signalPollMs={10}
    size={{columns: 60, rows: 18}} readStatus={() => status} readDiff={(_workspace, file) => ({ok: true, text: `+${file.path}`, truncated: false})}
    readSignal={() => signal}/>);
  await waitForFrame(lastFrame, /\+new.ts/);
  signal = {...signal, revision: 1, files: [files[0]!.path]};
  await pause(40);
  assert.match(lastFrame() ?? '', /\+new.ts/);
  signal = {...signal, revision: 3, scope: {...signal.scope, owner: createSidebarOwner()}};
  await pause(40);
  assert.match(lastFrame() ?? '', /\+new.ts/);
});

const files: ChangedFile[] = [
  {path: 'tui/src/app.tsx', status: 'modified', index: '.', worktree: 'M', additions: 4, deletions: 2, staged: false},
  {path: 'new.ts', status: 'added', index: 'A', worktree: '.', additions: 6, deletions: 0, staged: true},
];

const status: StatusResult = {ok: true, status: {root: '/repo', branch: 'main', head: 'abc1234', ahead: 0, behind: 0, files}};
const diffText = ['diff --git a/tui/src/app.tsx b/tui/src/app.tsx', '@@ -1,2 +1,3 @@', ' context', '-old line', '+added line'].join('\n');
const diff: DiffResult = {ok: true, text: diffText, truncated: false};

async function waitForFrame(
  lastFrame: () => string | undefined, pattern: RegExp, timeoutMs = 2000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  let frame = '';
  while (Date.now() < deadline) {
    frame = lastFrame() ?? '';
    if (pattern.test(frame)) return frame;
    await pause(10);
  }
  return frame;
}

function mount(overrides: {
  statusResult?: StatusResult;
  diffResult?: DiffResult;
  columns?: number;
  rows?: number;
} = {}) {
  let quits = 0;
  const view = render(<SidebarApp workspace="/repo" theme="dark" refreshMs={0} size={{columns: overrides.columns ?? 60, rows: overrides.rows ?? 18}}
    readStatus={() => overrides.statusResult ?? status}
    readDiff={() => overrides.diffResult ?? diff}
    onQuit={() => {quits += 1;}}/>);
  return {...view, quits: () => quits};
}

test('sidebar renders branch, changed files, follow mode and the selected diff', async () => {
  const {lastFrame} = mount();
  const frame = await waitForFrame(lastFrame, /\+added line/u);
  assert.match(frame, /main/u);
  assert.match(frame, /tui\/src\/app\.tsx/u);
  assert.match(frame, /new\.ts/u);
  assert.match(frame, /跟随/u);
  assert.match(frame, /\+added line/u);
  assert.match(frame, /-old line/u);
  assert.match(frame, /修改 · tui\/src\/app\.tsx/u);
});

test('sidebar keeps every rendered row within the terminal width', async () => {
  for (const [columns, rows] of [[40, 14], [60, 18], [80, 24]] as const) {
    const {lastFrame} = mount({columns, rows});
    await pause(30);
    const lines = (lastFrame() ?? '').split('\n');
    assert.ok(lines.length <= rows, `rendered ${lines.length} rows for ${rows}`);
    assert.ok(lines.every(line => stringWidth(line) <= columns), 'no horizontal overflow');
  }
});

test('keyboard selection moves to the next file and stops following', async () => {
  const {stdin, lastFrame} = mount();
  await waitForFrame(lastFrame, /\+added line/u);
  stdin.write('j');
  const frame = await waitForFrame(lastFrame, /新增 · new\.ts/u);
  assert.match(frame, /固定/u);
});

test('scrolling pins the displayed version until the user accepts an update', async () => {
  let version = 1;
  const {stdin, lastFrame} = render(<SidebarApp workspace="/repo" theme="dark" refreshMs={20} signalPollMs={0}
    size={{columns: 60, rows: 18}}
    readStatus={() => ({ok: true, status: {...status.status, files: [{...files[0]!, additions: version}]}})}
    readDiff={() => ({ok: true, text: Array.from({length: 60}, (_, i) => `+version-${version}-line-${i}`).join('\n'), truncated: false})}/>);
  await waitForFrame(lastFrame, /version-1-line-0/);
  stdin.write('\u001b[6~');
  await pause(30);
  const anchor = (lastFrame() ?? '').match(/version-1-line-(\d+)/)?.[1];
  assert.ok(anchor && Number(anchor) > 0);
  version = 2;
  await pause(100);
  assert.match(lastFrame() ?? '', new RegExp(`version-1-line-${anchor}`));
  assert.match(lastFrame() ?? '', /固定/);
  assert.match(lastFrame() ?? '', /有更新/);
  stdin.write('r');
  const updated = await waitForFrame(lastFrame, /version-2-line-/);
  assert.match(updated, new RegExp(`version-2-line-${anchor}`));
  assert.doesNotMatch(updated, /有更新/);
});

test('polling detects external edits even when file counts stay the same', async () => {
  let text = '+before';
  const {stdin, lastFrame} = render(<SidebarApp workspace="/repo" refreshMs={20} signalPollMs={0}
    size={{columns: 60, rows: 18}} readStatus={() => status}
    readDiff={() => ({ok: true, text, truncated: false})}/>);
  assert.match(await waitForFrame(lastFrame, /before/), /before/);
  stdin.write('f');
  await pause(30);
  text = '+after!';
  assert.match(await waitForFrame(lastFrame, /有更新/), /有更新/);
  assert.match(lastFrame() ?? '', /before/);
  stdin.write('r');
  assert.match(await waitForFrame(lastFrame, /after!/), /after!/);
});

test('a pinned file that disappears keeps its old version with a stale warning', async () => {
  let present = true;
  const {stdin, lastFrame} = render(<SidebarApp workspace="/repo" refreshMs={20} signalPollMs={0}
    size={{columns: 60, rows: 18}}
    readStatus={() => ({ok: true, status: {...status.status, files: present ? files : []}})} readDiff={() => diff}/>);
  await waitForFrame(lastFrame, /added line/);
  stdin.write('f');
  await pause(30);
  present = false;
  assert.match(await waitForFrame(lastFrame, /旧版/), /旧版/);
  assert.match(lastFrame() ?? '', /added line/);
  stdin.write('r');
  assert.match(await waitForFrame(lastFrame, /不再有差异/), /不再有差异/);
  assert.doesNotMatch(lastFrame() ?? '', /added line/);
});

test('refreshes coalesce and a late diff cannot replace a newly selected file', async () => {
  let slow = false;
  let finish: ((result: DiffResult) => void) | undefined;
  let active = 0;
  let maximum = 0;
  const {stdin, lastFrame, frames, unmount} = render(<SidebarApp workspace="/repo" refreshMs={10} signalPollMs={0}
    size={{columns: 60, rows: 18}} readStatus={async () => {
      active++; maximum = Math.max(maximum, active); await pause(2); active--; return status;
    }} readDiff={async (_root, file) => {
      active++; maximum = Math.max(maximum, active);
      const result = slow && file.path === files[0]!.path
        ? await new Promise<DiffResult>(resolve => {finish = resolve;})
        : {ok: true as const, text: `+contents of ${file.path}`, truncated: false};
      active--; return result;
    }}/>);
  await waitForFrame(lastFrame, /contents of tui/);
  slow = true;
  for (let i = 0; i < 100 && !finish; i++) await pause(5);
  assert.ok(finish);
  for (let i = 0; i < 10; i++) stdin.write('r');
  stdin.write('j');
  await pause(20);
  const afterSelection = frames.length;
  finish({ok: true, text: '+obsolete response', truncated: false});
  assert.match(await waitForFrame(lastFrame, /contents of new.ts/), /contents of new.ts/);
  assert.equal(maximum, 1);
  assert.ok(frames.slice(afterSelection).every(frame => !frame.includes('obsolete response')));
  unmount();
});

test('fixed reading shows Git failure without destroying the old content', async () => {
  let failing = false;
  const {stdin, lastFrame} = render(<SidebarApp workspace="/repo" refreshMs={20} signalPollMs={0}
    size={{columns: 60, rows: 18}} readStatus={() => failing ? {ok: false, reason: 'error', message: 'Git 暂时不可用'} : status}
    readDiff={() => diff}/>);
  await waitForFrame(lastFrame, /added line/);
  stdin.write('f'); await pause(30);
  failing = true;
  assert.match(await waitForFrame(lastFrame, /旧版/), /旧版/);
  assert.match(lastFrame() ?? '', /added line/);
  assert.match(lastFrame() ?? '', /Git 暂时不可用/);
});

test('narrow sidebar shows basename, focus, selection and essential actions without color', async () => {
  const path = 'very/deep/中文目录/with/a/long/name/important-file.ts';
  for (const columns of [40, 44, 60]) {
    const {lastFrame, stdin} = render(<SidebarApp workspace="/repo" refreshMs={0} signalPollMs={0}
      size={{columns, rows: 18}} readStatus={() => ({ok: true, status: {...status.status, files: [{...files[0]!, path}]}})} readDiff={() => diff}/>);
    const frame = await waitForFrame(lastFrame, /added line/);
    assert.match(frame, /›.*important-file\.ts/);
    assert.match(frame, /焦点:文件/);
    assert.match(frame, /工作区 vs HEAD/);
    for (const action of ['r 接入', 'f 固定', 'o 路径', 'q 退出']) assert.ok(frame.includes(action), `${columns}: ${action}`);
    stdin.write('\t');
    assert.match(await waitForFrame(lastFrame, /焦点:diff/), /焦点:diff/);
    stdin.write('o');
    const fullPath = await waitForFrame(lastFrame, /文件路径/);
    assert.ok(fullPath.replace(/\s+/g, '').includes(path));
    stdin.write('\u001b'); await pause(30);
    assert.match(lastFrame() ?? '', /焦点:diff/);
    assert.match(lastFrame() ?? '', /added line/);
  }
});

test('resizing preserves the source diff line at the reading anchor', async () => {
  const text = Array.from({length: 80}, (_, i) => `+ROW${String(i).padStart(2, '0')} ${'中文'.repeat(18)}`).join('\n');
  const props = {workspace: '/repo', refreshMs: 0, signalPollMs: 0, readStatus: () => status,
    readDiff: (): DiffResult => ({ok: true, text, truncated: false})};
  const {lastFrame, stdin, rerender} = render(<SidebarApp {...props} size={{columns: 60, rows: 20}}/>);
  await waitForFrame(lastFrame, /ROW00/);
  stdin.write('\u001b[6~'); await pause(30);
  const anchor = (lastFrame() ?? '').match(/ROW\d+/)?.[0];
  assert.ok(anchor && anchor !== 'ROW00');
  rerender(<SidebarApp {...props} size={{columns: 40, rows: 20}}/>);
  await pause(30);
  assert.equal((lastFrame() ?? '').match(/ROW\d+/)?.[0], anchor);
});

test('path inspection pins its object while new agent events arrive', async () => {
  const owner = createSidebarOwner();
  let signal = {scope: {workspace: '/repo', owner, sessionId: null}, revision: 1, version: '1', files: [files[0]!.path], active: true, updatedAt: 0};
  const {stdin, lastFrame} = render(<SidebarApp workspace="/repo" owner={owner} refreshMs={0} signalPollMs={10}
    size={{columns: 60, rows: 18}} readStatus={() => status} readDiff={(_root, file) => ({ok: true, text: `+${file.path}`, truncated: false})}
    readSignal={() => signal}/>);
  await waitForFrame(lastFrame, /\+tui/);
  stdin.write('o'); await pause(20);
  signal = {...signal, revision: 2, version: '2', files: ['new.ts', files[0]!.path]};
  await pause(50);
  assert.match(lastFrame() ?? '', /tui\/src\/app.tsx/);
  assert.doesNotMatch(lastFrame() ?? '', /new.ts/);
  stdin.write('\u001b'); await pause(30);
  assert.match(lastFrame() ?? '', /固定/);
  assert.match(lastFrame() ?? '', /\+tui/);
  stdin.write('f');
  assert.match(await waitForFrame(lastFrame, /\+new.ts/), /\+new.ts/);
  signal = {...signal, revision: 3, version: '3', files: [files[0]!.path, 'new.ts']};
  assert.match(await waitForFrame(lastFrame, /\+tui/), /\+tui/);
});

test('a rejected old-file query cannot mark the new selection as failed', async () => {
  let reject: ((reason: Error) => void) | undefined;
  const {stdin, lastFrame, frames} = render(<SidebarApp workspace="/repo" refreshMs={0} signalPollMs={0}
    size={{columns: 60, rows: 18}} readStatus={() => status}
    readDiff={(_root, file) => file.path === files[0]!.path
      ? new Promise<DiffResult>((_resolve, fail) => {reject = fail;})
      : {ok: true, text: '+new file content', truncated: false}}/>);
  for (let i = 0; i < 100 && !reject; i++) await pause(5);
  assert.ok(reject);
  stdin.write('j'); await pause(20);
  const from = frames.length;
  reject(new Error('old-file-only-error'));
  assert.match(await waitForFrame(lastFrame, /new file content/), /new file content/);
  assert.ok(frames.slice(from).every(frame => !frame.includes('old-file-only-error')));
});

test('truncation is visible at the start and hidden-tail revisions still notify a pinned reader', async () => {
  let revision = '1';
  const {stdin, lastFrame} = render(<SidebarApp workspace="/repo" refreshMs={20} signalPollMs={0}
    size={{columns: 40, rows: 18}} readStatus={() => status}
    readDiff={() => ({ok: true, text: '+same displayed prefix', revision, truncated: true})}/>);
  assert.match(await waitForFrame(lastFrame, /same displayed/), /截断/);
  stdin.write('f'); await pause(30);
  revision = '2';
  assert.match(await waitForFrame(lastFrame, /有更新/), /有更新/);
});

test('empty workspace explains there is nothing to review', async () => {
  const {lastFrame} = mount({statusResult: {ok: true, status: {root: '/repo', branch: 'main', head: 'abc', ahead: 0, behind: 0, files: []}}});
  await pause(40);
  assert.match(lastFrame() ?? '', /工作区没有未提交改动/u);
});

test('non-git workspace shows the read-only error instead of crashing', async () => {
  const {lastFrame} = mount({statusResult: {ok: false, reason: 'not-git', message: '当前工作区不是 Git 仓库'}});
  await pause(40);
  assert.match(lastFrame() ?? '', /当前工作区不是 Git 仓库/u);
});

test('diff read errors stay visible and q exits the sidebar', async () => {
  const {stdin, lastFrame, quits} = mount({diffResult: {ok: false, message: '读取 diff 失败：权限不足'}});
  await pause(40);
  assert.match(lastFrame() ?? '', /读取 diff 失败/u);
  stdin.write('q');
  await pause(20);
  assert.equal(quits(), 1);
});

test('sidebar header shows plan summary from signal', async () => {
  const owner = createSidebarOwner();
  const signal = {scope: {workspace: '/repo', owner, sessionId: null}, revision: 1, version: 'plan:1', files: [], active: false, updatedAt: Date.now(),
    plan: {version: 1, status: 'approved', stale: false, objective: '计划目标', stepCount: 2, doneSteps: 1, acceptance: true,
      steps: [{id: 's1', title: '读代码', status: 'done'}, {id: 's2', title: '写测试', status: 'in_progress'}]}};
  const {lastFrame, stdin} = render(<SidebarApp workspace="/repo" owner={owner} theme="dark" refreshMs={0} signalPollMs={10}
    size={{columns: 80, rows: 18}} readSignal={() => signal}
    readStatus={() => ({...status, status: {...status.status, files: []}})}/>);
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.match(lastFrame() ?? '', /计划 v1 approved 1\/2步 ✓/);
  stdin.write('p');
  await pause(20);
  assert.match(lastFrame() ?? '', /s1 读代码/);
  stdin.write('\u001b[B');
  await pause(20);
  assert.match(lastFrame() ?? '', /› in_progress\s+s2 写测试/);
});

test('agent activity signal refreshes immediately, follows and marks agent files', async () => {
  let statusCalls = 0;
  let signalReads = 0;
  const owner = createSidebarOwner();
  const signal = {scope: {workspace: '/repo', owner, sessionId: null}, revision: 1, version: 'run-1:3', files: ['new.ts'], active: true, updatedAt: Date.now()};
  const {lastFrame} = render(<SidebarApp workspace="/repo" owner={owner} theme="dark" refreshMs={0} signalPollMs={15} size={{columns: 60, rows: 18}}
    readStatus={() => {statusCalls += 1; return status;}}
    readDiff={() => diff}
    readSignal={() => {if (signalReads++ === 0) {assert.equal(statusCalls, 0); return null;} return signal;}}
    onQuit={() => {}}/>);
  await pause(120);
  const frame = lastFrame() ?? '';
  assert.ok(statusCalls >= 1, `source signal must trigger status: ${statusCalls}`);
  assert.match(frame, /● 本轮触及 1/u);
  assert.match(frame, /新增 · new\.ts/u);
});
