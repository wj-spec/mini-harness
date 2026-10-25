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
