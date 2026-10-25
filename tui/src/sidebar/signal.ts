import {createHash} from 'node:crypto';
import {mkdirSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';

/** 主 TUI 到侧栏的轻量事件信号：只含“本轮改过哪些文件”和刷新版本。 */
export type SidebarSignal = {
  version: string;
  files: string[];
  active: boolean;
  updatedAt: number;
};

export type SidebarSignalInput = {
  signature: string;
  files: string[];
  active: boolean;
};

export function sidebarSignalPath(workspace: string): string {
  const name = createHash('sha1').update(path.resolve(workspace)).digest('hex').slice(0, 16);
  return path.join(tmpdir(), 'mini-harness-sidebar', `${name}.signal.json`);
}

export function writeSidebarSignal(workspace: string, input: SidebarSignalInput): SidebarSignal {
  const file = sidebarSignalPath(workspace);
  const payload: SidebarSignal = {
    version: input.signature,
    files: input.files.slice(0, 200),
    active: input.active,
    updatedAt: Date.now(),
  };
  mkdirSync(path.dirname(file), {recursive: true});
  writeFileSync(file, JSON.stringify(payload));
  return payload;
}

export function readSidebarSignal(workspace: string): SidebarSignal | null {
  try {
    const value = JSON.parse(readFileSync(sidebarSignalPath(workspace), 'utf8')) as Partial<SidebarSignal>;
    if (typeof value.version !== 'string' || typeof value.active !== 'boolean') return null;
    const files = Array.isArray(value.files)
      ? value.files.filter((item): item is string => typeof item === 'string' && item.length > 0 && item.length <= 512).slice(0, 200)
      : [];
    return {version: value.version, files, active: value.active, updatedAt: typeof value.updatedAt === 'number' ? value.updatedAt : 0};
  } catch {
    return null;
  }
}

export function removeSidebarSignal(workspace: string): void {
  rmSync(sidebarSignalPath(workspace), {force: true});
}
