import {realpath, stat} from 'node:fs/promises';
import type {FileSource} from '../inspection.js';
import {runGitAsync} from './git.js';

export async function checkFileSource(source: FileSource | null): Promise<string | null> {
  if (!source) return '文件来源未知；不能用项目目录替代';
  try {
    const info = await stat(source.root, {bigint: true});
    if (!info.isDirectory() || `${info.dev}:${info.ino}` !== source.identity || await realpath(source.root) !== source.root) {
      return '来源目录已被替换；原结果仍可查看计划与验证';
    }
    const git = await runGitAsync(['rev-parse', '--show-toplevel'], {cwd: source.root});
    if (git.status !== 0 || await realpath(git.stdout.trim()) !== source.root) return '来源目录不再是原 Git 根；未回退到项目目录';
    return null;
  } catch {return '来源目录不可用；原结果仍可查看计划与验证';}
}
