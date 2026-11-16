import assert from 'node:assert/strict';
import {test} from 'node:test';
import {demoArguments} from '../src/demo-launch.js';

test('demo arguments stay fake and isolated regardless of the launch flags', () => {
  assert.deepEqual(demoArguments(['--workspace', '/repo']), ['--workspace', '/repo', '--fake', '--test-session']);
});

test('demo drops real-model and session flags with their values', () => {
  assert.deepEqual(
    demoArguments(['--workspace', '/repo', '--session', 's-1', '--continue', '--provider', 'openai',
      '--model', 'x', '--base-url', 'https://example.com/v1', '--autonomy', 'ask', '--fake']),
    ['--workspace', '/repo', '--autonomy', 'ask', '--fake', '--test-session']);
});

test('demo never leaves a stale session identity', () => {
  assert.deepEqual(demoArguments(['--session-identity', 'a'.repeat(64)]),
    ['--fake', '--test-session']);
});
