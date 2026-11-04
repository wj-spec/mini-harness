import assert from 'node:assert/strict';
import test from 'node:test';
import {backendSpawnArgs} from '../src/backend.js';

test('backend spawns stdio by default and attach mode with task/session', () => {
  assert.deepEqual(backendSpawnArgs(['--fake']), ['--stdio', '--fake']);
  assert.deepEqual(backendSpawnArgs(['--fake', '--workspace', '/repo'], {taskId: 't-1', session: 's-1'}), [
    '--attach', '--task-id', 't-1', '--session', 's-1', '--mode', 'control',
    '--fake', '--workspace', '/repo',
  ]);
});
