#!/usr/bin/env node
import React from 'react';
import {render} from 'ink';
import {sidebarTitle} from './host.js';
import {SidebarApp} from './app.js';
import {parseSidebarArgs, SIDEBAR_HELP, type SidebarOptions} from './args.js';
import {isProcessAlive, readSidebarLink, removeSidebarLink, sidebarLinkPath, watchParentPid, writeSidebarLink} from './lifecycle.js';
import {createTerminalInput} from '../terminal-input.js';
import {claimSidebarLease} from './lease.js';

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
  const lease = options.parentPid === null ? null : claimSidebarLease(JSON.stringify([
    'instance', sidebarLinkPath(options.workspace, options.owner),
  ]));
  if (options.parentPid !== null && !lease) {
    process.stderr.write('此工作台已有侧栏或正在启动；请回到原侧栏。\n'); process.exitCode = 2; return;
  }
  try {
    const previous = readSidebarLink(options.workspace, options.owner);
    if (options.parentPid !== null && (!isProcessAlive(options.parentPid) || previous && isProcessAlive(previous.pid))) {
      process.stderr.write(previous && isProcessAlive(previous.pid) ? '此工作台已有侧栏；请回到原侧栏。\n' : '主工作台已退出，未启动侧栏。\n');
      process.exitCode = 2; return;
    }
    await runSidebar(options);
  } finally {lease?.release();}
}

async function runSidebar(options: SidebarOptions) {
  const stdin = createTerminalInput(process.stdin);
  let hasSelection = false;
  let updateLink = () => {};
  let instance: ReturnType<typeof render> | undefined;
  let stopParentWatch = () => {};
  const onSignal = () => {instance?.unmount();};
  try {
    setTitle(sidebarTitle(options.owner, options.hostMode));
    instance = render(<SidebarApp workspace={options.workspace} owner={options.owner} theme={options.theme} refreshMs={options.refreshMs}
      onSelectionChange={selected => {hasSelection = selected; updateLink();}}
      onQuit={() => instance?.unmount()}/>, {
      exitOnCtrlC: false, patchConsole: false, interactive: true, alternateScreen: true,
      stdin,
    });
    // 登记父进程与当前终端列宽；工作台据列宽把 split 调成 70/30。
    updateLink = () => {
      if (options.parentPid === null) return;
      writeSidebarLink(options.workspace, {
        pid: process.pid, parentPid: options.parentPid,
        owner: options.owner, hasSelection,
        columns: process.stdout.columns, rows: process.stdout.rows,
      });
    };
    updateLink();
    process.stdout.on('resize', updateLink);
    // 主工作台退出即联动收起侧栏；即使工作台崩溃，进程消失也会触发。
    stopParentWatch = options.parentPid === null ? () => {} : watchParentPid(options.parentPid, () => {instance?.unmount();});
    process.once('SIGTERM', onSignal);
    process.once('SIGHUP', onSignal);
    await instance.waitUntilExit();
  } finally {
    instance?.unmount();
    stopParentWatch();
    stdin.dispose();
    process.stdout.off('resize', updateLink);
    process.off('SIGTERM', onSignal);
    process.off('SIGHUP', onSignal);
    setTitle('');
    if (options.parentPid !== null) removeSidebarLink(options.workspace, process.pid, options.owner);
  }
}

try {
  await main();
} catch (error) {
  process.stderr.write(`变更侧栏失败：${(error as Error).message}\n`);
  process.exitCode = 1;
}
