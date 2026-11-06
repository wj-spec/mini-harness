import {createHash} from 'node:crypto';
import {mkdirSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';

/** 主 TUI 到侧栏的轻量事件信号：只含“本轮改过哪些文件”和刷新版本。 */
export type SidebarPlanSummary = {
  version: number;
  status: string;
  stale: boolean;
  objective: string;
  stepCount: number;
  doneSteps: number;
  acceptance?: boolean | null;
};

export type SidebarSignal = {
  version: string;
  files: string[];
  active: boolean;
  updatedAt: number;
  plan?: SidebarPlanSummary;
};

export type SidebarSignalInput = {
  signature: string;
  files: string[];
  active: boolean;
  plan?: SidebarPlanSummary;
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
    ...(input.plan ? {plan: input.plan} : {}),
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
    let plan: SidebarPlanSummary | undefined;
    if (value.plan && typeof value.plan === 'object') {
      const item = value.plan as Record<string, unknown>;
      if (typeof item.version === 'number' && typeof item.status === 'string' &&
          typeof item.objective === 'string' && typeof item.stepCount === 'number') {
        plan = {
          version: item.version, status: item.status, stale: item.stale === true,
          objective: item.objective.slice(0, 200), stepCount: item.stepCount,
          doneSteps: typeof item.doneSteps === 'number' ? item.doneSteps : 0,
          acceptance: item.acceptance === true ? true : item.acceptance === false ? false : null,
        };
      }
    }
    return {version: value.version, files, active: value.active, plan,
      updatedAt: typeof value.updatedAt === 'number' ? value.updatedAt : 0};
  } catch {
    return null;
  }
}

export function removeSidebarSignal(workspace: string): void {
  rmSync(sidebarSignalPath(workspace), {force: true});
}
