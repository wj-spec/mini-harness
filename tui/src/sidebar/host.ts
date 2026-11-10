import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {validOwner, type SidebarOwner} from './channel.js';
import {claimSidebarLease} from './lease.js';

/** Ghostty 1.3+ 与 macOS Terminal 的 AppleScript 入口。 */
export const SIDEBAR_TITLE = 'mini-harness sidebar';
export const SIDEBAR_COMMAND = 'osascript';

export type TerminalHost = 'ghostty' | 'terminal' | 'unknown';
export type SidebarMode = 'quick' | 'split' | 'window';
export type LaunchOutcome = 'started' | 'toggled' | 'focused' | 'opened' | 'unsupported' | 'misconfigured' | 'failed';
export type LaunchResult = {outcome: LaunchOutcome; message: string; command?: string; host?: TerminalHost; windowId?: string};

export type RunResult = {status: number | null; stdout: string; stderr: string; error?: string};
export type Runner = (command: string, args: string[], options: {timeoutMs: number}) => RunResult;

export type OpenSidebarOptions = {
  owner?: SidebarOwner;
  sourceTty?: string;
  workspace: string;
  cliPath: string;
  nodePath?: string;
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  run?: Runner;
  timeoutMs?: number;
  mode?: SidebarMode;
  /** split 前主 TUI 的总列数，用于首次 resize 估算。 */
  totalColumns?: number;
  /** 主 TUI 进程号；侧栏用它做生命周期联动。 */
  parentPid?: number;
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

export function sidebarTitle(owner?: SidebarOwner, mode?: SidebarMode): string {
  if (owner && !validOwner(owner)) throw new Error('Invalid sidebar owner');
  return `${SIDEBAR_TITLE}${owner ? ` [${owner.instanceId}:${owner.parentPid}]` : ''}${mode ? ` [${mode}]` : ''}`;
}

export function sidebarCommand(options: {workspace: string; cliPath: string; nodePath?: string; parentPid?: number; owner?: SidebarOwner; mode?: SidebarMode}): string {
  const node = options.nodePath ?? 'node';
  if (options.owner) sidebarTitle(options.owner);
  const parentPid = options.owner?.parentPid ?? options.parentPid;
  const parent = parentPid ? ` --parent-pid ${parentPid}` : '';
  const instance = options.owner ? ` --instance-id ${options.owner.instanceId}` : '';
  const mode = options.mode ? ` --host-mode ${options.mode}` : '';
  return `cd ${shellQuote(options.workspace)} && exec ${shellQuote(node)} ${shellQuote(options.cliPath)} --workspace ${shellQuote(options.workspace)}${parent}${instance}${mode}`;
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

function sourceTerminal(owner?: SidebarOwner, sourceTty?: string): string {
  if (!owner) return '  set target to focused terminal of selected tab of front window';
  const fallback = sourceTty && /^\/dev\/tty[\w]+$/u.test(sourceTty)
    ? `\n  if (count of sourceHits) is not 1 then set sourceHits to (every terminal whose tty is "${sourceTty}")` : '';
  return `  set sourceHits to (every terminal whose pid is ${owner.parentPid})${fallback}
  if (count of sourceHits) is not 1 then return "source-missing"
  set target to item 1 of sourceHits`;
}

/**
 * 打开或切换 Ghostty 右侧 quick terminal：
 * 1) 已存在标题匹配的侧栏终端时只切换显隐；
 * 2) 否则打开 quick terminal，等待新终端出现后注入启动命令。
 */
export function ghosttyScript(command: string, owner?: SidebarOwner, sourceTty?: string): string {
  const ownedQuick = owner ? `    if name of (item 1 of sidebarHits) does not end with "[quick]" then
      focus (item 1 of sidebarHits)
      return "focused"
    end if\n` : '';
  const conflict = owner ? `  set quickHits to (every terminal whose name contains "${SIDEBAR_TITLE}" and name ends with "[quick]")
  set legacyHits to (every terminal whose name is "${SIDEBAR_TITLE}")
  if (count of quickHits) > 0 or (count of legacyHits) > 0 then return "occupied"\n` : '';
  return `set sidebarTitle to "${sidebarTitle(owner)}"
tell application "Ghostty"
  set sidebarHits to (every terminal whose name contains sidebarTitle)
  if (count of sidebarHits) > 0 then
${ownedQuick}    if not (perform action "toggle_quick_terminal" on (item 1 of sidebarHits)) then return "action-failed"
    return "toggled"
  end if
${conflict}${sourceTerminal(owner, sourceTty)}
  set beforeIds to id of every terminal
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
${owner ? `  set name override of newTerminal to "${sidebarTitle(owner, 'quick')}"\n` : ''}  delay 0.5
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

/**
 * 在同一个 Ghostty 窗口内右侧分屏。split 属于窗口布局，会跟随窗口尺寸；
 * 代价是主 TUI 的可视宽度减少。
 */
export function splitScript(command: string, workspace: string, initialPixels = 0, owner?: SidebarOwner, sourceTty?: string): string {
  const initial = Math.max(0, Math.round(initialPixels));
  const initialResize = initial > 0
    ? `  delay 0.05\n  perform action "resize_split:right,${initial}" on newTerminal\n`
    : '';
  return `set sidebarTitle to "${sidebarTitle(owner)}"
tell application "Ghostty"
  set sidebarHits to (every terminal whose name contains sidebarTitle)
  if (count of sidebarHits) > 0 then
    focus (item 1 of sidebarHits)
    return "focused"
  end if
  set cfg to new surface configuration
  set initial working directory of cfg to "${appleScriptString(workspace)}"
${sourceTerminal(owner, sourceTty)}
  set newTerminal to split target direction right with configuration cfg
${owner ? `  set name override of newTerminal to "${sidebarTitle(owner, 'split')}"\n` : ''}${initialResize}  delay 0.6
  input text "${appleScriptString(command)}" to newTerminal
  send key "enter" to newTerminal
  focus target
  return "started"
end tell`;
}

export function focusSidebarScript(pid: number): string {
  const target = Math.max(1, Math.round(pid));
  return `tell application "Ghostty"
  set hits to (every terminal whose pid is ${target})
  if (count of hits) is 0 then return "missing"
  focus (item 1 of hits)
  return "focused"
end tell`;
}

/** 标题覆盖失效时按登记的侧栏进程号聚焦，避免重复分屏。仅在 Ghostty 下可用。 */
export function focusSidebarTerminal(pid: number, options: {
  env?: NodeJS.ProcessEnv; platform?: NodeJS.Platform; run?: Runner; timeoutMs?: number;
} = {}): boolean {
  const platform = options.platform ?? process.platform;
  if (platform !== 'darwin' || detectHost(options.env ?? process.env) !== 'ghostty') return false;
  const run = options.run ?? defaultRunner;
  const result = run(SIDEBAR_COMMAND, ['-e', focusSidebarScript(pid)], {timeoutMs: options.timeoutMs ?? 1500});
  return result.status === 0 && lastLine(result.stdout) === 'focused';
}

export function resizeSplitScript(direction: 'left' | 'right', pixels: number, owner?: SidebarOwner): string {
  const amount = Math.max(1, Math.round(pixels));
  return `tell application "Ghostty"
  set sidebarHits to (every terminal whose name contains "${sidebarTitle(owner, owner ? 'split' : undefined)}")
  if (count of sidebarHits) is not 1 then return "missing"
  if not (perform action "resize_split:${direction},${amount}" on (item 1 of sidebarHits)) then return "action-failed"
  return "ok"
end tell`;
}

/** 调整 split 分隔条，把侧栏缩到约 30% / 主 TUI 70%。 */
export function resizeSidebarSplit(direction: 'left' | 'right', pixels: number, options: {
  owner?: SidebarOwner;
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  run?: Runner;
  timeoutMs?: number;
} = {}): boolean {
  const platform = options.platform ?? process.platform;
  const env = options.env ?? process.env;
  if (platform !== 'darwin' || detectHost(env) !== 'ghostty') return false;
  const run = options.run ?? defaultRunner;
  const result = run(SIDEBAR_COMMAND, ['-e', resizeSplitScript(direction, pixels, options.owner)], {timeoutMs: options.timeoutMs ?? 3000});
  return result.status === 0 && lastLine(result.stdout) === 'ok';
}

/** Ghostty 独立窗口模式：新建窗口运行侧栏，窗口位置由用户手动摆放。 */
export function ghosttyWindowScript(command: string, workspace: string, owner?: SidebarOwner): string {
  return `set sidebarTitle to "${sidebarTitle(owner)}"
tell application "Ghostty"
  set sidebarHits to (every terminal whose name contains sidebarTitle)
  if (count of sidebarHits) > 0 then
    focus (item 1 of sidebarHits)
    return "focused"
  end if
  set cfg to new surface configuration
  set initial working directory of cfg to "${appleScriptString(workspace)}"
  set initial input of cfg to "${appleScriptString(command)}" & return
  set newWin to new window with configuration cfg
${owner ? `  set name override of focused terminal of selected tab of newWin to "${sidebarTitle(owner, 'window')}"\n` : ''}  return "started:" & ((id of newWin) as text)
end tell`;
}

/** macOS Terminal 独立窗口：新建窗口并尽量放在当前 TUI 窗口右侧、同高。 */
export function terminalWindowScript(command: string, owner?: SidebarOwner): string {
  return `set sidebarTitle to "${sidebarTitle(owner)}"
tell application "Terminal"
  activate
  set sidebarHits to (every window whose name contains sidebarTitle)
  if (count of sidebarHits) > 0 then
    set index of (item 1 of sidebarHits) to 1
    return "focused"
  end if
  set tuiWindow to window 1
  set tuiBounds to bounds of window 1
  set leftX to item 1 of tuiBounds
  set topY to item 2 of tuiBounds
  set rightX to item 3 of tuiBounds
  set bottomY to item 4 of tuiBounds
  set sidebarWidth to ((rightX - leftX) * 0.3) as integer
  if sidebarWidth < 320 then set sidebarWidth to 320
  do script ":"
  set newWindow to window 1
  if (id of newWindow) = (id of tuiWindow) then return "unexpected-tab"
${owner ? `  set custom title of selected tab of newWindow to "${sidebarTitle(owner, 'window')}"\n` : ''}  set bounds of newWindow to {rightX, topY, rightX + sidebarWidth, bottomY}
  do script "${appleScriptString(command)}" in newWindow
  set index of tuiWindow to 1
  return "started:" & ((id of newWindow) as text)
end tell`;
}

/** 关闭独立窗口模式留下的窗口；split/quick 不需要。 */
export function closeWindowScript(host: TerminalHost, windowId: string, owner?: SidebarOwner): string | null {
  const ownership = owner ? ` and name contains "${sidebarTitle(owner, 'window')}"` : '';
  if (host === 'terminal' && /^\d+$/u.test(windowId)) {
    return `tell application "Terminal"
  set matches to (every window whose id is ${windowId}${ownership})
  if (count of matches) is not 1 then return "missing"
  set targetWindow to item 1 of matches
${owner ? '  if (count of tabs of targetWindow) is not 1 then return "expanded"\n' : ''}  close targetWindow
  return "closed"
end tell`;
  }
  if (host === 'ghostty') {
    return `tell application "Ghostty"
  set matches to (every window whose id is "${appleScriptString(windowId)}"${ownership})
  if (count of matches) is not 1 then return "missing"
  set targetWindow to item 1 of matches
${owner ? '  if (count of tabs of targetWindow) is not 1 or (count of terminals of targetWindow) is not 1 then return "expanded"\n' : ''}  close targetWindow
  return "closed"
end tell`;
  }
  return null;
}

export function closeSidebarWindow(host: TerminalHost, windowId: string, options: {
  owner?: SidebarOwner;
  env?: NodeJS.ProcessEnv; platform?: NodeJS.Platform; run?: Runner; timeoutMs?: number;
} = {}): boolean {
  const platform = options.platform ?? process.platform;
  const env = options.env ?? process.env;
  if (platform !== 'darwin' || detectHost(env) === 'unknown') return false;
  const script = closeWindowScript(host, windowId, options.owner);
  if (!script) return false;
  const run = options.run ?? defaultRunner;
  const result = run(SIDEBAR_COMMAND, ['-e', script], {timeoutMs: options.timeoutMs ?? 3000});
  return result.status === 0 && lastLine(result.stdout) === 'closed';
}

function launchFailure(result: RunResult, command: string): LaunchResult | null {
  if (result.error) return {outcome: 'failed', message: `打开侧栏失败：${result.error}`, command};
  if (result.status === 0) return null;
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

function mapWindowMarker(marker: string, host: TerminalHost, command: string): LaunchResult {
  const started = /^started:(.+)$/u.exec(marker);
  if (started) {
    return {
      outcome: 'started', command, host, windowId: started[1],
      message: host === 'terminal'
        ? '已在 Terminal 中打开独立侧栏窗口（已放到右侧、与 TUI 同高；可拖动调整）'
        : '已打开 Ghostty 侧栏窗口（不会自动摆位，请拖到窗口右侧）',
    };
  }
  if (marker === 'focused') return {outcome: 'focused', command, host, message: '已聚焦侧栏窗口'};
  if (marker === 'unexpected-tab') {
    return {outcome: 'failed', command, message: 'Terminal 未能创建独立侧栏窗口（命令被开在新标签页）；请确认 Terminal 的“新建窗口”设置或手动运行命令。'};
  }
  return {outcome: 'failed', command, message: `侧栏窗口启动结果未知：${marker || '空输出'}`};
}

function mapQuickMarker(marker: string, command: string): LaunchResult {
  switch (marker) {
    case 'started': return {outcome: 'started', message: '已打开右侧 Git 变更侧栏（Ghostty quick terminal；不跟随窗口尺寸，需要跟随用 /sidebar split 或直接用 /sidebar 默认分屏）', command};
    case 'toggled': return {outcome: 'toggled', message: '已切换右侧 quick terminal 侧栏', command};
    case 'opened': return {outcome: 'opened', message: `已打开 quick terminal；请在其中运行：${command}`, command};
    case 'focused': return {outcome: 'focused', command, message: '已聚焦此工作台的侧栏'};
    case 'source-missing': return {outcome: 'failed', command, message: '未找到此工作台的来源终端；请在原终端重试，或使用 /sidebar window'};
    case 'occupied': return {outcome: 'failed', command, message: 'quick terminal 已被另一侧栏占用；请使用 /sidebar split 或 /sidebar window'};
    case 'action-failed': return {outcome: 'failed', command, message: 'Ghostty 无法切换 quick terminal；请确认版本 ≥ 1.3 且未设置 macos-applescript = false'};
    default: return {outcome: 'failed', command, message: `侧栏启动结果未知：${marker || '空输出'}`};
  }
}

function mapSplitMarker(marker: string, command: string): LaunchResult {
  switch (marker) {
    case 'started': return {outcome: 'started', message: '已在当前窗口右侧打开 Git 变更侧栏（split，会跟随窗口尺寸；TUI 可用宽度会减少）', command};
    case 'focused': return {outcome: 'focused', message: '已聚焦右侧 Git 变更侧栏', command};
    case 'source-missing': return {outcome: 'failed', command, message: '未找到此工作台的来源终端；请在原终端重试，或使用 /sidebar window'};
    default: return {outcome: 'failed', command, message: `侧栏启动结果未知：${marker || '空输出'}`};
  }
}

function parseFontSize(output: string): number | null {
  const match = /^\s*font-size\s*=\s*([\d.]+)\s*$/mu.exec(output);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isFinite(value) && value > 0 ? value : null;
}

/** 读取 Ghostty 字号，用于估算 split 初始 resize 的像素量（默认 13pt）。 */
export function readFontSize(run: Runner, env: NodeJS.ProcessEnv = process.env, binary?: string | null): number {
  const ghostty = binary === null ? null : binary ?? ghosttyBinary(env);
  if (ghostty) {
    const effective = run(ghostty, ['+show-config'], {timeoutMs: 1000});
    const value = effective.status === 0 ? parseFontSize(effective.stdout) : null;
    if (value) return value;
    const defaults = run(ghostty, ['+show-config', '--default'], {timeoutMs: 1500});
    const fallback = defaults.status === 0 ? parseFontSize(defaults.stdout) : null;
    if (fallback) return fallback;
  }
  return 13;
}

export function openSidebar(options: OpenSidebarOptions): LaunchResult {
  const host = detectHost(options.env ?? process.env);
  if (!options.owner || (options.platform ?? process.platform) !== 'darwin' || host === 'unknown') return launchSidebar(options);
  const lease = claimSidebarLease(JSON.stringify(['launch', path.resolve(options.workspace), options.owner.instanceId, options.owner.parentPid]));
  if (!lease) return {outcome: 'failed', host, message: '此工作台正在打开侧栏；请等待后再重试'};
  try {return launchSidebar(options);} finally {lease.release();}
}

function launchSidebar(options: OpenSidebarOptions): LaunchResult {
  const mode: SidebarMode = options.mode ?? 'quick';
  const host = detectHost(options.env ?? process.env);
  const command = sidebarCommand({...options, mode: host === 'terminal' ? 'window' : mode});
  const platform = options.platform ?? process.platform;
  if (platform !== 'darwin') return unsupported(`当前平台暂不支持自动打开侧栏；请手动运行：${command}`, command);
  const env = options.env ?? process.env;
  if (host === 'unknown') {
    return unsupported(`当前终端不支持自动打开侧栏（Ghostty 或 macOS Terminal）；请手动运行：${command}`, command);
  }
  const run = options.run ?? defaultRunner;
  const timeoutMs = options.timeoutMs ?? 10_000;
  // Terminal 没有 split/quick：统一用独立窗口，并尽量放到右侧。
  if (mode === 'window' || host === 'terminal') {
    const script = host === 'terminal' ? terminalWindowScript(command, options.owner) : ghosttyWindowScript(command, options.workspace, options.owner);
    const result = run(SIDEBAR_COMMAND, ['-e', script], {timeoutMs});
    const failure = launchFailure(result, command);
    if (failure) {
      if (host === 'terminal' && /-10000|AppleEvent handler failed/iu.test(result.stderr)) {
        return {outcome: 'failed', command, message: 'Terminal 窗口创建失败（AppleScript -10000）；请更新到最新代码重试，或手动运行 ./run_sidebar.sh 后把窗口拖到右侧。'};
      }
      return failure;
    }
    return mapWindowMarker(lastLine(result.stdout), host, command);
  }
  if (mode === 'split') {
    const initialPixels = options.totalColumns
      ? Math.max(0, Math.round(options.totalColumns * 0.2 * readFontSize(run, env, options.ghosttyBinary) * 0.6))
      : 0;
    const result = run(SIDEBAR_COMMAND, ['-e', splitScript(command, options.workspace, initialPixels, options.owner, options.sourceTty)], {timeoutMs});
    return {...(launchFailure(result, command) ?? mapSplitMarker(lastLine(result.stdout), command)), host};
  }
  const quickTerminal = checkQuickTerminalPosition({env, run, binary: options.ghosttyBinary});
  if (quickTerminal.checked && !quickTerminal.ok) {
    const where = quickTerminal.position === 'top' ? '屏幕顶部' : `位置 ${quickTerminal.position ?? '未知'}`;
    return {
      outcome: 'misconfigured',
      command,
      message: `Ghostty quick terminal 当前在${where}（quick-terminal-position=${quickTerminal.position ?? '未配置，默认 top'}），quick 侧栏不会出现在右侧。输入 /sidebar setup 写入右侧配置，然后完全退出并重启 Ghostty，再执行 /sidebar quick；如果只是想让侧栏跟随窗口尺寸，直接用默认的 /sidebar（窗口内分屏，无需配置）。`,
    };
  }
  const lease = claimSidebarLease('ghostty-quick-launch');
  if (!lease) return {outcome: 'failed', command, host, message: '另一个工作台正在打开 quick terminal；请稍后重试或使用 /sidebar split'};
  try {
    const result = run(SIDEBAR_COMMAND, ['-e', ghosttyScript(command, options.owner, options.sourceTty)], {timeoutMs});
    return {...(launchFailure(result, command) ?? mapQuickMarker(lastLine(result.stdout), command)), host};
  } finally {lease.release();}
}
