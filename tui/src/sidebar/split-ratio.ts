import {readSidebarLink, type SidebarLink} from './lifecycle.js';
import {resizeSidebarSplit} from './host.js';
import {setTimeout as delay} from 'node:timers/promises';

/** 右侧侧栏目标宽度占比；主 TUI 约占 70%。 */
export const SIDEBAR_RATIO = 0.3;
const LINK_POLL_MS = 100;
const RESIZE_SETTLE_MS = 300;
const DEFAULT_TIMEOUT_MS = 3000;

export function targetSidebarColumns(totalColumns: number, ratio = SIDEBAR_RATIO): number {
  return Math.max(1, Math.round(totalColumns * ratio));
}

export type AdjustDeps = {
  workspace: string;
  totalColumns: number;
  ratio?: number;
  parentPid?: number;
  readLink?: (workspace: string) => SidebarLink | null;
  resize?: (direction: 'left' | 'right', pixels: number) => boolean;
  wait?: (ms: number) => Promise<unknown>;
  timeoutMs?: number;
};

async function waitForColumns(
  workspace: string,
  readLink: (workspace: string) => SidebarLink | null,
  wait: (ms: number) => Promise<unknown>,
  timeoutMs: number,
  parentPid: number,
  differentFrom?: number,
): Promise<SidebarLink | null> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const link = readLink(workspace);
    if (link?.columns && link.parentPid === parentPid && link.columns !== differentFrom) return link;
    await wait(LINK_POLL_MS);
  }
  const final = readLink(workspace);
  return final?.parentPid === parentPid ? final : null;
}

/**
 * Ghostty split 创建时比例固定 50/50；这里用一次校准 resize 求出“每列对应多少像素”，
 * 再分几次调整到约 70/30。侧栏通过 link 文件回报自己的终端列数。
 */
export async function adjustSidebarSplit(options: AdjustDeps): Promise<SidebarLink | null> {
  const wait = options.wait ?? (ms => delay(ms));
  const readLink = options.readLink ?? readSidebarLink;
  const resize = options.resize ?? ((direction, pixels) => resizeSidebarSplit(direction, pixels));
  const target = targetSidebarColumns(options.totalColumns, options.ratio);
  const parentPid = options.parentPid ?? process.pid;
  let link = await waitForColumns(options.workspace, readLink, wait, options.timeoutMs ?? DEFAULT_TIMEOUT_MS, parentPid);
  if (!link?.columns) return null;
  let columns = link.columns;
  if (Math.abs(columns - target) <= 1) return link;

  // 校准：移动一小段，观察侧栏列数变化，得到每列像素数。
  let pixelsPerColumn = 8;
  for (const probe of [80, 200]) {
    const direction: 'left' | 'right' = columns > target ? 'right' : 'left';
    if (!resize(direction, probe)) return link;
    await wait(RESIZE_SETTLE_MS);
    const next = await waitForColumns(options.workspace, readLink, wait, 800, parentPid, columns);
    if (!next?.columns) return link;
    if (next.columns === columns) continue;
    pixelsPerColumn = probe / Math.abs(next.columns - columns);
    columns = next.columns;
    link = next;
    break;
  }

  // 用校准值逐次收敛，最多 3 次，避免单次误差或终端拒绝 resize。
  for (let attempt = 0; attempt < 3 && Math.abs(columns - target) > 1; attempt += 1) {
    const direction: 'left' | 'right' = columns > target ? 'right' : 'left';
    const pixels = Math.max(4, Math.round(Math.abs(columns - target) * pixelsPerColumn));
    if (!resize(direction, pixels)) break;
    await wait(RESIZE_SETTLE_MS);
    const next = await waitForColumns(options.workspace, readLink, wait, 800, parentPid, columns);
    if (!next?.columns) break;
    if (next.columns === columns) break;
    pixelsPerColumn = pixels / Math.abs(next.columns - columns);
    columns = next.columns;
    link = next;
  }
  return link;
}
