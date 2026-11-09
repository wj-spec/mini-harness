#!/usr/bin/env node
import React, {useEffect, useState} from 'react';
import {spawn} from 'node:child_process';
import {constants} from 'node:os';
import {render} from 'ink';
import {App} from './app.js';
import {createBackendClient, type AttachOptions, type BackendClient} from './backend.js';
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
  --attach-task TASK_ID   连接到运行中的后台 worker（需配合 --session）
  --list-sessions         只读列出当前工作区普通会话后退出（无需交互终端）
  --test-session          显式进入测试会话范围，恢复和列表也仅限测试会话
  --sandbox               用 macOS Seatbelt 沙箱执行命令（写入限工作区，默认断网）
  --sandbox-network       沙箱内允许网络（隐含 --sandbox）
  --sandbox-network-allow HOSTS  沙箱内只允许这些域名（逗号分隔，隐含 --sandbox）
  --autonomy LEVEL        授权档位：ask | edits | workspace | full
  --doctor                环境自检（检查 Node / Python / 依赖 / API Key，不需要先装前端依赖）
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
输入 / 按中文或前缀查找命令；↑↓ 选择，Enter 确认，Tab 只填充；缺少参数时先填写。
Tab 选择工具；Ctrl+O 查看工具详情；Escape 返回。
Ctrl+K 随时查看上下文分区；←/→ 回放本轮快照，l 跟随最新，Esc 返回。
审批时 Ctrl+R 进入审批；y 单次、s 精确规则、p 工作区、n 拒绝仅选择，Enter 确认；Esc 返回草稿。
具体选项由后端决定；MCP 启动与工具调用不提供工作区授权。
Ctrl+C 取消任务或 MCP 初始化，空闲时退出；Ctrl+Q 退出。

  /help                         查看操作帮助
  /cost                         查看会话累计 token 与费用
  /context                      查看估算分区、压缩趋势与块来源
  /tools                        查看可用工具
  /resume                       继续已保存的任务
  /permissions                  查看权限来源、有效期与剩余权限；面板内逐条撤销本进程授权
  /permissions reset            清除本进程授权（档位和持久策略不变）
  /evidence                     查看最近一轮验收证据（verified/审计/改动 hash）
  /autonomy [LEVEL]             查看或切换授权档位：ask|edits|workspace|full
  /diff                         查看实际 Git 改动（执行前审批）
  /sidebar                      在当前窗口右侧分屏，跟随窗口尺寸（Ctrl+G；占用 TUI 宽度）
  /sidebar quick                屏幕右侧 quick terminal（不挤占 TUI；需先 /sidebar setup）
  /sidebar window               独立侧栏窗口（Ghostty 需手动摆位；Terminal 会自动靠右）
  /sidebar setup                写入 Ghostty quick terminal 右侧配置（需重启 Ghostty）
  /mcp                          查看 MCP 服务与工具状态
  /skills [refresh|activate NAME|unload NAME]  技能菜单与生命周期（↑↓ 选择，Tab 补全）
  /goal <目标> | /goal stop | /goal resume   目标模式：长任务自动续段
  /session                      查看当前会话路径
  /sessions                     选择并预览旧会话，显式打开（打开不会自动运行）
  /followup [TEXT]              查看/编辑后续消息；成功后续发，失败暂停
  /copy                         复制最近回复、工具详情或审批预览（Ctrl+Y）
  /project                     查看项目背景；/project add 文本、/project remove 序号、/project source 序号 来源
  /about                       查看个人偏好；/about add/propose/observe/edit/triggers/confirm/disable/enable/remove/on/off
  /theme system|light|dark       切换主题
  /quit 或 /exit                 退出工作台

交互入口为 ./run_ink.sh；Python 后端使用 ./run_vibe.sh --stdio，eval 使用 ./run_vibe.sh --eval。
`;

function extractAttach(args: string[]): {args: string[]; attach?: AttachOptions; error?: string} {
  const index = args.indexOf('--attach-task');
  if (index < 0) return {args};
  const taskId = args[index + 1];
  if (!taskId || taskId.startsWith('--')) return {args: [], error: '--attach-task requires TASK_ID'};
  const rest = args.filter((_value, position) => position !== index && position !== index + 1);
  const sessionIndex = rest.indexOf('--session');
  const session = sessionIndex >= 0 ? rest[sessionIndex + 1] : undefined;
  if (!session || session.startsWith('--')) return {args: [], error: '--attach-task requires --session SESSION_ID'};
  return {args: rest, attach: {taskId, session}};
}

// 顶层 App 持有当前会话的后端；旧会话显式打开成功后替换客户端并重挂载工作台。
function Root({initial, attach, onClientChange}: {initial: BackendClient; attach: boolean; onClientChange: (client: BackendClient) => void}) {
  const [current, setCurrent] = useState({client: initial, generation: 0});
  useEffect(() => {onClientChange(current.client);}, [current.client, onClientChange]);
  return <App key={current.generation} client={current.client} attach={attach}
    onSessionSwitch={async next => {
      const previous = current.client;
      setCurrent({client: next, generation: current.generation + 1});
      try {await previous.shutdown();} catch {void 0;}
    }}/>;
}

async function main() {
  const args = process.argv.slice(2);
  if (args.includes('--help') || args.includes('-h')) {
    process.stdout.write(help);
    return;
  }
  const extracted = extractAttach(args);
  if (extracted.error) {
    process.stderr.write(`${extracted.error}\n`);
    process.exitCode = 2;
    return;
  }
  let checkedArgs: string[];
  try {
    checkedArgs = validateClientArgs(extracted.args);
  } catch (error) {
    process.stderr.write(`${(error as Error).message}\n`);
    process.exitCode = 2;
    return;
  }
  if (extracted.attach && checkedArgs.includes('--list-sessions')) {
    process.stderr.write('--attach-task cannot be combined with --list-sessions\n');
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
  // 终端读 EIO 时不要让 Node 抛未处理的 error；直接以明确信息退出。
  process.stdin.on('error', error => {
    if ((error as NodeJS.ErrnoException).code === 'EIO') {
      process.stderr.write('终端输入已断开（EIO）。\n');
      process.exit(2);
    }
  });
  try {
    // 某些伪终端/IDE 面板 isTTY=true 但 setRawMode 会 EIO；提前用 true/false
    // 完整探测一次，避免 Ink 启动后抛未处理的 'error' 事件。
    process.stdin.setRawMode?.(true);
    process.stdin.setRawMode?.(false);
  } catch {
    process.stderr.write('当前终端无法进入 raw mode（setRawMode EIO）。请在真实交互终端中运行 ./run_ink.sh。\n');
    process.exitCode = 2;
    return;
  }
  const client = createBackendClient({args: checkedArgs, attach: extracted.attach});
  const stdin = createTerminalInput(process.stdin);
  const currentClient = {client};
  let instance: ReturnType<typeof render>;
  try {
    instance = render(<Root initial={client} attach={Boolean(extracted.attach)} onClientChange={next => {currentClient.client = next;}}/>, {
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
    void currentClient.client.shutdown().finally(() => {
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
      await currentClient.client.shutdown();
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
