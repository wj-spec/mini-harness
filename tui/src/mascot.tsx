import React from 'react';
import {Box, Text} from 'ink';
import type {Palette} from './theme.js';

export const MASCOT_COLORS: Readonly<Record<string, string>> = {
  B: '#DDA24F', // 蜂蜜金
  S: '#95612F', // 毛尖
  D: '#382B22', // 眼缘
  L: '#F5DFB2', // 口鼻、下巴
  N: '#BC7770', // 鼻子
  G: '#91A56B', // 虹膜
};

/** 卡通金渐层胖脸：16×10 像素，半格渲染为 5 行。 */
export const WIDE_MASCOT = [
  '..BB........BB..',
  '.SBLBBBSSBBBLBS.',
  '.SBBBBBBBBBBBBS.',
  'BBBBBBBBBBBBBBBB',
  'BBBBDDBBBBDDBBBB',
  'BBBBDGBBBBGDBBBB',
  'BBBBLLLNNLLLBBBB',
  'BBBBLLLLLLLLBBBB',
  '.BBBLLLLLLLLBBB.',
  '..BBBBBBBBBBBB..',
] as const;

/** 紧凑版胖脸：10×6 像素，保留小五官和饱满脸颊。 */
export const COMPACT_MASCOT = [
  '.BB....BB.',
  '.BSBBBBSB.',
  'BBBDBBDBBB',
  'BBBBNNBBBB',
  'BBLLLLLLBB',
  '.BBBBBBBB.',
] as const;

function color(cell: string): string | null {
  return MASCOT_COLORS[cell] ?? null;
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
      const cell = upper ? '▀' : '▄';
      cells.push(<Text key={x} color={upper ?? lower ?? palette.background}
        backgroundColor={upper && lower ? lower : palette.background}>{cell}</Text>);
    }
    rows.push(<Text key={y}>{cells}</Text>);
  }
  return <Box flexDirection="column">{rows}</Box>;
}
