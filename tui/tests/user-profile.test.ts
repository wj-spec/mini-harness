import assert from 'node:assert/strict';
import {mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {loadUserProfile, saveUserProfile, userProfileFile} from '../src/user-profile.js';

test('user profile round-trips confirmed and proposed entries', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'user-profile-'));
  const file = path.join(directory, 'user-profile.json');
  try {
    assert.deepEqual(loadUserProfile(file), {enabled: true, entries: []});
    saveUserProfile(file, {
      enabled: true,
      entries: [
        {text: '回答先给结论', source: 'user', status: 'confirmed'},
        {text: '可能偏好简短回复', source: 'observation', status: 'proposed', updated_at: '2026-10-23T00:00:00Z'},
      ],
    });
    const loaded = loadUserProfile(file);
    assert.equal(loaded.enabled, true);
    assert.deepEqual(loaded.entries.map(entry => [entry.text, entry.source, entry.status]), [
      ['回答先给结论', 'user', 'confirmed'],
      ['可能偏好简短回复', 'observation', 'proposed'],
    ]);
    const payload = JSON.parse(await readFile(file, 'utf8')) as {version: number; enabled: boolean};
    assert.equal(payload.version, 1);
    assert.equal(payload.enabled, true);
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});

test('broken or wrong-version user profiles are empty and enabled', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'user-profile-'));
  const file = path.join(directory, 'user-profile.json');
  try {
    await writeFile(file, 'not json', 'utf8');
    assert.deepEqual(loadUserProfile(file), {enabled: true, entries: []});
    await writeFile(file, JSON.stringify({version: 2, enabled: false, entries: []}), 'utf8');
    assert.deepEqual(loadUserProfile(file), {enabled: true, entries: []});
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});

test('default user profile path honors VIBE_USER_PROFILE', () => {
  const previous = process.env.VIBE_USER_PROFILE;
  process.env.VIBE_USER_PROFILE = '/tmp/example-profile.json';
  try {
    assert.equal(userProfileFile(), '/tmp/example-profile.json');
  } finally {
    if (previous === undefined) delete process.env.VIBE_USER_PROFILE;
    else process.env.VIBE_USER_PROFILE = previous;
  }
});
