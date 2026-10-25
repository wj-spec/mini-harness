#!/usr/bin/env node
import React from 'react';
import {render} from 'ink';
import {SIDEBAR_TITLE} from './host.js';
import {SidebarApp} from './app.js';
import {parseSidebarArgs, SIDEBAR_HELP, type SidebarOptions} from './args.js';
import {removeSidebarLink, watchParentPid, writeSidebarLink} from './lifecycle.js';

function setTitle(title: string): void {
  if (process.stdout.isTTY) process.stdout.write(`\u001b]2;${title}\u0007`);
}

async function main() {
  const args = process.argv.slice(2);
  if (args.includes('--help') || args.includes('-h')) {
    process.stdout.write(SIDEBAR_HELP);
    return;
  }
  let options: SidebarOptions;
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
  if (options.parentPid !== null) writeSidebarLink(options.workspace, {pid: process.pid, parentPid: options.parentPid});
  // 主工作台退出即联动收起侧栏；即使工作台崩溃，进程消失也会触发。
  const stopParentWatch = options.parentPid === null ? () => {} : watchParentPid(options.parentPid, () => instance?.unmount());
  const onSignal = () => {instance?.unmount();};
  process.once('SIGTERM', onSignal);
  process.once('SIGHUP', onSignal);
  try {
    await instance.waitUntilExit();
  } finally {
    stopParentWatch();
    process.off('SIGTERM', onSignal);
    process.off('SIGHUP', onSignal);
    setTitle('');
    if (options.parentPid !== null) removeSidebarLink(options.workspace, process.pid);
  }
}

try {
  await main();
} catch (error) {
  process.stderr.write(`变更侧栏失败：${(error as Error).message}\n`);
  process.exitCode = 1;
}
