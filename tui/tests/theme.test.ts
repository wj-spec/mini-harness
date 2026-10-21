import assert from 'node:assert/strict';
import {test} from 'node:test';
import {resolveSystemTheme, themes} from '../src/theme.js';

function luminance(hex: string): number {
  const values = hex.slice(1).match(/../g)!.map(value => {
    const channel = Number.parseInt(value, 16) / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return values[0]! * 0.2126 + values[1]! * 0.7152 + values[2]! * 0.0722;
}

test('light and dark surfaces keep body, secondary and semantic text readable', () => {
  for (const name of ['light', 'dark'] as const) {
    const palette = themes[name];
    const backgrounds = [palette.background, palette.surface, palette.raised, palette.focus, ...Object.values(palette.surfaces)];
    for (const foreground of [palette.foreground, palette.secondary, palette.accent, palette.success, palette.warning, palette.error]) {
      for (const background of backgrounds) {
        const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
        assert.ok((values[0]! + 0.05) / (values[1]! + 0.05) >= 4.5, `${name} ${foreground} on ${background}`);
      }
    }
  }
});

test('system theme uses known terminal background hints and a defined fallback', () => {
  assert.equal(resolveSystemTheme('0;15'), 'light');
  assert.equal(resolveSystemTheme('15;0'), 'dark');
  assert.equal(resolveSystemTheme('0;7'), 'light');
  assert.equal(resolveSystemTheme(undefined), 'dark');
  assert.equal(resolveSystemTheme('invalid'), 'dark');
});
