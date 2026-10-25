import assert from 'node:assert/strict';
import test from 'node:test';
import type {ChangedFile, WorkspaceStatus} from '../src/sidebar/git.js';
import {
  applyWorkspace, initialSidebarState, scrollDiff, selectDelta, selectedFile, toggleFocus, toggleFollow,
} from '../src/sidebar/state.js';

function file(path: string, overrides: Partial<ChangedFile> = {}): ChangedFile {
  return {path, status: 'modified', index: '.', worktree: 'M', additions: 1, deletions: 0, staged: false, ...overrides};
}

function status(files: ChangedFile[], overrides: Partial<WorkspaceStatus> = {}): WorkspaceStatus {
  return {root: '/repo', branch: 'main', head: 'abc', ahead: 0, behind: 0, files, ...overrides};
}

test('first snapshot selects the first file and keeps follow enabled', () => {
  const state = applyWorkspace(initialSidebarState(), status([file('a.ts'), file('b.ts')]));
  assert.equal(state.selected, 'a.ts');
  assert.equal(state.follow, true);
  assert.equal(state.error, null);
  assert.equal(selectedFile(state)?.path, 'a.ts');
});

test('follow tracks the file whose signature changed, including new files', () => {
  let state = applyWorkspace(initialSidebarState(), status([file('a.ts'), file('b.ts')]));
  state = applyWorkspace(state, status([file('a.ts'), file('b.ts', {additions: 7})]));
  assert.equal(state.selected, 'b.ts');
  state = applyWorkspace(state, status([file('a.ts'), file('b.ts', {additions: 7}), file('c.ts', {status: 'untracked'})]));
  assert.equal(state.selected, 'c.ts');
});

test('manual selection disables follow and survives refreshes', () => {
  let state = applyWorkspace(initialSidebarState(), status([file('a.ts'), file('b.ts')]));
  state = selectDelta(state, 1);
  assert.equal(state.selected, 'b.ts');
  assert.equal(state.follow, false);
  state = applyWorkspace(state, status([file('a.ts', {additions: 9}), file('b.ts'), file('c.ts')]));
  assert.equal(state.selected, 'b.ts');
});

test('selection falls back to the first file when it disappears', () => {
  let state = applyWorkspace(initialSidebarState(), status([file('a.ts'), file('b.ts')]));
  state = selectDelta(state, 1);
  state = applyWorkspace(state, status([file('a.ts')]));
  assert.equal(state.selected, 'a.ts');
});

test('follow can be toggled and resets diff scroll', () => {
  let state = applyWorkspace(initialSidebarState(), status([file('a.ts'), file('b.ts')]));
  state = scrollDiff(state, 5, 10);
  assert.equal(state.diffScroll, 5);
  state = toggleFollow(state);
  assert.equal(state.follow, false);
  state = toggleFollow(state);
  assert.equal(state.follow, true);
  assert.equal(state.diffScroll, 0);
});

test('focus, selection boundaries and scroll clamping stay within limits', () => {
  let state = applyWorkspace(initialSidebarState(), status([file('a.ts'), file('b.ts')]));
  assert.equal(state.focus, 'list');
  state = toggleFocus(state);
  assert.equal(state.focus, 'diff');
  state = selectDelta(state, -1);
  assert.equal(state.selected, 'a.ts');
  state = selectDelta(state, 99);
  assert.equal(state.selected, 'b.ts');
  assert.equal(selectDelta(state, 99).selected, 'b.ts');
  state = scrollDiff(state, 99, 3);
  assert.equal(state.diffScroll, 3);
  state = scrollDiff(state, -99, 3);
  assert.equal(state.diffScroll, 0);
});
