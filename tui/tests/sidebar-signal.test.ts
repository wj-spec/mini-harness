import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import test from 'node:test';
import {readSidebarSignal, removeSidebarSignal, sidebarSignalPath, writeSidebarSignal} from '../src/sidebar/signal.js';

test('sidebar signals include plan summary and can be removed', () => {
  const root = `/tmp/mini-harness-signal-plan-${randomUUID()}`;
  writeSidebarSignal(root, {
    signature: 'plan:2', files: [], active: false,
    plan: {version: 2, status: 'approved', stale: false, objective: '计划', stepCount: 3},
  });
  const signal = readSidebarSignal(root);
  assert.equal(signal?.plan?.version, 2);
  assert.equal(signal?.plan?.stepCount, 3);
  removeSidebarSignal(root);
});

test('sidebar signals round-trip per workspace and can be removed', () => {
  const first = `/tmp/mini-harness-signal-${randomUUID()}`;
  const second = `/tmp/mini-harness-signal-${randomUUID()}`;
  try {
    assert.equal(readSidebarSignal(first), null);
    writeSidebarSignal(first, {signature: 'run-1:2', files: ['a.ts', 'dir/b.ts'], active: true});
    const signal = readSidebarSignal(first);
    assert.equal(signal?.version, 'run-1:2');
    assert.deepEqual(signal?.files, ['a.ts', 'dir/b.ts']);
    assert.equal(signal?.active, true);
    assert.ok(sidebarSignalPath(first).endsWith('.signal.json'));

    writeSidebarSignal(second, {signature: 'run-2:1', files: [], active: false});
    assert.equal(readSidebarSignal(first)?.version, 'run-1:2');
    assert.equal(readSidebarSignal(second)?.version, 'run-2:1');

    removeSidebarSignal(first);
    assert.equal(readSidebarSignal(first), null);
  } finally {
    removeSidebarSignal(first);
    removeSidebarSignal(second);
  }
});
