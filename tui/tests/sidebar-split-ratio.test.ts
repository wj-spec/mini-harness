import assert from 'node:assert/strict';
import test from 'node:test';
import type {SidebarLink} from '../src/sidebar/lifecycle.js';
import {adjustSidebarSplit, targetSidebarColumns} from '../src/sidebar/split-ratio.js';

test('target columns keep the sidebar at roughly 30% of the window', () => {
  assert.equal(targetSidebarColumns(170), 51);
  assert.equal(targetSidebarColumns(154), 46);
  assert.equal(targetSidebarColumns(10, 0.5), 5);
});

test('split adjustment calibrates pixels per column and converges near 70/30', async () => {
  let columns = 85;
  const resizes: [string, number][] = [];
  const readLink = (): SidebarLink => ({pid: 1, parentPid: process.pid, columns});
  const resize = (direction: 'left' | 'right', pixels: number) => {
    resizes.push([direction, pixels]);
    const delta = Math.max(1, Math.round(pixels / 8));
    columns = Math.max(5, Math.min(160, columns + (direction === 'right' ? -delta : delta)));
    return true;
  };
  const result = await adjustSidebarSplit({workspace: '/repo', totalColumns: 170, readLink, resize, wait: async () => {}});
  assert.ok(result);
  assert.ok(Math.abs(columns - targetSidebarColumns(170)) <= 1, `columns=${columns}`);
  assert.ok(resizes.every(([direction]) => direction === 'right'), 'sidebar must shrink from 50% to 30%');
});

test('split adjustment stops early when already at 70/30', async () => {
  let resizes = 0;
  await adjustSidebarSplit({
    workspace: '/repo', totalColumns: 170,
    readLink: () => ({pid: 1, parentPid: process.pid, columns: targetSidebarColumns(170)}),
    resize: () => {resizes += 1; return true;},
    wait: async () => {},
  });
  assert.equal(resizes, 0);
});

test('split adjustment gives up when the sidebar never reports its width', async () => {
  const result = await adjustSidebarSplit({
    workspace: '/repo', totalColumns: 170, timeoutMs: 30,
    readLink: () => null,
    resize: () => {throw new Error('must not resize');},
    wait: () => new Promise(resolve => setTimeout(resolve, 2)),
  });
  assert.equal(result, null);
});
