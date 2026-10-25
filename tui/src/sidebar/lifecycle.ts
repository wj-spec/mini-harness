/** 侧栏与主 TUI 的生命周期联动：TUI 退出后侧栏自动退出，避免留下孤儿面板。 */
import {createHash} from 'node:crypto';
import {mkdirSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';

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

export type SidebarLink = {pid: number; parentPid: number; columns?: number; rows?: number};

/** 侧栏登记文件放在系统临时目录，不给目标工作区留下运行时文件。 */
export function sidebarLinkPath(workspace: string): string {
  const name = createHash('sha1').update(path.resolve(workspace)).digest('hex').slice(0, 16);
  return path.join(tmpdir(), 'mini-harness-sidebar', `${name}.json`);
}

export function writeSidebarLink(workspace: string, link: SidebarLink): void {
  const file = sidebarLinkPath(workspace);
  mkdirSync(path.dirname(file), {recursive: true});
  writeFileSync(file, JSON.stringify(link));
}

export function readSidebarLink(workspace: string): SidebarLink | null {
  try {
    const value = JSON.parse(readFileSync(sidebarLinkPath(workspace), 'utf8')) as Partial<SidebarLink>;
    if (!Number.isSafeInteger(value.pid) || !Number.isSafeInteger(value.parentPid)) return null;
    if ((value.pid ?? 0) <= 0 || (value.parentPid ?? 0) <= 0) return null;
    const link: SidebarLink = {pid: value.pid!, parentPid: value.parentPid!};
    if (Number.isSafeInteger(value.columns) && (value.columns ?? 0) > 0) link.columns = value.columns!;
    if (Number.isSafeInteger(value.rows) && (value.rows ?? 0) > 0) link.rows = value.rows!;
    return link;
  } catch {
    return null;
  }
}

export function removeSidebarLink(workspace: string, pid?: number): void {
  const link = readSidebarLink(workspace);
  if (pid !== undefined && link && link.pid !== pid) return;
  rmSync(sidebarLinkPath(workspace), {force: true});
}

/** 主 TUI 退出时尽量立即终止自己启动的侧栏；只有 parentPid 匹配才处理，避免误杀。 */
export function killLinkedSidebar(workspace: string, options: {
  parentPid?: number;
  alive?: (pid: number) => boolean;
  kill?: (pid: number, signal: NodeJS.Signals) => boolean;
} = {}): boolean {
  const parentPid = options.parentPid ?? process.pid;
  const link = readSidebarLink(workspace);
  if (!link || link.parentPid !== parentPid) return false;
  const alive = options.alive ?? isProcessAlive;
  if (!alive(link.pid)) {
    removeSidebarLink(workspace, link.pid);
    return false;
  }
  try {
    (options.kill ?? ((pid, signal) => process.kill(pid, signal)))(link.pid, 'SIGTERM');
    return true;
  } catch {
    return false;
  }
}
