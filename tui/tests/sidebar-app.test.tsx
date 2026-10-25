import assert from 'node:assert/strict';
import test, {afterEach} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {SidebarApp} from '../src/sidebar/app.js';
import type {ChangedFile, DiffResult, StatusResult} from '../src/sidebar/git.js';

afterEach(cleanup);

const files: ChangedFile[] = [
  {path: 'tui/src/app.tsx', status: 'modified', index: '.', worktree: 'M', additions: 4, deletions: 2, staged: false},
  {path: 'new.ts', status: 'added', index: 'A', worktree: '.', additions: 6, deletions: 0, staged: true},
];

const status: StatusResult = {ok: true, status: {root: '/repo', branch: 'main', head: 'abc1234', ahead: 0, behind: 0, files}};
const diffText = ['diff --git a/tui/src/app.tsx b/tui/src/app.tsx', '@@ -1,2 +1,3 @@', ' context', '-old line', '+added line'].join('\n');
const diff: DiffResult = {ok: true, text: diffText, truncated: false};

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
  await pause(40);
  const frame = lastFrame() ?? '';
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
  await pause(40);
  stdin.write('j');
  await pause(30);
  assert.match(lastFrame() ?? '', /新增 · new\.ts/u);
  assert.match(lastFrame() ?? '', /手动/u);
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
