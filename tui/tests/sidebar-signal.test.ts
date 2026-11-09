import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {writeFileSync} from 'node:fs';
import test from 'node:test';
import {readSidebarSignal, removeSidebarSignal, sidebarSignalPath, writeSidebarSignal} from '../src/sidebar/signal.js';


test('revision source and artifact signals require a bound run and preserve the exact plan identity', () => {
  const root = `/tmp/sidebar-revision-envelope-${randomUUID()}`; const owner = {instanceId: randomUUID(), parentPid: process.pid};
  const target = {session_id: 's', plan_task_id: 't-20261109-010000-abcdef', plan_id: 'pl-source', plan_version: 2, plan_hash: 'a'.repeat(64)};
  const run = {requestId: 'request', traceRunId: null, taskId: 'revision-task', segment: 2, taskStatus: 'completed', status: 'finished' as const, objective: 'revise'};
  try {
    for (const relation of ['revision-source', 'revision-result'] as const) {
      const saved = writeSidebarSignal(root, {owner, sessionId: 's', signature: relation, files: [], active: false, run,
        plan: {target, relation, problem: null, version: 2, status: 'proposed', stale: false, objective: 'exact', stepCount: 1, doneSteps: null, acceptance: null}});
      assert.equal(readSidebarSignal(root, owner)?.plan?.relation, relation);
      assert.deepEqual(readSidebarSignal(root, owner)?.plan?.target, target);
      for (const invalid of [undefined, {...run, taskId: null}, {...run, segment: null}, {...run, requestId: null}]) {
        writeFileSync(sidebarSignalPath(root, owner), JSON.stringify({...saved, run: invalid})); assert.equal(readSidebarSignal(root, owner), null);
      }
    }
  } finally {removeSidebarSignal(root, owner);}
});

test('result navigation requires an owned signal and a known page with a unique selection id', () => {
  const root = `/tmp/sidebar-navigation-envelope-${randomUUID()}`; const owner = {instanceId: randomUUID(), parentPid: process.pid};
  const navigation = {id: randomUUID(), page: 'verification' as const, mode: 'result' as const};
  try {
    const saved = writeSidebarSignal(root, {owner, sessionId: 's', signature: 'result', files: [], active: false, navigation});
    assert.deepEqual(readSidebarSignal(root, owner)?.navigation, navigation);
    for (const invalid of [{...navigation, page: 'execute'}, {...navigation, id: 'old'}, {...navigation, mode: 'approve'}, {...navigation, extra: true}]) {
      writeFileSync(sidebarSignalPath(root, owner), JSON.stringify({...saved, navigation: invalid}));
      assert.equal(readSidebarSignal(root, owner), null);
    }
    writeSidebarSignal(root, {signature: 'unowned', files: [], active: false, navigation});
    assert.equal(readSidebarSignal(root), null);
  } finally {removeSidebarSignal(root, owner); removeSidebarSignal(root);}
});

test('verification envelopes reject another request and inconsistent execution facts', () => {
  const root = `/tmp/sidebar-verification-envelope-${randomUUID()}`;
  const owner = {instanceId: randomUUID(), parentPid: process.pid};
  const input = {owner, sessionId: 'session', signature: 'v', files: [], active: false,
    run: {requestId: 'request', traceRunId: null, taskId: null, segment: null, taskStatus: null, status: 'finished' as const, objective: 'check'},
    verification: {requestId: 'request', status: 'done' as const, stopReason: '', verified: null,
      completionAllowed: null, completionMessages: [], omittedCommands: 0,
      commands: [{id: 'c', command: 'test', verification: true, pending: false, ok: true,
        execution: {status: 'succeeded' as const, exit_code: 0}, output: 'ok', truncated: false}]}};
  try {
    const saved = writeSidebarSignal(root, input);
    assert.deepEqual(readSidebarSignal(root, owner)?.verification, input.verification);
    for (const invalid of [
      {...saved, run: undefined},
      {...saved, verification: {...input.verification, requestId: 'other'}},
      {...saved, verification: {...input.verification, commands: [{...input.verification.commands[0], execution: {status: 'succeeded', exit_code: 2}}]}},
      {...saved, verification: {...input.verification, commands: [{...input.verification.commands[0], output: 'x'.repeat(2001)}]}},
    ]) {
      writeFileSync(sidebarSignalPath(root, owner), JSON.stringify(invalid));
      assert.equal(readSidebarSignal(root, owner), null);
    }
  } finally {removeSidebarSignal(root, owner);}
});

test('scoped signals increment revisions and reject mismatched, corrupt or oversized envelopes', () => {
  const root = `/tmp/sidebar-envelope-${randomUUID()}`;
  const owner = {instanceId: randomUUID(), parentPid: process.pid};
  try {
    const input = {owner, sessionId: 'a', signature: 'first', files: ['a.ts'], active: true};
    assert.equal(writeSidebarSignal(root, input).revision, 1);
    const second = writeSidebarSignal(root, {...input, sessionId: 'b', signature: 'second'});
    assert.equal(second.revision, 2);
    assert.equal(readSidebarSignal(root, owner)?.scope?.sessionId, 'b');
    for (const invalid of [
      {...second, scope: {...second.scope, workspace: '/other'}},
      {...second, scope: {...second.scope, owner: {...owner, parentPid: owner.parentPid + 1}}},
      {...second, revision: 0}, {...second, revision: 1.5},
      {...second, version: 'x'.repeat(256 * 1024)},
    ]) {
      writeFileSync(sidebarSignalPath(root, owner), JSON.stringify(invalid));
      assert.equal(readSidebarSignal(root, owner), null);
    }
    writeFileSync(sidebarSignalPath(root, owner), '{');
    assert.equal(readSidebarSignal(root, owner), null);
  } finally {removeSidebarSignal(root, owner);}
});

test('two workbenches in the same workspace keep separate signals and cleanup', () => {
  const root = `/tmp/sidebar-two-owners-${randomUUID()}`;
  const a = {instanceId: randomUUID(), parentPid: process.pid};
  const b = {instanceId: randomUUID(), parentPid: process.pid};
  const first = {signature: 'a-1', files: ['a.ts'], active: true, owner: a, sessionId: 'session-a'};
  const second = {signature: 'b-1', files: ['b.ts'], active: false, owner: b, sessionId: 'session-b'};
  try {
    writeSidebarSignal(root, first);
    writeSidebarSignal(root, second);
    assert.equal(readSidebarSignal(root, a)?.version, 'a-1');
    assert.equal(readSidebarSignal(root, b)?.version, 'b-1');
    removeSidebarSignal(root, a);
    assert.equal(readSidebarSignal(root, b)?.version, 'b-1');
  } finally {
    removeSidebarSignal(root, a);
    removeSidebarSignal(root, b);
    removeSidebarSignal(root);
  }
});

test('sidebar signals include plan summary and can be removed', () => {
  const root = `/tmp/mini-harness-signal-plan-${randomUUID()}`;
  writeSidebarSignal(root, {
    signature: 'plan:2', files: [], active: false,
    plan: {version: 2, status: 'approved', stale: false, objective: '计划', stepCount: 3, doneSteps: 2, acceptance: true},
  });
  const signal = readSidebarSignal(root);
  assert.equal(signal?.plan?.version, 2);
  assert.equal(signal?.plan?.stepCount, 3);
  assert.equal(signal?.plan?.acceptance, true);
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
