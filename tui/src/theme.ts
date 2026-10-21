export type ThemeName = 'system' | 'light' | 'dark';
export type SemanticTone = 'accent' | 'success' | 'warning' | 'error';
export type SurfaceName = 'task' | 'agent' | 'tool' | 'notice' | 'warning' | 'error' | 'selected' | 'code';
export type Palette = {
  background: string; foreground: string; secondary: string;
  surface: string; raised: string; focus: string;
  accent: string; border: string; success: string; warning: string; error: string;
  surfaces: Record<SurfaceName, string>;
};

const light: Palette = {
  background: '#FFFFFF', foreground: '#263449', secondary: '#526174',
  surface: '#F4F6F8', raised: '#E9EEF3', focus: '#E0EAF5',
  accent: '#174F91', border: '#CBD5E1', success: '#25663F', warning: '#835019', error: '#A42635',
  surfaces: {
    task: '#E7EDF4', agent: '#FFFFFF', tool: '#F0F3F6', notice: '#EDF3FA',
    warning: '#FBF0D9', error: '#FBECEF', selected: '#DCE9F8', code: '#E8EEF4',
  },
};
const dark: Palette = {
  background: '#101820', foreground: '#E2E8F0', secondary: '#B8C6D6',
  surface: '#19232F', raised: '#253444', focus: '#264965',
  accent: '#95D6F5', border: '#405267', success: '#8AD8A3', warning: '#F5CB7A', error: '#FFABB4',
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
