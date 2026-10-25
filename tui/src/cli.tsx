#!/usr/bin/env node
import React from 'react';
import {spawn} from 'node:child_process';
import {constants} from 'node:os';
import {render} from 'ink';
import {App} from './app.js';
import {createBackendClient} from './backend.js';
import {validateClientArgs} from './protocol.js';
import {createTerminalInput} from './terminal-input.js';
import {backendCommand, backendLaunchOptions} from './backend-launch.js';

const help = `vibe Ink — 个人编码工作台

用法：./run_ink.sh [选项]

  --fake                  使用本地 fake provider，自动进入测试会话范围
  --workspace PATH        项目工作区
  --provider PROVIDER     openai | fake
  --model MODEL           模型名称
  --base-url URL          模型服务地址
  --max-steps NUMBER      单任务最大步数，0 表示不限
  --chat-only             只对话，不加载工具
  --continue              恢复当前工作区最近有对话的普通会话
  --session SESSION_ID    恢复当前工作区指定普通会话
  --list-sessions         只读列出当前工作区普通会话后退出（无需交互终端）
  --test-session          显式进入测试会话范围，恢复和列表也仅限测试会话
  --sandbox               用 macOS Seatbelt 沙箱执行命令（写入限工作区，默认断网）
  --sandbox-network       沙箱内允许网络（隐含 --sandbox）
  --help                  显示帮助

支持会话恢复、MCP 启动与调用、分范围审批和可靠取消。
普通使用不显示或加载测试会话；--fake 或 --provider fake 自动进入测试会话范围。
--continue 在当前范围内选择最近有对话的会话；查找更早会话用 --list-sessions，再用 --session SESSION_ID 打开。
恢复会话不会自动续跑；输入 /resume 继续任务。
Enter 发送任务；Ctrl+J 换行（终端支持时也可用 Shift+Enter）。
滚轮 / 触控板在对话区滚动；PageUp / PageDown 翻页；鼠标拖选文本，松开即复制。
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
  /sidebar                      打开右侧 Git 变更侧栏（Ghostty ≥ 1.3；Ctrl+G）
  /mcp                          查看 MCP 服务与工具状态
  /skills [refresh|activate NAME|unload NAME]  技能目录与生命周期
  /session                      查看当前会话路径
  /followup TEXT                运行中排队为后续消息，结束后自动继续
  /copy                         复制最近回复、工具详情或审批预览（Ctrl+Y）
  /project                     查看项目背景；/project add 文本、/project remove 序号、/project source 序号 来源
  /about                       查看个人偏好；/about add/propose/confirm/remove/on/off
  /theme system|light|dark       切换主题
  /quit 或 /exit                 退出工作台

交互入口为 ./run_ink.sh；Python 后端使用 ./run_vibe.sh --stdio，eval 使用 ./run_vibe.sh --eval。
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
  if (checkedArgs.includes('--list-sessions')) {
    process.exitCode = await new Promise<number>((resolve, reject) => {
      const child = spawn(backendCommand, checkedArgs, {
        ...backendLaunchOptions(), stdio: ['ignore', 'inherit', 'inherit'],
      });
      child.once('error', reject);
      child.once('close', (code, signal) => resolve(code ?? (signal ? 128 + constants.signals[signal] : 1)));
    });
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
