import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {mkdtempSync, writeFileSync, rmSync, renameSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import test, {afterEach, type TestContext} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import {SidebarApp} from '../src/sidebar/app.js';
import {readFileDiffAsync, readWorkspaceStatusAsync, runGitAsync} from '../src/sidebar/git.js';

afterEach(cleanup);

function repository(t: TestContext) {
  const root = mkdtempSync(path.join(tmpdir(), 'sidebar-git-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  const git = (...args: string[]) => execFileSync('git', args, {cwd: root, stdio: 'pipe'});
  git('init', '-q');
  for (const name of ['A.txt', 'delete.txt', 'rename.txt']) writeFileSync(path.join(root, name), `${name} original\n`);
  writeFileSync(path.join(root, 'binary.bin'), Buffer.from([0, 1, 2]));
  git('add', '.');
  git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'baseline');
  return {root, git};
}

test('real Git external equal-count edits appear while pinned and after acceptance', async t => {
  const {root} = repository(t);
  writeFileSync(path.join(root, 'A.txt'), 'version-one\n');
  const {lastFrame, stdin} = render(<SidebarApp workspace={root} refreshMs={30} signalPollMs={0} size={{columns: 60, rows: 18}}/>);
  const wait = async (pattern: RegExp) => {
    for (let i = 0; i < 200 && !pattern.test(lastFrame() ?? ''); i++) await pause(10);
    assert.match(lastFrame() ?? '', pattern);
  };
  await wait(/\+version-one/);
  stdin.write('f'); await wait(/固定/);
  writeFileSync(path.join(root, 'A.txt'), 'version-two\n');
  await wait(/有更新/);
  assert.match(lastFrame() ?? '', /\+version-one/);
  stdin.write('r'); await wait(/\+version-two/);
});

test('async Git reads cover rename, deletion, binary, untracked, empty and non-Git workspaces', async t => {
  const {root, git} = repository(t);
  const clean = await readWorkspaceStatusAsync(root);
  assert.ok(clean.ok); assert.deepEqual(clean.status.files, []);
  rmSync(path.join(root, 'delete.txt'));
  renameSync(path.join(root, 'rename.txt'), path.join(root, 'renamed.txt'));
  git('add', '-A');
  writeFileSync(path.join(root, 'untracked.txt'), 'hello\n');
  writeFileSync(path.join(root, 'binary.bin'), Buffer.from([0, 1, 3]));
  const status = await readWorkspaceStatusAsync(root);
  assert.ok(status.ok);
  const files = new Map(status.status.files.map(file => [file.path, file]));
  assert.equal(files.get('delete.txt')?.status, 'deleted');
  assert.equal(files.get('renamed.txt')?.originalPath, 'rename.txt');
  for (const [name, expected] of [['delete.txt', /-delete.txt original/], ['renamed.txt', /original|rename/], ['binary.bin', /Binary files/], ['untracked.txt', /\+hello/]] as const) {
    const result = await readFileDiffAsync(root, files.get(name)!, true);
    assert.ok(result.ok); assert.match(result.text, expected);
  }
  const nonGit = path.join(root, '.git');
  const result = await readWorkspaceStatusAsync(nonGit);
  assert.equal(result.ok, false);
});

test('large diff reads stay bounded and detect changes outside their displayed prefix', async t => {
  const {root} = repository(t);
  const name = 'large.txt';
  const prefix = ('+' + '中文'.repeat(100) + '\n').repeat(4000);
  writeFileSync(path.join(root, name), prefix + 'before\n');
  const status = await readWorkspaceStatusAsync(root);
  assert.ok(status.ok);
  const file = status.status.files.find(file => file.path === name)!;
  const before = await readFileDiffAsync(root, file);
  writeFileSync(path.join(root, name), prefix + 'after!\n');
  const after = await readFileDiffAsync(root, file);
  assert.ok(before.ok && after.ok);
  assert.ok(before.truncated && after.truncated);
  assert.ok(before.text.replace(/^index .+$/m, '') === after.text.replace(/^index .+$/m, ''), 'only the Git object ID changes in the displayed prefix');
  assert.notEqual(before.revision, after.revision);
  assert.ok(Buffer.byteLength(before.text) + Buffer.byteLength(after.text) <= 4 * 1024 * 1024);
  const limited = await runGitAsync(['diff', '--no-index', '--', '/dev/null', name], {cwd: root, maxBuffer: 16});
  assert.ok(limited.error, 'output limit must fail explicitly');
});

test('a staged file in an unborn repository is shown against the empty baseline', async t => {
  const root = mkdtempSync(path.join(tmpdir(), 'sidebar-unborn-'));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  execFileSync('git', ['init', '-q'], {cwd: root});
  writeFileSync(path.join(root, 'first.txt'), 'first content\n');
  execFileSync('git', ['add', 'first.txt'], {cwd: root});
  const status = await readWorkspaceStatusAsync(root);
  assert.ok(status.ok); assert.equal(status.status.head, null);
  const diff = await readFileDiffAsync(root, status.status.files[0]!, false);
  assert.ok(diff.ok); assert.match(diff.text, /\+first content/);
});
