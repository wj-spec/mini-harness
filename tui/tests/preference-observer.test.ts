import assert from 'node:assert/strict';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {tmpdir} from 'node:os';
import test from 'node:test';
import {proposePreferences, readSessionUserMessages} from '../src/preference-observer.js';
import type {UserProfile} from '../src/user-profile.js';

const profile: UserProfile = {enabled: true, entries: [{text: '已有偏好', source: 'user', status: 'confirmed'}]};

test('only explicit long-term user cues become proposed preferences', () => {
  const proposals = proposePreferences(profile, [
    {text: '以后解释代码时先给结论，再给细节。'},
    {text: '今天修一个 bug。'},
    {text: '请记住：测试失败时先复现，再改代码。'},
    {text: '以后解释代码时先给结论，再给细节。'},
  ], 'session-1');
  assert.equal(proposals.length, 2);
  assert.equal(proposals[0]?.status, 'proposed');
  assert.equal(proposals[0]?.source, 'observation');
  assert.deepEqual(proposals[0]?.evidence, ['session:session-1']);
  assert.ok(proposals[0]?.triggers?.includes('解释'));
  assert.ok(proposals[0]?.triggers?.includes('结论'));
  assert.ok(proposals[1]?.triggers?.includes('测试'));
});

test('existing preference text is not proposed twice', () => {
  const existing = {...profile, entries: [{text: '以后解释代码时先给结论', source: 'user', status: 'proposed' as const}]};
  assert.deepEqual(proposePreferences(existing, [{text: '以后解释代码时先给结论'}], 's'), []);
});

test('session reader only returns user messages and tolerates a missing trace', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'preference-observer-'));
  try {
    await writeFile(path.join(dir, 'events.jsonl'), [
      JSON.stringify({type: 'message', message: {role: 'user', content: '以后用中文回答'}}),
      JSON.stringify({type: 'message', message: {role: 'assistant', content: '好的'}}),
      'not json',
    ].join('\n'), 'utf8');
    assert.deepEqual(readSessionUserMessages(dir), [{text: '以后用中文回答', index: 0}]);
    assert.deepEqual(readSessionUserMessages(path.join(dir, 'missing')), []);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});
