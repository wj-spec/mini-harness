#!/usr/bin/env node
import React from 'react';
import {render} from 'ink';
import {SIDEBAR_TITLE} from './host.js';
import {SidebarApp} from './app.js';
import type {ThemeName} from '../theme.js';

const help = `mini-harness 变更侧栏

用法：./run_sidebar.sh [选项]

  --workspace PATH   要显示的 Git 工作区（默认当前目录）
  --theme NAME       system | light | dark（默认 system）
  --refresh-ms N     轮询间隔毫秒，0 表示只在启动和按键时刷新（默认 1000）
  --help             显示帮助

只读：侧栏只执行固定参数的 git status / git diff，不写工作区、不联网、不经过 shell。
通常由工作台用 /sidebar 或 Ctrl+G 在 Ghostty 右侧 quick terminal 打开；也可直接运行本命令。
快捷键：↑↓/jk 选择文件，Tab 切换列表/diff 焦点，PgUp/PgDn 翻页，f 跟随，r 刷新，q 退出。
`;

type Options = {workspace: string; theme: ThemeName; refreshMs: number};

export function parseSidebarArgs(args: readonly string[]): Options {
  const options: Options = {workspace: process.cwd(), theme: 'system', refreshMs: 1000};
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
    throw new Error(`未知参数：${arg}`);
  }
  return options;
}

function setTitle(title: string): void {
  if (process.stdout.isTTY) process.stdout.write(`\u001b]2;${title}\u0007`);
}

async function main() {
  const args = process.argv.slice(2);
  if (args.includes('--help') || args.includes('-h')) {
    process.stdout.write(help);
    return;
  }
  let options: Options;
  try {
    options = parseSidebarArgs(args);
  } catch (error) {
    process.stderr.write(`${(error as Error).message}\n`);
    process.exitCode = 2;
    return;
  }
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    process.stderr.write('变更侧栏需要交互式终端。参数说明：./run_sidebar.sh --help\n');
    process.exitCode = 2;
    return;
  }
  setTitle(SIDEBAR_TITLE);
  let instance: ReturnType<typeof render> | undefined;
  instance = render(<SidebarApp workspace={options.workspace} theme={options.theme} refreshMs={options.refreshMs}
    onQuit={() => instance?.unmount()}/>, {
    exitOnCtrlC: false, patchConsole: false, interactive: true, alternateScreen: true,
  });
  const onSignal = () => {instance?.unmount();};
  process.once('SIGTERM', onSignal);
  process.once('SIGHUP', onSignal);
  try {
    await instance.waitUntilExit();
  } finally {
    process.off('SIGTERM', onSignal);
    process.off('SIGHUP', onSignal);
    setTitle('');
  }
}

try {
  await main();
} catch (error) {
  process.stderr.write(`变更侧栏失败：${(error as Error).message}\n`);
  process.exitCode = 1;
}
