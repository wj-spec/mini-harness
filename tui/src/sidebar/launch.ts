import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {closeSidebarWindow, openSidebar, type LaunchResult, type SidebarMode, type TerminalHost} from './host.js';
import {killLinkedSidebar} from './lifecycle.js';
import {adjustSidebarSplit} from './split-ratio.js';

/** 侧栏入口与主 TUI 同目录构建：dist/src/sidebar/cli.js。 */
export function sidebarCliPath(argv1: string | undefined = process.argv[1]): string {
  const base = argv1 ? path.dirname(argv1) : process.cwd();
  return path.resolve(base, 'sidebar', 'cli.js');
}

export function launchSidebarForTui(workspace: string, mode: SidebarMode = 'split', totalColumns?: number): LaunchResult {
  return openSidebar({workspace, cliPath: sidebarCliPath(), nodePath: process.execPath, mode, parentPid: process.pid, totalColumns});
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

/** 工作台退出时尽量立即终止同一工作区、同一父进程启动的侧栏。 */
export function killSidebarForTui(workspace: string): boolean {
  return killLinkedSidebar(workspace);
}

/** split 打开后把比例从 Ghostty 默认 50/50 调整到约 70/30。后台执行，不阻塞 TUI。 */
export function adjustSidebarSplitForTui(workspace: string, totalColumns: number): void {
  void adjustSidebarSplit({workspace, totalColumns}).catch(() => {});
}

/** 退出工作台时关闭独立窗口模式留下的窗口（split/quick 不需要）。 */
export function closeSidebarWindowForTui(host: TerminalHost, windowId: string): boolean {
  return closeSidebarWindow(host, windowId);
}
