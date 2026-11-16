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

test('wide mascot renders sixteen pixel columns in five terminal rows', () => {
  const lines = plain(renderToString(<PixelMascot sprite={WIDE_MASCOT} palette={themes.system}/>, {columns: 16}));
  assert.equal(lines.length, 5);
  assert.ok(lines.every(line => stringWidth(line) <= 16));
  assert.match(lines.join(''), /[▀▄█]/u);
});

test('compact mascot fits the three-row header', () => {
  const lines = plain(renderToString(<PixelMascot sprite={COMPACT_MASCOT} palette={themes.system}/>, {columns: 10}));
  assert.equal(lines.length, 3);
  assert.ok(lines.every(line => stringWidth(line) <= 10));
});


test('both mascot sizes preserve their header footprint across themes', () => {
  for (const palette of [themes.light, themes.dark]) {
    for (const [sprite, width, height] of [[WIDE_MASCOT, 16, 5], [COMPACT_MASCOT, 10, 3]] as const) {
      assert.equal(sprite.length, height * 2);
      assert.ok(sprite.every(row => row.length === width));
      const lines = plain(renderToString(<PixelMascot sprite={sprite} palette={palette}/>, {columns: width}));
      assert.equal(lines.length, height);
      assert.ok(lines.every(line => stringWidth(line) <= width));
    }
  }
});

test('transparent half pixels use the theme background on either side', () => {
  for (const palette of [themes.light, themes.dark]) {
    const output = PixelMascot({sprite: ['.B', 'B.'], palette});
    const row = output.props.children[0];
    const [lowerOnly, upperOnly] = row.props.children;
    assert.equal(lowerOnly.props.children, '▄');
    assert.equal(upperOnly.props.children, '▀');
    assert.equal(lowerOnly.props.backgroundColor, palette.background);
    assert.equal(upperOnly.props.backgroundColor, palette.background);
  }
});
