import assert from 'node:assert/strict';
import test from 'node:test';
import {parseSidebarArgs} from '../src/sidebar/args.js';

test('owned sidebar arguments require a valid instance and parent pair', () => {
  const instanceId = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa';
  assert.deepEqual(parseSidebarArgs(['--instance-id', instanceId, '--parent-pid', '4242', '--host-mode', 'split'], '/repo'), {
    workspace: '/repo', theme: 'system', refreshMs: 1000, parentPid: 4242,
    owner: {instanceId, parentPid: 4242}, hostMode: 'split',
  });
  assert.throws(() => parseSidebarArgs(['--instance-id', instanceId]), /parent-pid/u);
  assert.throws(() => parseSidebarArgs(['--instance-id', '../outside', '--parent-pid', '4242']), /instance-id/u);
  assert.throws(() => parseSidebarArgs(['--host-mode', 'bad']), /host-mode/u);
});

test('sidebar args default to cwd, system theme and 1s refresh', () => {
  assert.deepEqual(parseSidebarArgs([], '/repo'), {workspace: '/repo', theme: 'system', refreshMs: 1000, parentPid: null});
});

test('sidebar args parse workspace, theme, refresh and parent pid', () => {
  assert.deepEqual(
    parseSidebarArgs(['--workspace', '/repo', '--theme', 'dark', '--refresh-ms', '500', '--parent-pid', '4242'], '/other'),
    {workspace: '/repo', theme: 'dark', refreshMs: 500, parentPid: 4242},
  );
  assert.equal(parseSidebarArgs(['--refresh-ms', '0'], '/repo').refreshMs, 0);
});

test('sidebar args reject invalid values', () => {
  assert.throws(() => parseSidebarArgs(['--workspace']), /--workspace/u);
  assert.throws(() => parseSidebarArgs(['--theme', 'blue']), /--theme/u);
  assert.throws(() => parseSidebarArgs(['--refresh-ms', '-1']), /--refresh-ms/u);
  assert.throws(() => parseSidebarArgs(['--parent-pid', '0']), /--parent-pid/u);
  assert.throws(() => parseSidebarArgs(['--nope']), /未知参数/u);
});
