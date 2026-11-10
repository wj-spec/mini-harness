/** 侧栏与主 TUI 的生命周期联动：TUI 退出后侧栏自动退出，避免留下孤儿面板。 */
import {createHash} from 'node:crypto';
import {readdirSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {readChannel, sameOwner, sidebarChannelPath, validateOwner, writeChannel, type SidebarOwner} from './channel.js';

export function isProcessAlive(pid: number, kill: (pid: number, signal: 0) => boolean = (target, signal) => process.kill(target, signal)): boolean {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    kill(pid, 0);
    return true;
  } catch (error) {
    // EPERM 说明进程仍在，只是当前用户没有信号权限。
    return (error as NodeJS.ErrnoException).code === 'EPERM';
  }
}

export function watchParentPid(pid: number, onGone: () => void, options: {
  intervalMs?: number;
  alive?: (target: number) => boolean;
} = {}): () => void {
  const alive = options.alive ?? isProcessAlive;
  let stopped = false;
  let fired = false;
  const check = () => {
    if (stopped || fired) return;
    if (alive(pid)) return;
    fired = true;
    stopped = true;
    clearInterval(timer);
    onGone();
  };
  const timer = setInterval(check, options.intervalMs ?? 1000);
  // 若 TUI 在侧栏启动前已经退出，也直接收尾。
  queueMicrotask(check);
  return () => {
    stopped = true;
    clearInterval(timer);
  };
}

export type SidebarLink = {pid: number; parentPid: number; columns?: number; rows?: number; owner?: SidebarOwner; hasSelection?: boolean};

/** 侧栏登记文件放在系统临时目录，不给目标工作区留下运行时文件。 */
export function sidebarLinkPath(workspace: string, owner?: SidebarOwner): string {
  return sidebarChannelPath(workspace, 'link', owner);
}

export function writeSidebarLink(workspace: string, link: SidebarLink): void {
  if (link.owner && link.owner.parentPid !== link.parentPid) throw new Error('Sidebar parent does not match owner');
  writeChannel(sidebarLinkPath(workspace, link.owner), link);
}

export function readSidebarLink(workspace: string, owner?: SidebarOwner): SidebarLink | null {
  try {
    const value = readChannel(sidebarLinkPath(workspace, owner)) as Partial<SidebarLink> | null;
    if (!value || owner && (!sameOwner(value.owner, owner) || value.parentPid !== owner.parentPid)) return null;
    if (!Number.isSafeInteger(value.pid) || !Number.isSafeInteger(value.parentPid)) return null;
    if ((value.pid ?? 0) <= 0 || (value.parentPid ?? 0) <= 0) return null;
    const link: SidebarLink = {pid: value.pid!, parentPid: value.parentPid!, ...(owner ? {owner} : {})};
    if (Number.isSafeInteger(value.columns) && (value.columns ?? 0) > 0) link.columns = value.columns!;
    if (Number.isSafeInteger(value.rows) && (value.rows ?? 0) > 0) link.rows = value.rows!;
    if (value.hasSelection === true) link.hasSelection = true;
    return link;
  } catch {
    return null;
  }
}

export function removeSidebarLink(workspace: string, pid?: number, owner?: SidebarOwner): void {
  const link = readSidebarLink(workspace, owner);
  if (pid !== undefined && link && link.pid !== pid) return;
  rmSync(sidebarLinkPath(workspace, owner), {force: true});
}

/** 主 TUI 退出时尽量立即终止自己启动的侧栏；只有 parentPid 匹配才处理，避免误杀。 */
export function killLinkedSidebar(workspace: string, options: {
  owner?: SidebarOwner;
  parentPid?: number;
  alive?: (pid: number) => boolean;
  kill?: (pid: number, signal: NodeJS.Signals) => boolean;
} = {}): boolean {
  const parentPid = options.parentPid ?? options.owner?.parentPid ?? process.pid;
  const link = readSidebarLink(workspace, options.owner);
  if (!link || link.parentPid !== parentPid) return false;
  const alive = options.alive ?? isProcessAlive;
  if (!alive(link.pid)) {
    removeSidebarLink(workspace, link.pid, options.owner);
    return false;
  }
  try {
    (options.kill ?? ((pid, signal) => process.kill(pid, signal)))(link.pid, 'SIGTERM');
    return true;
  } catch {
    return false;
  }
}

/**
 * 清理同一工作区中「父工作台已退出」的遗留侧栏：退出主区后无论是否有选区都应很快退出，
 * 但崩溃/旧版本可能留下孤儿面板；新工作台启动或再次打开侧栏前先按 link 文件回收。
 */
export function reapStaleSidebars(workspace: string, options: {
  root?: string;
  alive?: (pid: number) => boolean;
  kill?: (pid: number, signal: NodeJS.Signals) => boolean;
} = {}): number[] {
  const alive = options.alive ?? isProcessAlive;
  const kill = options.kill ?? ((pid, signal) => process.kill(pid, signal));
  const root = options.root ?? tmpdir();
  const base = path.join(root, 'mini-harness-sidebar');
  const prefix = `${createHash('sha1').update(path.resolve(workspace)).digest('hex').slice(0, 16)}-`;
  let entries: string[] = [];
  try {entries = readdirSync(base);} catch {return [];}
  const reaped: number[] = [];
  for (const entry of entries) {
    if (!entry.startsWith(prefix) || !entry.endsWith('.link.json')) continue;
    const file = path.join(base, entry);
    const link = readChannel(file) as Partial<SidebarLink> | null;
    if (!link || !Number.isSafeInteger(link.pid) || !Number.isSafeInteger(link.parentPid)) {rmSync(file, {force: true}); continue;}
    if (alive(link.parentPid!)) continue;
    if (alive(link.pid!)) {
      try {kill(link.pid!, 'SIGTERM'); reaped.push(link.pid!);} catch {}
    }
    if (validateOwner(link.owner)) {
      for (const kind of ['signal', 'request', 'reply'] as const) rmSync(sidebarChannelPath(workspace, kind, link.owner), {force: true});
    }
    rmSync(file, {force: true});
  }
  return reaped;
}
