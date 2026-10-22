import chalk, {type ColorSupportLevel} from 'chalk';

if (process.env.NO_COLOR && process.env.FORCE_COLOR === undefined) chalk.level = 0;

/** 与 Ink 共用 Chalk 的能力检测，不能只提升调色板的颜色等级。 */
export function terminalColorLevel(): ColorSupportLevel {
  return chalk.level;
}
