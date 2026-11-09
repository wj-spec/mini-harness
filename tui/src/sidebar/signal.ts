import {rmSync} from 'node:fs';
import path from 'node:path';
import {validInspectionScope, validInspectionVerification, validFileSource, type FileSource, type InspectionScope, type InspectionVerification} from '../inspection.js';
import type {InspectionPlan} from '../inspection.js';
import {reviewRef, type ReviewRef} from '../protocol.js';
import {readChannel, sameOwner, sidebarChannelPath, writeChannel, type SidebarOwner} from './channel.js';

/** 主 TUI 到侧栏的轻量事件信号：只含“本轮改过哪些文件”和刷新版本。 */
export type SidebarPlanStep = {id: string; title: string; status: string};

export type SidebarPlanSummary = {
  target?: ReviewRef;
  relation?: InspectionPlan['relation'];
  problem?: string | null;
  version: number;
  status: string;
  stale: boolean;
  objective: string;
  stepCount: number;
  doneSteps: number | null;
  acceptance?: boolean | null;
  steps?: SidebarPlanStep[];
};

export type SidebarSignal = {
  fileSource?: FileSource | null;
  navigation?: {id: string; page: 'changes' | 'plan' | 'verification'; mode: 'result' | 'current'};
  verification?: InspectionVerification;
  run?: InspectionScope;
  version: string;
  files: string[];
  active: boolean;
  updatedAt: number;
  plan?: SidebarPlanSummary;
  scope?: {workspace: string; owner: SidebarOwner; sessionId: string | null};
  revision?: number;
};

export type SidebarSignalInput = {
  fileSource?: FileSource | null;
  navigation?: SidebarSignal['navigation'];
  verification?: InspectionVerification;
  run?: InspectionScope;
  signature: string;
  files: string[];
  active: boolean;
  plan?: SidebarPlanSummary;
  owner?: SidebarOwner;
  sessionId?: string;
};

export function sidebarSignalPath(workspace: string, owner?: SidebarOwner): string {
  return sidebarChannelPath(workspace, 'signal', owner);
}

export function writeSidebarSignal(workspace: string, input: SidebarSignalInput): SidebarSignal {
  const file = sidebarSignalPath(workspace, input.owner);
  const previous = readSidebarSignal(workspace, input.owner);
  const payload: SidebarSignal = {
    ...(input.fileSource !== undefined ? {fileSource: input.fileSource} : {}),
    version: input.signature,
    files: input.files.slice(0, 200),
    active: input.active,
    updatedAt: Date.now(),
    ...(input.navigation ? {navigation: input.navigation} : {}),
    ...(input.run ? {run: input.run} : {}),
    ...(input.verification ? {verification: input.verification} : {}),
    ...(input.plan ? {plan: input.plan} : {}),
    ...(input.owner ? {scope: {workspace: path.resolve(workspace), owner: input.owner, sessionId: input.sessionId || null},
      revision: (previous?.revision ?? 0) + 1} : {}),
  };
  writeChannel(file, payload);
  return payload;
}

export function readSidebarSignal(workspace: string, owner?: SidebarOwner): SidebarSignal | null {
  try {
    const value = readChannel(sidebarSignalPath(workspace, owner)) as Partial<SidebarSignal> | null;
    if (!value) return null;
    if (value.fileSource !== undefined && value.fileSource !== null && !validFileSource(value.fileSource)) return null;
    if (value.navigation !== undefined && (!owner || !value.navigation ||
        Object.keys(value.navigation).length !== 3 || typeof value.navigation.id !== 'string' ||
        !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value.navigation.id) ||
        !['changes', 'plan', 'verification'].includes(value.navigation.page) || !['result', 'current'].includes(value.navigation.mode))) return null;
    if (value.run !== undefined && !validInspectionScope(value.run)) return null;
    if (value.verification !== undefined && (!value.run || !validInspectionVerification(value.verification) ||
        value.verification.requestId !== value.run.requestId)) return null;
    if (owner && (!value.scope || !sameOwner(value.scope.owner, owner) || value.scope.workspace !== path.resolve(workspace) ||
        !(value.scope.sessionId === null || typeof value.scope.sessionId === 'string' && value.scope.sessionId.length <= 256) ||
        !Number.isSafeInteger(value.revision) || (value.revision ?? 0) < 1)) return null;
    if (typeof value.version !== 'string' || typeof value.active !== 'boolean') return null;
    const files = Array.isArray(value.files)
      ? value.files.filter((item): item is string => typeof item === 'string' && item.length > 0 && item.length <= 512).slice(0, 200)
      : [];
    let plan: SidebarPlanSummary | undefined;
    if (value.plan && typeof value.plan === 'object') {
      const item = value.plan as Record<string, unknown>;
      if (item.target !== undefined && (!reviewRef(item.target) || item.target.session_id !== value.scope?.sessionId ||
          item.target.plan_version !== item.version || !['review', 'planning', 'execution', 'revision-source', 'revision-result'].includes(String(item.relation)) ||
          !(item.problem === null || typeof item.problem === 'string') ||
          item.relation === 'review' && value.run !== undefined ||
          item.relation === 'planning' && value.run?.taskId !== item.target.plan_task_id ||
          ['execution', 'revision-source', 'revision-result'].includes(String(item.relation)) && (!value.run?.requestId || !value.run.taskId || !value.run.segment))) return null;
      if (typeof item.version === 'number' && typeof item.status === 'string' &&
          typeof item.objective === 'string' && typeof item.stepCount === 'number') {
        plan = {
          ...(item.target ? {target: item.target as ReviewRef, relation: item.relation as InspectionPlan['relation'], problem: item.problem as string | null} : {}),
          version: item.version, status: item.status, stale: item.stale === true,
          objective: item.objective.slice(0, 200), stepCount: item.stepCount,
          doneSteps: typeof item.doneSteps === 'number' ? item.doneSteps : null,
          steps: Array.isArray(item.steps)
            ? item.steps.filter((step): step is SidebarPlanStep => Boolean(step) && typeof step === 'object' &&
                typeof (step as Record<string, unknown>).id === 'string' &&
                typeof (step as Record<string, unknown>).title === 'string' &&
                typeof (step as Record<string, unknown>).status === 'string').slice(0, 100)
            : [],
          acceptance: item.acceptance === true ? true : item.acceptance === false ? false : null,
        };
      }
    }
    return {version: value.version, files, active: value.active, plan, ...(value.run ? {run: value.run} : {}),
      ...(value.fileSource !== undefined ? {fileSource: value.fileSource} : {}),
      ...(value.navigation ? {navigation: value.navigation} : {}),
      ...(value.verification ? {verification: value.verification} : {}),
      ...(owner ? {scope: value.scope, revision: value.revision} : {}),
      updatedAt: typeof value.updatedAt === 'number' ? value.updatedAt : 0};
  } catch {
    return null;
  }
}

export function removeSidebarSignal(workspace: string, owner?: SidebarOwner): void {
  rmSync(sidebarSignalPath(workspace, owner), {force: true});
}
