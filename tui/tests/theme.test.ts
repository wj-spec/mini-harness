import assert from 'node:assert/strict';
import {test} from 'node:test';
import {getPalette, resolveSystemTheme, themes} from '../src/theme.js';

function indexedHex(color: string): string {
  const index = Number(color.match(/^ansi256\((\d+)\)$/u)?.[1]);
  assert.ok(index >= 16 && index <= 255, color);
  const value = index - 16;
  const levels = [0, 95, 135, 175, 215, 255];
  const channels = index >= 232 ? Array(3).fill(8 + (index - 232) * 10) : [levels[Math.floor(value / 36)], levels[Math.floor(value / 6) % 6], levels[value % 6]];
  return '#' + channels.map(channel => channel!.toString(16).padStart(2, '0')).join('');
}

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

test('256-color profiles pair actual indexed foregrounds with readable gray surfaces', () => {
  for (const name of ['light', 'dark'] as const) {
    const palette = getPalette(name, 2);
    const backgrounds = [palette.background, palette.surface, palette.raised, palette.focus, ...Object.values(palette.surfaces)];
    for (const foreground of [palette.foreground, palette.secondary, palette.accent, palette.success, palette.warning, palette.error]) {
      for (const background of backgrounds) {
        const values = [luminance(indexedHex(foreground)), luminance(indexedHex(background))].sort((a, b) => b - a);
        assert.ok((values[0]! + 0.05) / (values[1]! + 0.05) >= 4.5, `${name} ${foreground} on ${background}`);
      }
    }
  }
  assert.equal(getPalette('light', 3), themes.light);
  const focusIndex = Number(getPalette('light', 2).focus.match(/^ansi256\((\d+)\)$/u)?.[1]);
  assert.ok(focusIndex >= 232 && focusIndex <= 255, 'large editor surface uses neutral grayscale in limited color terminals');
  assert.equal(getPalette('light', 1).foreground, 'black');
  assert.notEqual(getPalette('light', 1).focus, getPalette('light', 1).background);
});

test('context partitions have distinct readable colors in light, dark, and limited profiles', () => {
  for (const name of ['light', 'dark'] as const) for (const level of [1, 2, 3] as const) {
    const palette = getPalette(name, level);
    const colors = ['system', 'memory', 'tools', 'history'].map(section => palette.contextColors[section as keyof typeof palette.contextColors]);
    assert.equal(new Set(colors).size, 4);
    assert.ok(!colors.includes(palette.contextColors.remaining));
    assert.notEqual(palette.contextColors.remaining, palette.background);
    assert.notEqual(palette.contextColors.remaining, palette.surface);
    if (level === 1) continue;
    for (const foreground of colors) for (const background of [palette.background, palette.surface]) {
      const values = [foreground, background].map(color => luminance(level === 2 ? indexedHex(color) : color)).sort((a, b) => b - a);
      assert.ok((values[0]! + 0.05) / (values[1]! + 0.05) >= 4.5, `${name}/${level}: ${foreground} on ${background}`);
    }
  }
});
