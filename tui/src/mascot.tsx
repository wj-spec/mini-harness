import React from 'react';
import {Box, Text} from 'ink';
import type {Palette} from './theme.js';

const BODY = '#E8A15A';
const INK = '#2A2320';
const CREAM = '#F5E6CF';
const NOSE = '#D96C6C';

/** 12x10 pixel cat head; two sprite rows share one terminal cell via ▀. */
export const WIDE_MASCOT = [
  '..B......B..',
  '..BB....BB..',
  '..BBBBBBBB..',
  '.BBBBBBBBBB.',
  '.BDBBBBBBDB.',
  '.BBBBBBBBBB.',
  '.BBBBNNBBBB.',
  'LBBBBLLBBBBL',
  '.BBBBBBBBBB.',
  '..BBBBBBBB..',
] as const;

/** 8x6 compact cat face for the three-row header. */
export const COMPACT_MASCOT = [
  '..B..B..',
  '.BBBBBB.',
  '.BDBBDB.',
  '.BBBBBB.',
  '.BBNNBB.',
  'LBBBBBBL',
] as const;

function color(cell: string): string | null {
  if (cell === 'B') return BODY;
  if (cell === 'D') return INK;
  if (cell === 'L') return CREAM;
  if (cell === 'N') return NOSE;
  return null;
}

export function PixelMascot({sprite, palette}: {sprite: readonly string[]; palette: Palette}) {
  const rows: React.ReactNode[] = [];
  for (let y = 0; y < sprite.length; y += 2) {
    const top = sprite[y] ?? '';
    const bottom = sprite[y + 1] ?? '';
    const width = Math.max(top.length, bottom.length);
    const cells: React.ReactNode[] = [];
    for (let x = 0; x < width; x += 1) {
      const upper = color(top[x] ?? '.');
      const lower = color(bottom[x] ?? '.');
      if (!upper && !lower) {
        cells.push(<Text key={x} backgroundColor={palette.background}> </Text>);
        continue;
      }
      const cell = upper && lower ? '▀' : upper ? '▀' : '▄';
      cells.push(<Text key={x} color={upper ?? lower ?? palette.background}
        backgroundColor={lower ?? palette.background}>{cell}</Text>);
    }
    rows.push(<Text key={y}>{cells}</Text>);
  }
  return <Box flexDirection="column">{rows}</Box>;
}
