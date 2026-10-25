import assert from 'node:assert/strict';
import test from 'node:test';
import {buildSkillMenu} from '../src/skills-menu.js';
import type {SkillInfo} from '../src/protocol.js';

const skills: SkillInfo[] = [
  {name: 'ponytail', summary: '精简实现', active: false},
  {name: 'tdd', summary: '先写测试', active: false},
  {name: 'old', summary: '旧技能', active: true},
];

test('skill menu activates inactive skills and unloads active ones', () => {
  const menu = buildSkillMenu('/skills', skills);
  assert.ok(menu);
  assert.deepEqual(menu.matches, ['/skills activate ponytail', '/skills activate tdd', '/skills unload old']);
  assert.match(menu.descriptions['/skills activate ponytail']!, /精简实现/);
  assert.match(menu.descriptions['/skills unload old']!, /已激活/);
});

test('skill menu filters by action and typed prefix', () => {
  assert.deepEqual(buildSkillMenu('/skills activate ', skills)?.matches, [
    '/skills activate ponytail', '/skills activate tdd', '/skills activate old',
  ]);
  assert.deepEqual(buildSkillMenu('/skills activate po', skills)?.matches, ['/skills activate ponytail']);
  assert.deepEqual(buildSkillMenu('/skills unload ', skills)?.matches, ['/skills unload old']);
  assert.equal(buildSkillMenu('/skills unload nope', skills), null);
});

test('skill menu stays out of unrelated slash commands and empty catalogs', () => {
  assert.equal(buildSkillMenu('/help', skills), null);
  assert.equal(buildSkillMenu('/skillsr', skills), null);
  assert.equal(buildSkillMenu('/skills', []), null);
});
