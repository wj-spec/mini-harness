import {spawnSync} from 'node:child_process';

export const MAX_COPY_BYTES = 1024 * 1024;

export type CopyMethod = 'pbcopy' | 'wl-copy' | 'xclip' | 'clip' | 'osc52';

export interface CopyDeps {
  platform?: NodeJS.Platform;
  run?: (command: string, args: string[], input: string) => boolean;
  write?: (sequence: string) => void;
}

function runCommand(command: string, args: string[], input: string): boolean {
  try {
    return spawnSync(command, args, {input, timeout: 2000}).status === 0;
  } catch {
    return false;
  }
}

function truncate(text: string): string {
  if (Buffer.byteLength(text, 'utf8') <= MAX_COPY_BYTES) return text;
  let end = Math.min(text.length, MAX_COPY_BYTES);
  while (end > 0 && Buffer.byteLength(text.slice(0, end), 'utf8') > MAX_COPY_BYTES) end -= 1;
  return text.slice(0, end);
}

/**
 * 复制到系统剪贴板：优先本机命令，其次 OSC 52 转义序列。
 * 返回使用的方式；不可用或内容为空时返回 null。
 */
export function copyToClipboard(text: string, deps: CopyDeps = {}): CopyMethod | null {
  const body = truncate(text);
  if (!body) return null;
  const run = deps.run ?? runCommand;
  const platform = deps.platform ?? process.platform;
  const candidates: [CopyMethod, string, string[]][] = [];
  if (platform === 'darwin') candidates.push(['pbcopy', 'pbcopy', []]);
  else if (platform === 'win32') candidates.push(['clip', 'clip', []]);
  else candidates.push(['wl-copy', 'wl-copy', []], ['xclip', 'xclip', ['-selection', 'clipboard']]);
  for (const [method, command, args] of candidates) {
    if (run(command, args, body)) return method;
  }
  try {
    const write = deps.write ?? ((sequence: string) => {process.stdout.write(sequence);});
    write(`\x1b]52;c;${Buffer.from(body, 'utf8').toString('base64')}\x07`);
    return 'osc52';
  } catch {
    return null;
  }
}
