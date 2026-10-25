import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {openSidebar, type LaunchResult} from './host.js';

/** 侧栏入口与主 TUI 同目录构建：dist/src/sidebar/cli.js。 */
export function sidebarCliPath(argv1: string | undefined = process.argv[1]): string {
  const base = argv1 ? path.dirname(argv1) : process.cwd();
  return path.resolve(base, 'sidebar', 'cli.js');
}

export function launchSidebarForTui(workspace: string): LaunchResult {
  return openSidebar({workspace, cliPath: sidebarCliPath(), nodePath: process.execPath});
}

/** dist/src/cli.js → 仓库根目录。 */
export function repoRootFromCli(argv1: string | undefined = process.argv[1]): string {
  const base = argv1 ? path.dirname(argv1) : process.cwd();
  return path.resolve(base, '..', '..', '..');
}

export function sidebarSetupPath(argv1: string | undefined = process.argv[1]): string {
  return path.resolve(repoRootFromCli(argv1), 'scripts', 'setup-ghostty-sidebar.sh');
}

export type SetupResult = {ok: boolean; message: string};

/** 由 /sidebar setup 显式调用；写入 Ghostty 配置，属于用户主动操作。 */
export function runSidebarSetup(argv1: string | undefined = process.argv[1]): SetupResult {
  const script = sidebarSetupPath(argv1);
  if (!existsSync(script)) return {ok: false, message: `未找到 Ghostty 配置脚本：${script}`};
  const result = spawnSync('bash', [script], {encoding: 'utf8', timeout: 5000});
  const output = `${result.stdout ?? ''}${result.stderr ?? ''}`.trim();
  if (result.error) return {ok: false, message: `运行配置脚本失败：${result.error.message}`};
  const lines = output.split('\n').filter(Boolean);
  return {
    ok: result.status === 0,
    message: lines.slice(-3).join('\n') || (result.status === 0 ? '已写入 Ghostty 配置；请完全重启 Ghostty。' : `配置脚本退出码 ${result.status ?? '未知'}`),
  };
}
