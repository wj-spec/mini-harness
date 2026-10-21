export type ThemeName = 'system' | 'light' | 'dark';
export type SemanticTone = 'accent' | 'success' | 'warning' | 'error';
export const themes = {
  system: {accent: 'blueBright', border: undefined, secondary: undefined, success: 'green', warning: 'yellow', error: 'red'},
  light: {accent: 'blue', border: 'blue', secondary: undefined, success: 'green', warning: 'magenta', error: 'red'},
  dark: {accent: 'cyanBright', border: 'blueBright', secondary: undefined, success: 'greenBright', warning: 'yellowBright', error: 'redBright'},
} as const;
export type Palette = (typeof themes)[ThemeName];
