import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import path from 'node:path';

/** Ghostty 1.3+ 的 AppleScript 入口；后续 macOS Terminal 在此扩展。 */
export const SIDEBAR_TITLE = 'mini-harness sidebar';
export const SIDEBAR_COMMAND = 'osascript';

export type TerminalHost = 'ghostty' | 'terminal' | 'unknown';
export type LaunchOutcome = 'started' | 'toggled' | 'opened' | 'unsupported' | 'misconfigured' | 'failed';
export type LaunchResult = {outcome: LaunchOutcome; message: string; command?: string};

export type RunResult = {status: number | null; stdout: string; stderr: string; error?: string};
export type Runner = (command: string, args: string[], options: {timeoutMs: number}) => RunResult;

export type OpenSidebarOptions = {
  workspace: string;
  cliPath: string;
  nodePath?: string;
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  run?: Runner;
  timeoutMs?: number;
  /** 测试注入；显式 null 表示跳过 quick terminal 配置预检。 */
  ghosttyBinary?: string | null;
};

export type QuickTerminalCheck = {checked: boolean; ok: boolean; position: string | null; binary: string | null};

export function detectHost(env: NodeJS.ProcessEnv = process.env): TerminalHost {
  const program = (env.TERM_PROGRAM ?? '').toLowerCase();
  if (program === 'ghostty' || env.GHOSTTY_RESOURCES_DIR) return 'ghostty';
  if (program === 'apple_terminal') return 'terminal';
  return 'unknown';
}

/** POSIX shell 单引号转义；只用于注入到 quick terminal 的启动命令。 */
export function shellQuote(value: string): string {
  if (/^[\w@%+=:,./-]+$/u.test(value)) return value;
  return `'${value.replace(/'/g, `'\\''`)}'`;
}

export function sidebarCommand(options: {workspace: string; cliPath: string; nodePath?: string}): string {
  const node = options.nodePath ?? 'node';
  return `cd ${shellQuote(options.workspace)} && exec ${shellQuote(node)} ${shellQuote(options.cliPath)} --workspace ${shellQuote(options.workspace)}`;
}

/** 找到 Ghostty 命令行二进制；找不到时跳过配置预检，保持旧行为。 */
export function ghosttyBinary(env: NodeJS.ProcessEnv = process.env, exists: (target: string) => boolean = existsSync): string | null {
  const candidates: string[] = [];
  if (env.GHOSTTY_BIN_DIR) candidates.push(path.join(env.GHOSTTY_BIN_DIR, 'ghostty'));
  if (env.GHOSTTY_RESOURCES_DIR) candidates.push(path.resolve(env.GHOSTTY_RESOURCES_DIR, '..', 'MacOS', 'ghostty'));
  candidates.push('/Applications/Ghostty.app/Contents/MacOS/ghostty');
  for (const candidate of candidates) if (exists(candidate)) return candidate;
  return null;
}

/**
 * 预检 quick terminal 的实际配置位置。
 * `ghostty +show-config` 只读当前配置文件；默认 position 是 top，
 * 若未显式设为 right，侧栏就会停到屏幕顶部而不是右侧。
 */
export function checkQuickTerminalPosition(options: {
  env?: NodeJS.ProcessEnv;
  run?: Runner;
  binary?: string | null;
  exists?: (target: string) => boolean;
} = {}): QuickTerminalCheck {
  const binary = options.binary !== undefined ? options.binary : ghosttyBinary(options.env, options.exists);
  if (!binary) return {checked: false, ok: true, position: null, binary: null};
  const run = options.run ?? defaultRunner;
  const result = run(binary, ['+show-config'], {timeoutMs: 3000});
  if (result.error || result.status !== 0) return {checked: false, ok: true, position: null, binary};
  const match = /^\s*quick-terminal-position\s*=\s*(\S+)\s*$/mu.exec(result.stdout);
  const position = match?.[1] ?? 'top';
  return {checked: true, ok: position === 'right', position, binary};
}

function appleScriptString(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

/**
 * 打开或切换 Ghostty 右侧 quick terminal：
 * 1) 已存在标题匹配的侧栏终端时只切换显隐；
 * 2) 否则打开 quick terminal，等待新终端出现后注入启动命令。
 */
export function ghosttyScript(command: string): string {
  return `set sidebarTitle to "${SIDEBAR_TITLE}"
tell application "Ghostty"
  set sidebarHits to (every terminal whose name contains sidebarTitle)
  if (count of sidebarHits) > 0 then
    if not (perform action "toggle_quick_terminal" on (item 1 of sidebarHits)) then return "action-failed"
    return "toggled"
  end if
  set beforeIds to id of every terminal
  set target to focused terminal of selected tab of front window
  if not (perform action "toggle_quick_terminal" on target) then return "action-failed"
  set newTerminal to missing value
  repeat 15 times
    delay 0.2
    repeat with candidate in (every terminal)
      if not (beforeIds contains (id of candidate)) then
        set newTerminal to candidate
        exit repeat
      end if
    end repeat
    if newTerminal is not missing value then exit repeat
  end repeat
  if newTerminal is missing value then return "opened"
  delay 0.5
  input text "${appleScriptString(command)}" to newTerminal
  send key "enter" to newTerminal
  return "started"
end tell`;
}

function defaultRunner(command: string, args: string[], options: {timeoutMs: number}): RunResult {
  try {
    const result = spawnSync(command, args, {
      encoding: 'utf8',
      timeout: options.timeoutMs,
      maxBuffer: 1024 * 1024,
    });
    return {
      status: result.status,
      stdout: result.stdout ?? '',
      stderr: result.stderr ?? '',
      error: result.error ? result.error.message : undefined,
    };
  } catch (error) {
    return {status: null, stdout: '', stderr: '', error: error instanceof Error ? error.message : String(error)};
  }
}

function lastLine(text: string): string {
  return text.trim().split('\n').filter(Boolean).at(-1) ?? '';
}

function unsupported(message: string, command: string): LaunchResult {
  return {outcome: 'unsupported', message, command};
}

export function openSidebar(options: OpenSidebarOptions): LaunchResult {
  const command = sidebarCommand(options);
  const platform = options.platform ?? process.platform;
  if (platform !== 'darwin') return unsupported(`当前平台暂不支持自动打开侧栏；请手动运行：${command}`, command);
  const env = options.env ?? process.env;
  const host = detectHost(env);
  if (host === 'terminal') {
    return unsupported(`macOS Terminal 的自动侧栏尚未实现；请先手动运行：${command}`, command);
  }
  if (host !== 'ghostty') {
    return unsupported(`当前终端不支持自动打开侧栏（需要 Ghostty ≥ 1.3）；请手动运行：${command}`, command);
  }
  const run = options.run ?? defaultRunner;
  const quickTerminal = checkQuickTerminalPosition({env, run, binary: options.ghosttyBinary});
  if (quickTerminal.checked && !quickTerminal.ok) {
    const where = quickTerminal.position === 'top' ? '屏幕顶部' : `位置 ${quickTerminal.position ?? '未知'}`;
    return {
      outcome: 'misconfigured',
      command,
      message: `Ghostty quick terminal 当前在${where}（quick-terminal-position=${quickTerminal.position ?? '未配置，默认 top'}），侧栏不会出现在右侧。输入 /sidebar setup 写入右侧配置，然后完全退出并重启 Ghostty，再按 Ctrl+G。`,
    };
  }
  const result = run(SIDEBAR_COMMAND, ['-e', ghosttyScript(command)], {timeoutMs: options.timeoutMs ?? 10_000});
  if (result.error) return {outcome: 'failed', message: `打开侧栏失败：${result.error}`, command};
  if (result.status !== 0) {
    const stderr = result.stderr.trim();
    if (/-1743|not allowed|not authorized|Not authorized|automation/iu.test(stderr)) {
      return {
        outcome: 'failed',
        command,
        message: '打开侧栏需要 macOS 自动化权限：请在 系统设置 → 隐私与安全性 → 自动化 中允许当前终端控制 Ghostty，然后重试',
      };
    }
    return {outcome: 'failed', command, message: `打开侧栏失败：${lastLine(stderr) || `osascript 退出码 ${result.status ?? '未知'}`}`};
  }
  const marker = lastLine(result.stdout);
  switch (marker) {
    case 'started': return {outcome: 'started', message: '已打开右侧 Git 变更侧栏（Ghostty quick terminal）', command};
    case 'toggled': return {outcome: 'toggled', message: '已切换右侧 Git 变更侧栏', command};
    case 'opened': return {outcome: 'opened', message: `已打开 quick terminal；请在其中运行：${command}`, command};
    case 'action-failed': return {outcome: 'failed', command, message: 'Ghostty 无法切换 quick terminal；请确认版本 ≥ 1.3 且未设置 macos-applescript = false'};
    default: return {outcome: 'failed', command, message: `侧栏启动结果未知：${marker || '空输出'}`};
  }
}
