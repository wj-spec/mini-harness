import assert from 'node:assert/strict';
import test from 'node:test';
import {
  MAX_DIFF_LINES, STATUS_ARGS, changeCounts, diffArgs, mergeCounts, numstatArgs, parseNumstat,
  parsePorcelainV2, readFileDiff, readWorkspaceStatus, statusLetter, totalCounts,
  type ChangedFile, type GitRunResult,
} from '../src/sidebar/git.js';

const statusOutput = [
  '# branch.oid abc1234def',
  '# branch.head main',
  '# branch.ab +2 -1',
  '1 .M N... 100644 100644 100644 aaa bbb tui/src/app.tsx',
  '1 A. N... 000000 100644 100644 000 ccc new.ts',
  '1 .D N... 100644 100644 000 aaa ddd old.ts',
  '2 R. N... 100644 100644 100644 aaa eee R100 newname.ts',
  'oldname.ts',
  '? untracked.md',
  'u UU N... 100644 100644 100644 100644 aaa bbb ccc conflict.ts',
].join('\0') + '\0';

function fixtureRunner(overrides: Partial<Record<string, GitRunResult>> = {}) {
  const calls: {args: string[]; cwd: string}[] = [];
  const runner = (args: string[], options: {cwd: string; timeoutMs?: number; maxBuffer?: number}): GitRunResult => {
    calls.push({args, cwd: options.cwd});
    const joined = args.join(' ');
    if (joined.includes('status --porcelain=v2')) {
      return overrides.status ?? {status: 0, stdout: statusOutput, stderr: ''};
    }
    if (joined.includes('--numstat')) {
      return overrides.numstat ?? {status: 0, stdout: '12\t3\ttui/src/app.tsx\0-\t-\tnew.ts\0', stderr: ''};
    }
    if (joined.includes('--no-index')) {
      return overrides.untrackedDiff ?? {status: 1, stdout: '+++ b/untracked.md\n+hello\n', stderr: ''};
    }
    if (joined.includes('diff')) {
      return overrides.diff ?? {status: 0, stdout: 'diff --git a/tui/src/app.tsx b/tui/src/app.tsx\n@@ -1 +1 @@\n-old\n+new\n', stderr: ''};
    }
    return {status: 0, stdout: '', stderr: ''};
  };
  return {runner, calls};
}

test('porcelain v2 parses branch, counts, rename, untracked and conflict records', () => {
  const status = parsePorcelainV2(statusOutput, '/repo');
  assert.equal(status.branch, 'main');
  assert.equal(status.head, 'abc1234def');
  assert.equal(status.ahead, 2);
  assert.equal(status.behind, 1);
  const byPath = new Map(status.files.map(file => [file.path, file]));
  assert.equal(byPath.get('tui/src/app.tsx')?.status, 'modified');
  assert.equal(byPath.get('tui/src/app.tsx')?.staged, false);
  assert.equal(byPath.get('new.ts')?.status, 'added');
  assert.equal(byPath.get('new.ts')?.staged, true);
  assert.equal(byPath.get('old.ts')?.status, 'deleted');
  assert.equal(byPath.get('newname.ts')?.status, 'renamed');
  assert.equal(byPath.get('newname.ts')?.originalPath, 'oldname.ts');
  assert.equal(byPath.get('untracked.md')?.status, 'untracked');
  assert.equal(byPath.get('conflict.ts')?.status, 'conflicted');
  assert.equal(byPath.get('conflict.ts')?.staged, false);
});

test('detached head falls back to a short object id', () => {
  const status = parsePorcelainV2(['# branch.oid 0123456789abcdef', '# branch.head (detached)', '? a.md', ''].join('\0'), '/repo');
  assert.equal(status.branch, '0123456');
  assert.equal(status.head, '0123456789abcdef');
});

test('numstat -z parsing and merge fills tracked additions/deletions only', () => {
  const files: ChangedFile[] = [
    {path: 'tui/src/app.tsx', status: 'modified', index: '.', worktree: 'M', additions: null, deletions: null, staged: false},
    {path: 'new.ts', status: 'untracked', index: '?', worktree: '?', additions: null, deletions: null, staged: false},
  ];
  const counts = parseNumstat('12\t3\ttui/src/app.tsx\0-\t-\tnew.ts\0');
  const merged = mergeCounts(files, counts);
  assert.equal(merged[0]?.additions, 12);
  assert.equal(merged[0]?.deletions, 3);
  assert.equal(merged[1]?.additions, null);
  assert.equal(changeCounts(merged[0]!), '+12 -3');
  assert.equal(changeCounts(merged[1]!), '');
  assert.deepEqual(totalCounts(merged), {additions: 12, deletions: 3, unknown: true});
});

test('status flags are fixed, shell-free and disable path quoting', () => {
  assert.deepEqual(STATUS_ARGS.slice(0, 3), ['-c', 'core.quotepath=false', '--no-optional-locks']);
  assert.ok(STATUS_ARGS.includes('--porcelain=v2'));
  assert.ok(STATUS_ARGS.includes('-z'));
  assert.ok(STATUS_ARGS.includes('--untracked-files=all'));
  assert.ok(numstatArgs(true).includes('HEAD'));
  assert.ok(numstatArgs(false).includes('--numstat'));
  const tracked = diffArgs({path: 'a.ts', status: 'modified', index: '.', worktree: 'M', additions: null, deletions: null, staged: false}, true);
  assert.ok(tracked.includes('HEAD'));
  assert.ok(tracked.includes('a.ts'));
  const untracked = diffArgs({path: 'u.ts', status: 'untracked', index: '?', worktree: '?', additions: null, deletions: null, staged: false}, true);
  assert.ok(untracked.includes('--no-index'));
  assert.ok(untracked.includes('/dev/null'));
});

test('readWorkspaceStatus merges git status and numstat through injected runner', () => {
  const {runner, calls} = fixtureRunner();
  const result = readWorkspaceStatus('/repo', runner);
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.status.files.find(file => file.path === 'tui/src/app.tsx')?.additions, 12);
  assert.ok(calls.every(call => call.cwd === '/repo'));
});

test('readWorkspaceStatus maps non-git and missing-git failures', () => {
  const {runner: nonGit} = fixtureRunner({status: {status: 128, stdout: '', stderr: 'fatal: not a git repository (or any of the parent directories): .git'}});
  const missing = readWorkspaceStatus('/repo', nonGit);
  assert.equal(missing.ok, false);
  if (!missing.ok) assert.equal(missing.reason, 'not-git');
  const {runner: noGit} = fixtureRunner({status: {status: null, stdout: '', stderr: '', error: 'spawnSync git ENOENT'}});
  const missingBinary = readWorkspaceStatus('/repo', noGit);
  assert.equal(missingBinary.ok, false);
  if (!missingBinary.ok) assert.equal(missingBinary.reason, 'git-missing');
});

test('readFileDiff accepts no-index exit 1 and truncates very long diffs', () => {
  const file: ChangedFile = {path: 'untracked.md', status: 'untracked', index: '?', worktree: '?', additions: null, deletions: null, staged: false};
  const {runner} = fixtureRunner();
  const diff = readFileDiff('/repo', file, true, runner);
  assert.equal(diff.ok, true);
  if (diff.ok) assert.match(diff.text, /\+hello/);

  const longLines = Array.from({length: MAX_DIFF_LINES + 20}, (_, index) => `+line ${index}`).join('\n');
  const longRunner = () => ({status: 0, stdout: longLines, stderr: ''});
  const truncated = readFileDiff('/repo', {...file, status: 'modified'}, true, longRunner);
  assert.equal(truncated.ok, true);
  if (truncated.ok) {
    assert.equal(truncated.truncated, true);
    assert.match(truncated.text, /diff 已截断/);
  }
});

test('status letters and counts cover added, deleted and untracked files', () => {
  const base = {index: '.', worktree: '.', additions: null, deletions: null, staged: false};
  assert.equal(statusLetter({...base, path: 'a', status: 'added'}), 'A');
  assert.equal(statusLetter({...base, path: 'd', status: 'deleted'}), 'D');
  assert.equal(statusLetter({...base, path: '?', status: 'untracked'}), '?');
  assert.equal(statusLetter({...base, path: 'r', status: 'renamed'}), 'R');
  assert.equal(changeCounts({...base, path: 'x', status: 'modified', additions: 4, deletions: 1}), '+4 -1');
});
