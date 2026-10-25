import type {ThemeName} from '../theme.js';

export type SidebarOptions = {
  workspace: string;
  theme: ThemeName;
  refreshMs: number;
  parentPid: number | null;
};

export const SIDEBAR_HELP = `mini-harness 变更侧栏

用法：./run_sidebar.sh [选项]

  --workspace PATH   要显示的 Git 工作区（默认当前目录）
  --theme NAME       system | light | dark（默认 system）
  --refresh-ms N     轮询间隔毫秒，0 表示只在启动和按键时刷新（默认 1000）
  --parent-pid N     主工作台进程号；工作台退出后侧栏自动退出（由工作台传入）
  --help             显示帮助

只读：侧栏只执行固定参数的 git status / git diff，不写工作区、不联网、不经过 shell。
通常由工作台用 /sidebar 或 Ctrl+G 在 Ghostty 右侧 quick terminal 打开；也可直接运行本命令。
快捷键：↑↓/jk 选择文件，Tab 切换列表/diff 焦点，PgUp/PgDn 翻页，f 跟随，r 刷新，q 退出。
`;

export function parseSidebarArgs(args: readonly string[], cwd = process.cwd()): SidebarOptions {
  const options: SidebarOptions = {workspace: cwd, theme: 'system', refreshMs: 1000, parentPid: null};
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index]!;
    if (arg === '--workspace') {
      const value = args[index + 1];
      if (!value) throw new Error('--workspace 需要路径');
      options.workspace = value;
      index += 1;
      continue;
    }
    if (arg === '--theme') {
      const value = args[index + 1];
      if (value !== 'system' && value !== 'light' && value !== 'dark') throw new Error('--theme 只接受 system、light 或 dark');
      options.theme = value;
      index += 1;
      continue;
    }
    if (arg === '--refresh-ms') {
      const value = Number(args[index + 1]);
      if (!Number.isSafeInteger(value) || value < 0) throw new Error('--refresh-ms 需要非负整数');
      options.refreshMs = value;
      index += 1;
      continue;
    }
    if (arg === '--parent-pid') {
      const value = Number(args[index + 1]);
      if (!Number.isSafeInteger(value) || value <= 0) throw new Error('--parent-pid 需要正整数');
      options.parentPid = value;
      index += 1;
      continue;
    }
    throw new Error(`未知参数：${arg}`);
  }
  return options;
}
