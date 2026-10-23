import assert from 'node:assert/strict';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {loadProfile, projectProfilePath, saveProfile} from '../src/project-profile.js';

test('profile round-trips entries with sources and ignores broken files', async () => {
  const directory = await mkdtemp(path.join(tmpdir(), 'profile-'));
  const file = projectProfilePath(directory);
  try {
    assert.deepEqual(loadProfile(file), []);
    saveProfile(file, [
      {text: 'Ink 是唯一入口', source: 'AGENTS.md'},
      {text: '跑 npm test', source: 'user', updated_at: '2026-10-23T00:00:00Z'},
    ]);
    assert.deepEqual(loadProfile(file), [
      {text: 'Ink 是唯一入口', source: 'AGENTS.md'},
      {text: '跑 npm test', source: 'user', updated_at: '2026-10-23T00:00:00Z'},
    ]);
    const payload = JSON.parse(await readFile(file, 'utf8')) as {version: number};
    assert.equal(payload.version, 1);

    await import('node:fs/promises').then(fs => fs.writeFile(file, 'not json', 'utf8'));
    assert.deepEqual(loadProfile(file), []);
    await import('node:fs/promises').then(fs => fs.writeFile(file, JSON.stringify({version: 2, entries: []}), 'utf8'));
    assert.deepEqual(loadProfile(file), []);
  } finally {
    await rm(directory, {recursive: true, force: true});
  }
});
