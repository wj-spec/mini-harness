import assert from 'node:assert/strict';
import {test} from 'node:test';
import {parseAttachCommand} from '../src/attach-command.js';

test('/attach now does not treat now as a task id', () => {
  assert.deepEqual(parseAttachCommand('/attach now', 't-1'), {taskId: 't-1', now: true});
  assert.deepEqual(parseAttachCommand('/attach --now', 't-1'), {taskId: 't-1', now: true});
  assert.deepEqual(parseAttachCommand('/attach t-2', 't-1'), {taskId: 't-2', now: false});
  assert.deepEqual(parseAttachCommand('/attach', null), {taskId: null, now: false});
  assert.deepEqual(parseAttachCommand('/attach now', null), {taskId: null, now: true});
});
