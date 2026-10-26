import assert from 'node:assert/strict';
import {mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {tmpdir} from 'node:os';
import test from 'node:test';
import {goalFilePath, loadGoal, saveGoal} from '../src/goal-store.js';
import {startGoal} from '../src/goal.js';

test('goal state round-trips through the session directory', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-goal-'));
  try {
    const goal = startGoal('持久化目标');
    saveGoal(dir, goal);
    const raw = JSON.parse(await readFile(goalFilePath(dir), 'utf8'));
    assert.equal(raw.version, 1);
    assert.equal(raw.objective, '持久化目标');
    assert.equal(raw.active, true);

    const restored = loadGoal(dir);
    assert.equal(restored?.objective, '持久化目标');
    assert.equal(restored?.active, false, 'a restarted TUI must wait for explicit /goal resume');
    assert.equal(restored?.pausedReason, 'session_restored');

    saveGoal(dir, {...goal, active: false, completed: true, pausedReason: null});
    assert.equal(loadGoal(dir)?.completed, true);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('invalid or missing goal files are ignored', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-goal-'));
  try {
    assert.equal(loadGoal(dir), null);
    await writeFile(goalFilePath(dir), '{"version":1,"objective":42', 'utf8');
    assert.equal(loadGoal(dir), null);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});
