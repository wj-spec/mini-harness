import assert from 'node:assert/strict';
import test from 'node:test';
import {agentTouchedFiles, patchPaths} from '../src/sidebar/agent-activity.js';
import type {Entry} from '../src/view-state.js';

function entry(id: number, name: string, args: Record<string, unknown>, overrides: Partial<Entry> = {}): Entry {
  return {id, kind: 'tool', text: name, call: {id: `c${id}`, name, args}, ok: true, output: '', runId: 'run-1', ...overrides};
}

const patch = [
  'diff --git a/src/a.ts b/src/a.ts',
  '--- a/src/a.ts',
  '+++ b/src/a.ts',
  '@@ -1 +1 @@',
  '-old',
  '+new',
  'diff --git a/src/b.ts b/src/b.ts',
  '--- a/src/b.ts',
  '+++ b/src/b.ts',
].join('\n');

test('patchPaths extracts target paths from a unified diff', () => {
  assert.deepEqual(patchPaths(patch), ['src/a.ts', 'src/a.ts', 'src/b.ts', 'src/b.ts']);
  assert.deepEqual(patchPaths('+++ b/only.ts\n'), ['only.ts']);
  assert.deepEqual(patchPaths('--- a/old.ts\n'), ['old.ts']);
  assert.deepEqual(patchPaths('not a patch'), []);
});

test('agentTouchedFiles collects only successful write/edit/apply_patch entries', () => {
  const entries: Entry[] = [
    entry(1, 'edit_file', {path: 'src/a.ts'}),
    entry(2, 'write_file', {path: './src/b.ts'}),
    entry(3, 'apply_patch', {patch}),
    entry(4, 'edit_file', {path: 'src/c.ts'}, {ok: false}),
    entry(5, 'bash', {command: 'touch x'}),
    entry(6, 'edit_file', {path: 'src/a.ts'}),
  ];
  assert.deepEqual(agentTouchedFiles(entries), ['src/a.ts', 'src/b.ts']);
});

test('agentTouchedFiles ignores uncertain tool results', () => {
  const entries: Entry[] = [entry(1, 'write_file', {path: 'x.ts'}, {uncertain: true})];
  assert.deepEqual(agentTouchedFiles(entries), []);
});


test('only files from the latest run are marked', () => {
  const entries: Entry[] = [
    entry(1, 'edit_file', {path: 'old.ts'}, {runId: 'run-0'}),
    entry(2, 'edit_file', {path: 'new.ts'}),
  ];
  assert.deepEqual(agentTouchedFiles(entries), ['new.ts']);
});
