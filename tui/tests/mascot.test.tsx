import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {renderToString} from 'ink';
import stringWidth from 'string-width';
import {PixelMascot, WIDE_MASCOT, COMPACT_MASCOT} from '../src/mascot.js';
import {themes} from '../src/theme.js';

function plain(output: string): string[] {
  return output.replace(/\x1b\[[0-9;]*m/gu, '').split('\n');
}

test('wide mascot renders ten pixel columns in five terminal rows', () => {
  const lines = plain(renderToString(<PixelMascot sprite={WIDE_MASCOT} palette={themes.system}/>, {columns: 10}));
  assert.equal(lines.length, 5);
  assert.ok(lines.every(line => stringWidth(line) <= 10));
  assert.match(lines.join(''), /[▀▄█]/u);
});

test('compact mascot fits the three-row header', () => {
  const lines = plain(renderToString(<PixelMascot sprite={COMPACT_MASCOT} palette={themes.system}/>, {columns: 8}));
  assert.equal(lines.length, 3);
  assert.ok(lines.every(line => stringWidth(line) <= 8));
});
