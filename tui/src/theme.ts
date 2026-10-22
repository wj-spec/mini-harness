import {terminalColorLevel} from './terminal-colors.js';
import type {ContextSection} from './protocol.js';

export type ThemeName = 'system' | 'light' | 'dark';
export type SemanticTone = 'accent' | 'success' | 'warning' | 'error';
export type SurfaceName = 'task' | 'agent' | 'tool' | 'notice' | 'warning' | 'error' | 'selected' | 'code';
export type ContextColor = ContextSection | 'remaining';
export type Palette = {
  background: string; foreground: string; secondary: string;
  surface: string; raised: string; focus: string;
  accent: string; border: string; success: string; warning: string; error: string;
  surfaces: Record<SurfaceName, string>;
  contextColors: Record<ContextColor, string>;
};

const light: Palette = {
  background: '#FFFFFF', foreground: '#263449', secondary: '#526174',
  surface: '#F4F6F8', raised: '#E9EEF3', focus: '#E0EAF5',
  accent: '#174F91', border: '#CBD5E1', success: '#25663F', warning: '#835019', error: '#A42635',
  contextColors: {system: '#00718C', memory: '#85389C', tools: '#805500', history: '#1D7041', remaining: '#94A3B8'},
  surfaces: {
    task: '#E7EDF4', agent: '#FFFFFF', tool: '#F0F3F6', notice: '#EDF3FA',
    warning: '#FBF0D9', error: '#FBECEF', selected: '#DCE9F8', code: '#E8EEF4',
  },
};
const dark: Palette = {
  background: '#101820', foreground: '#E2E8F0', secondary: '#B8C6D6',
  surface: '#19232F', raised: '#253444', focus: '#264965',
  accent: '#95D6F5', border: '#405267', success: '#8AD8A3', warning: '#F5CB7A', error: '#FFABB4',
  contextColors: {system: '#6DD9F3', memory: '#D5A0FF', tools: '#F2C56B', history: '#83D19E', remaining: '#627286'},
  surfaces: {
    task: '#253444', agent: '#101820', tool: '#17212D', notice: '#182B3C',
    warning: '#342A18', error: '#38232B', selected: '#294B68', code: '#111C28',
  },
};

export function resolveSystemTheme(colorFgBg?: string): Exclude<ThemeName, 'system'> {
  const background = Number.parseInt(colorFgBg?.split(';').at(-1) ?? '', 10);
  return background === 7 || background === 15 ? 'light' : 'dark';
}

export const themes = {
  system: resolveSystemTheme(process.env.COLORFGBG) === 'light' ? light : dark,
  light,
  dark,
};
const indexed = (index: number) => `ansi256(${index})`;
const light256: Palette = {
  background: indexed(231), foreground: indexed(237), secondary: indexed(240),
  surface: indexed(255), raised: indexed(254), focus: indexed(253),
  accent: indexed(25), border: indexed(250), success: indexed(22), warning: indexed(58), error: indexed(124),
  contextColors: {system: indexed(23), memory: indexed(90), tools: indexed(58), history: indexed(22), remaining: indexed(247)},
  surfaces: {
    task: indexed(254), agent: indexed(231), tool: indexed(255), notice: indexed(255),
    warning: indexed(230), error: indexed(224), selected: indexed(189), code: indexed(254),
  },
};
const dark256: Palette = {
  background: indexed(233), foreground: indexed(255), secondary: indexed(252),
  surface: indexed(234), raised: indexed(236), focus: indexed(237),
  accent: indexed(117), border: indexed(240), success: indexed(115), warning: indexed(222), error: indexed(217),
  contextColors: {system: indexed(81), memory: indexed(183), tools: indexed(222), history: indexed(115), remaining: indexed(243)},
  surfaces: {
    task: indexed(236), agent: indexed(233), tool: indexed(234), notice: indexed(235),
    warning: indexed(235), error: indexed(52), selected: indexed(237), code: indexed(233),
  },
};
const light16: Palette = {
  background: 'whiteBright', foreground: 'black', secondary: 'black',
  surface: 'white', raised: 'white', focus: 'white',
  accent: 'blue', border: 'black', success: 'green', warning: 'black', error: 'red',
  contextColors: {system: 'cyan', memory: 'magenta', tools: 'yellow', history: 'green', remaining: 'blackBright'},
  surfaces: {
    task: 'white', agent: 'whiteBright', tool: 'white', notice: 'white',
    warning: 'white', error: 'white', selected: 'white', code: 'white',
  },
};
const dark16: Palette = {
  background: 'black', foreground: 'whiteBright', secondary: 'whiteBright',
  surface: 'blackBright', raised: 'blackBright', focus: 'blackBright',
  accent: 'cyanBright', border: 'white', success: 'greenBright', warning: 'yellowBright', error: 'redBright',
  contextColors: {system: 'cyanBright', memory: 'magentaBright', tools: 'yellowBright', history: 'greenBright', remaining: 'white'},
  surfaces: {
    task: 'blackBright', agent: 'black', tool: 'blackBright', notice: 'blackBright',
    warning: 'blackBright', error: 'blackBright', selected: 'blackBright', code: 'blackBright',
  },
};

export function getPalette(theme: ThemeName, level = terminalColorLevel()): Palette {
  const name = theme === 'system' ? resolveSystemTheme(process.env.COLORFGBG) : theme;
  if (level === 2) return name === 'light' ? light256 : dark256;
  if (level === 1) return name === 'light' ? light16 : dark16;
  return themes[name];
}
