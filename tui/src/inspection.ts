import {reviewKey, type PlanReviewResult, type PlanRevisionResult, type ReviewRef, type RunResult} from './protocol.js';
import {execution, reviewRef, type CommandExecution} from './protocol.js';
import type {Entry} from './view-state.js';
import path from 'node:path';

export type InspectionVerification = {
  requestId: string | null;
  status: RunResult['status'] | null;
  stopReason: string;
  verified: boolean | null;
  completionAllowed: boolean | null;
  completionMessages: string[];
  omittedCommands: number;
  commands: {id: string; command: string; verification: boolean; pending: boolean; ok: boolean | null;
    execution: CommandExecution | null; output: string; truncated: boolean}[];
};

export function verificationInspection(inspection: RunInspection, entries: Entry[]): InspectionVerification {
  const clip = (text: string, limit: number) => text.length > limit ? text.slice(0, limit - 6) + '…（已截断）' : text;
  const result = inspection.result;
  const commands = inspection.scope.requestId ? entries.filter(entry => entry.kind === 'tool' &&
    entry.runId === inspection.scope.requestId && entry.call?.name === 'bash') : [];
  return {requestId: inspection.scope.requestId, status: result?.status ?? null,
    stopReason: clip(result?.stop_reason ?? '', 256), verified: result?.verified ?? null,
    completionAllowed: result?.completion?.allowed ?? null,
    completionMessages: (result?.completion?.messages ?? []).slice(0, 10).map(message => clip(message, 500)),
    omittedCommands: Math.max(0, commands.length - 20),
    commands: commands.slice(-20).map(entry => ({id: (entry.call!.id ?? String(entry.id)).slice(0, 256),
      command: clip(String(entry.call!.args.command ?? ''), 1000), verification: entry.call!.args.verification === true,
      pending: entry.pending === true, ok: entry.ok ?? null, execution: entry.execution ?? null,
      output: (entry.output ?? '').slice(0, 2000), truncated: (entry.output?.length ?? 0) > 2000})),
  };
}

export function validInspectionVerification(value: unknown): value is InspectionVerification {
  if (!value || typeof value !== 'object') return false;
  const item = value as Record<string, unknown>;
  const text = (value: unknown, limit: number) => typeof value === 'string' && value.length <= limit;
  const bool = (value: unknown) => value === null || typeof value === 'boolean';
  return (item.requestId === null || text(item.requestId, 256)) &&
    (item.status === null || ['done', 'stopped', 'cancelled', 'error', 'truncated'].includes(String(item.status))) &&
    text(item.stopReason, 256) && bool(item.verified) && bool(item.completionAllowed) &&
    Number.isSafeInteger(item.omittedCommands) && Number(item.omittedCommands) >= 0 &&
    Array.isArray(item.completionMessages) && item.completionMessages.length <= 10 && item.completionMessages.every(message => text(message, 500)) &&
    Array.isArray(item.commands) && item.commands.length <= 20 && item.commands.every(command => command && typeof command === 'object' &&
      text(command.id, 256) && text(command.command, 1000) && typeof command.verification === 'boolean' &&
      typeof command.pending === 'boolean' && bool(command.ok) && (command.execution === null || execution(command.execution)) &&
      text(command.output, 2000) && typeof command.truncated === 'boolean');
}

export type InspectionScope = {
  requestId: string | null;
  traceRunId: string | null;
  taskId: string | null;
  segment: number | null;
  taskStatus: string | null;
  status: 'running' | 'finished' | 'unknown' | 'restored';
  objective: string;
};

export type InspectionRevision = {id: string; source: ReviewRef; result: ReviewRef | null};
export type RunInspection = {scope: InspectionScope; result: RunResult | null; revision?: InspectionRevision | null};

export type FileSource = {root: string; identity: string};
export function validFileSource(value: unknown): value is FileSource {
  if (!value || typeof value !== 'object') return false;
  const source = value as FileSource;
  return Object.keys(source).length === 2 && typeof source.root === 'string' && path.isAbsolute(source.root) &&
    source.root.length <= 4096 && !/[\x00-\x1f\x7f]/.test(source.root) &&
    !source.root.split('/').some(part => part === '.' || part === '..') &&
    typeof source.identity === 'string' && /^\d{1,30}:\d{1,30}$/.test(source.identity);
}

export type InspectionSnapshot = {
  fileSource?: FileSource | null;
  workspace: string; sessionId: string; scope: InspectionScope;
  plan: InspectionPlan | null; verification: InspectionVerification; files: string[]; revision?: InspectionRevision | null;
  omittedFiles?: number;
};

export function validInspectionSnapshot(value: unknown): value is InspectionSnapshot {
  if (!value || typeof value !== 'object') return false;
  const item = value as InspectionSnapshot;
  if (item.fileSource !== undefined && item.fileSource !== null && !validFileSource(item.fileSource)) return false;
  if (typeof item.workspace !== 'string' || !item.workspace || item.workspace.length > 4096 ||
      typeof item.sessionId !== 'string' || !item.sessionId || item.sessionId.length > 128 ||
      !validInspectionScope(item.scope) || !item.scope.requestId || item.scope.requestId.length > 128 ||
      !validInspectionVerification(item.verification) || item.verification.requestId !== item.scope.requestId ||
      !Array.isArray(item.files) || item.files.length > 200 || item.files.some(file => typeof file !== 'string' || !file ||
        file.length > 512 || file.startsWith('/') || /[\x00-\x1f\x7f]/.test(file) || file.split('/').some(part => ['', '.', '..'].includes(part))) ||
      item.omittedFiles !== undefined && (!Number.isSafeInteger(item.omittedFiles) || item.omittedFiles < 0)) return false;
  const ref = (target: unknown) => reviewRef(target) && target.session_id === item.sessionId;
  const plan = item.plan;
  if (plan !== null && (!plan || !ref(plan.target) || !['review', 'planning', 'execution', 'revision-source', 'revision-result'].includes(plan.relation) ||
      typeof plan.objective !== 'string' || plan.objective.length > 8000 || typeof plan.status !== 'string' || plan.status.length > 256 ||
      !Number.isSafeInteger(plan.stepCount) || plan.stepCount < 0 || !(plan.problem === null || typeof plan.problem === 'string' && plan.problem.length <= 4000))) return false;
  const revision = item.revision;
  if (revision !== undefined && revision !== null) {
    if (typeof revision.id !== 'string' || !revision.id || revision.id.length > 256 || !ref(revision.source) ||
        revision.result !== null && (!ref(revision.result) || revision.result.plan_id !== revision.source.plan_id ||
          revision.result.plan_task_id !== revision.source.plan_task_id || revision.result.plan_version !== revision.source.plan_version + 1) ||
        !plan || reviewKey(plan.target) !== reviewKey(revision.result ?? revision.source) ||
        plan.relation !== (revision.result ? 'revision-result' : 'revision-source')) return false;
  }
  return true;
}

export type InspectionPlan = {
  target: ReviewRef;
  relation: 'review' | 'planning' | 'execution' | 'revision-source' | 'revision-result';
  objective: string;
  status: string;
  stepCount: number;
  problem: string | null;
};

export function planInspection(inspection: RunInspection | null, previous: InspectionPlan | null,
  sessionId: string, result: PlanReviewResult, problem?: string): InspectionPlan | null {
  if (!result.target || result.target.session_id !== sessionId) return previous;
  const scope = inspection?.scope;
  const same = previous && reviewKey(previous.target) === reviewKey(result.target);
  let relation: InspectionPlan['relation'] = same ? previous.relation : 'review';
  if (inspection?.revision) {
    const expected = inspection.revision.result ?? inspection.revision.source;
    if (reviewKey(expected) !== reviewKey(result.target)) return previous;
    relation = inspection.revision.result ? 'revision-result' : 'revision-source';
  }
  else if (scope?.taskId === result.target.plan_task_id) relation = 'planning';
  else if (result.launch && scope?.taskId === result.launch.task_id && scope.requestId === result.launch.run_id) relation = 'execution';
  else if (scope?.status === 'running' && !same) return previous;
  return {target: result.target, relation, objective: result.summary?.objective ?? (same ? previous.objective : ''),
    status: result.summary?.status ?? (same ? previous.status : 'unknown'),
    stepCount: result.summary?.step_count ?? (same ? previous.stepCount : 0),
    problem: problem ?? (result.outcome === 'unavailable' ? result.message : null)};
}

export function bindRevision(inspection: RunInspection, receipt: PlanRevisionResult, sessionId: string, starting = false): RunInspection {
  const {target, task, revision_id: id, result_target: result} = receipt;
  const scope = inspection.scope; const previous = inspection.revision;
  if (!target || target.session_id !== sessionId || !task?.run_id || !task.segment || !id ||
      scope.requestId !== task.run_id || scope.taskId && scope.taskId !== task.task_id || scope.segment && scope.segment !== task.segment ||
      !previous && !starting || previous && (previous.id !== id || reviewKey(previous.source) !== reviewKey(target)) ||
      result && (result.session_id !== target.session_id || result.plan_task_id !== target.plan_task_id || result.plan_id !== target.plan_id ||
        result.plan_version !== target.plan_version + 1) || previous?.result && result && reviewKey(previous.result) !== reviewKey(result)) return inspection;
  // 已确认产物不能被较旧的运行回执撤回。
  if (previous?.result && !result) return inspection;
  return {...inspection, scope: {...scope, taskId: task.task_id, segment: task.segment, taskStatus: task.status},
    revision: {id, source: target, result}};
}

export function revisionPlan(inspection: RunInspection, previous: InspectionPlan | null, entries: Entry[]): InspectionPlan | null {
  const revision = inspection.revision; if (!revision) return previous;
  const target = revision.result ?? revision.source;
  const same = previous && reviewKey(previous.target) === reviewKey(target);
  const known = entries.findLast(entry => entry.review?.target && reviewKey(entry.review.target) === reviewKey(target));
  return {target, relation: revision.result ? 'revision-result' : 'revision-source',
    objective: known?.review?.summary?.objective ?? (same ? previous.objective : '修订产物 · 等待读取'),
    status: known?.review?.summary?.status ?? (same ? previous.status : 'unknown'),
    stepCount: known?.review?.summary?.step_count ?? (same ? previous.stepCount : 0),
    problem: known?.reviewProblem ?? (known?.review?.outcome === 'unavailable' ? known.review.message : null)};
}

export const inspectionPlanLabel = (relation: InspectionPlan['relation'] | undefined) =>
  ({review: '审阅快照', planning: '规划产物', execution: '执行绑定', 'revision-source': '修订来源', 'revision-result': '修订产物'}[relation ?? 'review']);

export function startInspection(requestId: string, objective: string): RunInspection {
  return {scope: {requestId, traceRunId: null, taskId: null, segment: null, taskStatus: null, status: 'running', objective}, result: null};
}

export function validInspectionScope(value: unknown): value is InspectionScope {
  if (!value || typeof value !== 'object') return false;
  const scope = value as Record<string, unknown>;
  return ['requestId', 'traceRunId', 'taskId', 'taskStatus'].every(key => scope[key] === null ||
    typeof scope[key] === 'string' && scope[key].length > 0 && scope[key].length <= 256) &&
    (scope.segment === null || Number.isSafeInteger(scope.segment) && Number(scope.segment) >= 0) &&
    ['running', 'finished', 'unknown', 'restored'].includes(String(scope.status)) &&
    typeof scope.objective === 'string' && scope.objective.length <= 8000;
}
