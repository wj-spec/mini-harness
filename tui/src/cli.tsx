#!/usr/bin/env node
import React from 'react';
import {render} from 'ink';
import {App} from './app.js';
import {createBackendClient} from './backend.js';
import {validateClientArgs} from './protocol.js';
import {createTerminalInput} from './terminal-input.js';

const help = `vibe Ink — 个人编码工作台

用法：./run_ink.sh [选项]

  --fake                  使用本地 fake provider
  --workspace PATH        项目工作区
  --provider PROVIDER     openai | fake
  --model MODEL           模型名称
  --base-url URL          模型服务地址
  --max-steps NUMBER      单任务最大步数，0 表示不限
  --chat-only             只对话，不加载工具
  --continue              恢复当前工作区最近有对话的会话
  --help                  显示帮助

支持会话恢复、MCP 启动与调用、分范围审批和可靠取消。
--continue 仅恢复对话，不会自动续跑；输入 /resume 继续任务。
Enter 发送任务；Ctrl+J 换行（终端支持时也可用 Shift+Enter）。
滚轮 / 触控板在对话区滚动；PageUp / PageDown 翻页。
Ctrl+R 切换阅读与输入；阅读时 ↑↓ 滚动，Esc 回输入。
Ctrl+P / Ctrl+N 逐行；Ctrl+Home / Ctrl+End 到首尾并跟随最新。
终端支持时，按住 Shift 拖选文字。
Tab 选择工具（输入 / 时补全命令）；Ctrl+O 查看工具详情；Escape 返回。
Ctrl+K 随时查看上下文分区；←/→ 回放本轮快照，l 跟随最新，Esc 返回。
审批时 y 允许一次，s 记住精确规则（当前进程），p 工作区授权，n 拒绝。
具体选项由后端决定；MCP 启动与工具调用不提供工作区授权。
Ctrl+C 取消任务或 MCP 初始化，空闲时退出；Ctrl+Q 退出。

  /help                         查看操作帮助
  /cost                         查看会话累计 token 与费用
  /context                      查看估算分区、压缩趋势与块来源
  /tools                        查看可用工具
  /resume                       继续已保存的任务
  /permissions                  查看当前授权规则
  /permissions reset            清除当前进程授权规则
  /diff                         查看实际 Git 改动（执行前审批）
  /mcp                          查看 MCP 服务与工具状态
  /theme system|light|dark       切换主题
  /quit                         退出工作台

默认 ./run_vibe.sh 继续使用 Textual，直到 Ink 的完成度与体验超越 Textual。
eval 继续使用 ./run_vibe.sh --eval。
`;

async function main() {
  const args = process.argv.slice(2);
  if (args.includes('--help') || args.includes('-h')) {
    process.stdout.write(help);
    return;
  }
  let checkedArgs: string[];
  try {
    checkedArgs = validateClientArgs(args);
  } catch (error) {
    process.stderr.write(`${(error as Error).message}\n`);
    process.exitCode = 2;
    return;
  }
  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    process.stderr.write('Ink 入口需要交互式终端。参数说明：./run_ink.sh --help\n');
    process.exitCode = 2;
    return;
  }
  const client = createBackendClient({args: checkedArgs});
  const stdin = createTerminalInput(process.stdin);
  let instance: ReturnType<typeof render>;
  try {
    instance = render(<App client={client}/>, {
      stdin, exitOnCtrlC: false, patchConsole: false, interactive: true, alternateScreen: true,
    });
  } catch (error) {
    stdin.dispose();
    throw error;
  }
  let ending = false;
  const onSignal = (signal: NodeJS.Signals) => {
    if (ending) return;
    ending = true;
    void client.shutdown().finally(() => {
      instance.unmount();
      process.exitCode = signal === 'SIGINT' ? 130 : signal === 'SIGTERM' ? 143 : 129;
    });
  };
  const onTerm = () => onSignal('SIGTERM');
  const onHup = () => onSignal('SIGHUP');
  const onInt = () => onSignal('SIGINT');
  process.on('SIGTERM', onTerm);
  process.on('SIGHUP', onHup);
  process.on('SIGINT', onInt);
  try {
    client.start();
    await instance.waitUntilExit();
  } finally {
    try {
      await client.shutdown();
    } finally {
      process.off('SIGTERM', onTerm);
      process.off('SIGHUP', onHup);
      process.off('SIGINT', onInt);
      try {instance.unmount();} finally {stdin.dispose();}
    }
  }
}

try {
  await main();
} catch (error) {
  process.stderr.write(`Ink 入口失败：${(error as Error).message}\n`);
  process.exitCode = 1;
}
