import assert from 'node:assert/strict';
import test from 'node:test';
import stringWidth from 'string-width';
import {diffStats, parseDiff, wrapDiffLines} from '../src/sidebar/diff.js';

const sample = [
  'diff --git a/a.ts b/a.ts',
  'index 111..222 100644',
  '--- a/a.ts',
  '+++ b/a.ts',
  '@@ -1,2 +1,3 @@',
  ' context',
  '-old line',
  '+new line',
  '+another line',
  '\\ No newline at end of file',
].join('\n');

test('unified diff lines are classified for coloring and meta display', () => {
  const lines = parseDiff(sample);
  assert.deepEqual(lines.map(line => line.kind), ['meta', 'meta', 'meta', 'meta', 'hunk', 'context', 'del', 'add', 'add', 'note']);
  assert.deepEqual(diffStats(lines), {additions: 2, deletions: 1});
  assert.deepEqual(parseDiff(''), []);
});

test('wrapping keeps display width and preserves wide characters', () => {
  const lines = parseDiff(`${'a'.repeat(25)}\n中文中文中文`);
  const wrapped = wrapDiffLines(lines, 10);
  assert.ok(wrapped.length >= 5);
  for (const line of wrapped) assert.ok(stringWidth(line.text) <= 10, `line too wide: ${line.text}`);
  assert.equal(wrapped[0]?.text, 'a'.repeat(10));
  assert.equal(wrapped.at(-1)?.text, '文');
});

test('empty diff lines survive wrapping without collapsing the diff body', () => {
  const lines = parseDiff('@@ -1 +1 @@\n\n+added');
  assert.deepEqual(lines.map(line => line.kind), ['hunk', 'context', 'add']);
  const wrapped = wrapDiffLines(lines, 20);
  assert.deepEqual(wrapped.map(line => line.text), ['@@ -1 +1 @@', '', '+added']);
});
