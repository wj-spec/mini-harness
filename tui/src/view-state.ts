import {AUTONOMY_LABELS, reviewKey} from './protocol.js';
import {bindRevision, revisionPlan, planInspection, startInspection, verificationInspection, type InspectionPlan, type InspectionSnapshot, type RunInspection} from './inspection.js';
import {agentTouchedFiles} from './sidebar/agent-activity.js';
import type {ApprovalDecision, ApprovalRule, AuditSummary, AutonomyLevel, BackendCommand, CommandExecution, CompletionSummary, ContextSnapshot, PersistedMessage, PlanModeState, PlanReviewResult, PlanRevisionResult, RunMetrics, RunResult, ServerEvent, SessionPurpose, SkillInfo, ToolCall} from './protocol.js';

export const MAX_CONTEXT_SNAPSHOTS = 100;

export type Entry = {
  id: number;
  kind: 'task' | 'assistant' | 'tool' | 'notice' | 'approval';
  text: string;
  call?: ToolCall;
  output?: string;
  ok?: boolean;
  pending?: boolean;
  uncertain?: boolean;
  runId?: string;
  execution?: CommandExecution;
  review?: PlanReviewResult;
  reviewProblem?: string;
  inspection?: InspectionSnapshot;
};
type Approval = {id: string; tool: string; preview: string; call: ToolCall; decisions?: ApprovalDecision[]; tier?: string; tierLabel?: string; rule?: ApprovalRule};
export type ViewState = {
  phase: 'starting' | 'idle' | 'running' | 'waiting_approval' | 'stopping' | 'closed';
  activeId: string | null;
  activeCommand: BackendCommand | null;
  runMode: string | null;
  activity: 'waiting_model' | 'responding' | 'using_tool' | 'initializing' | null;
  entries: Entry[];
  nextEntry: number;
  live: string;
  segment: string;
  approval: Approval | null;
  model: string;
  workspace: string;
  lastResult: string;
  lastStatus: RunResult['status'] | null;
  lastStopReason: string | null;
  error: string | null;
  sessionId: string;
  sessionPath: string;
  sessionPurpose: SessionPurpose;
  provider: string;
  metrics: RunMetrics | null;
  contextSnapshots: ContextSnapshot[];
  hasStartedRun: boolean;
  resumed: boolean;
  skills: SkillInfo[];
  autonomy: AutonomyLevel | '';
  autonomyLabel: string;
  planMode: PlanModeState;
  planModeKnown: boolean;
  lastVerified: boolean | null;
  lastCompletion: CompletionSummary | null;
  lastAudit: AuditSummary | null;
  inspection: RunInspection | null;
  inspectionPlan: InspectionPlan | null;
  attachedTaskId?: string;
  fileSource?: import('./inspection.js').FileSource | null;
  attachOffline?: boolean;
};
export type Action =
  | {type: 'event'; event: ServerEvent}
  | {type: 'begin'; id: string; text: string; plan?: PlanReviewResult; revision?: PlanRevisionResult}
  | {type: 'begin_command'; id: string; command: BackendCommand}
  | {type: 'notice'; text: string}
  | {type: 'retain_history'; snapshot: InspectionSnapshot}
  | {type: 'review_card'; result: PlanReviewResult; problem?: string}
  | {type: 'revision_receipt'; result: PlanRevisionResult}
  | {type: 'cancel'}
  | {type: 'approval_sent'};

export const initialState: ViewState = {
  phase: 'starting', activeId: null, activeCommand: null, runMode: null, activity: 'initializing', entries: [], nextEntry: 0, live: '', segment: '',
  approval: null, model: '', workspace: '', lastResult: '', lastStatus: null, lastStopReason: null, error: null,
  sessionId: '', sessionPath: '', sessionPurpose: 'user', provider: '', metrics: null, contextSnapshots: [], hasStartedRun: false, resumed: false, skills: [],
  autonomy: '', autonomyLabel: '', planMode: {active: false, pending: false, wanted: null}, planModeKnown: false, lastVerified: null, lastCompletion: null, lastAudit: null, inspection: null, inspectionPlan: null,
};

function append(state: ViewState, kind: Entry['kind'], text: string): ViewState {
  const runId = state.activeId && state.activeId === state.inspection?.scope.requestId ? state.activeId : undefined;
  return {...state, entries: [...state.entries, {id: state.nextEntry, kind, text, ...(runId ? {runId} : {})}], nextEntry: state.nextEntry + 1};
}

function retainInspection(state: ViewState): ViewState {
  const inspection = state.inspection;
  if (!inspection?.scope.requestId) return state;
  if (state.entries.some(entry => entry.inspection?.sessionId === state.sessionId && entry.inspection.scope.requestId === inspection.scope.requestId)) return state;
  const index = state.entries.findLastIndex(entry => entry.runId === inspection.scope.requestId);
  if (index < 0) return state;
  const snapshot: InspectionSnapshot = {workspace: state.workspace, sessionId: state.sessionId, scope: inspection.scope, fileSource: state.fileSource ?? null,
    revision: inspection.revision, plan: state.inspectionPlan?.relation === 'review' ? null : state.inspectionPlan,
    verification: verificationInspection(inspection, state.entries), files: agentTouchedFiles(state.entries, inspection.scope.requestId)};
  return {...state, entries: state.entries.map((entry, position) => position === index ? {...entry, inspection: snapshot} : entry)};
}

function flush(state: ViewState): ViewState {
  return state.live ? {...append(state, 'assistant', state.live), live: ''} : state;
}

function stream(state: ViewState, delta: string): ViewState {
  return {...state, live: state.live + delta, segment: state.segment + delta};
}

function clip(text: string, maximum: number): string {
  if (text.length <= maximum) return text;
  const half = Math.floor(maximum / 2);
  return `${text.slice(0, half)}\n… 省略 ${text.length - maximum} 字符 …\n${text.slice(-half)}`;
}

function argumentSummary(args: Record<string, unknown>): string {
  return clip(Object.entries(args).map(([key, value]) => {
    if (typeof value === 'string') return `${key}=${value.length > 120 ? `[${value.length} 字符]` : JSON.stringify(value)}`;
    if (Array.isArray(value)) return `${key}=[${value.length} 项]`;
    if (value && typeof value === 'object') return `${key}={…}`;
    return `${key}=${String(value)}`;
  }).join(', '), 240);
}

function resultLabel(status: string, reason: string | null | undefined): string {
  const label = ({done: '本轮结束', stopped: '已停止', cancelled: '已取消', error: '运行失败', truncated: '未完成'} as Record<string, string>)[status] ?? status;
  return reason ? `${label} · ${reason}` : label;
}

/** 结果标签已由状态标签展示，页脚只补「· 原因」部分 */
export function resultReason(result: string): string {
  return result.split(' · ').slice(1).join(' · ');
}

function ongoingActivity(state: ViewState): ViewState['activity'] {
  if (state.activeId === 'startup') return 'initializing';
  if (!state.activeId || (state.activeCommand && state.activeCommand !== 'resume')) return null;
  return state.entries.some(entry => entry.pending && entry.runId === state.activeId) ? 'using_tool' : 'waiting_model';
}

function afterApproval(state: ViewState): ViewState {
  if (!state.activeId) return {...state, approval: null, activity: null};
  return {...state, approval: null, activity: ongoingActivity(state),
    phase: state.phase === 'stopping' ? 'stopping' : state.activeId === 'startup' ? 'starting' : 'running'};
}

function restoreMessage(state: ViewState, message: PersistedMessage): ViewState {
  if (message.role === 'user') return append(state, 'task', message.content);
  if (message.role === 'assistant') {
    let next = message.content ? append(state, 'assistant', message.content) : state;
    for (const call of message.tool_calls ?? []) {
      const entry: Entry = {id: next.nextEntry, kind: 'tool', text: `工具 › ${call.name}`,
        call, uncertain: true, pending: false, output: '恢复记录中没有确认结果；请先核对实际文件与进程状态。'};
      next = {...next, entries: [...next.entries, entry], nextEntry: next.nextEntry + 1};
    }
    return next;
  }
  if (message.role !== 'tool') return state;
  const index = state.entries.findLastIndex(entry => entry.kind === 'tool' && entry.uncertain &&
    (message.tool_call_id ? entry.call?.id === message.tool_call_id : entry.call?.name === message.name));
  const entry: Entry = {id: index >= 0 ? state.entries[index].id : state.nextEntry, kind: 'tool',
    call: index >= 0 ? state.entries[index].call : {id: message.tool_call_id ?? null, name: message.name || 'tool', args: {}},
    text: `恢复工具记录 › ${message.name || 'tool'}`, output: message.content, pending: false,
    uncertain: message.content.startsWith('ERROR interrupted:'), execution: message.execution ?? undefined,
    ok: message.execution ? message.execution.status === 'succeeded' : undefined};
  return index < 0 ? {...state, entries: [...state.entries, entry], nextEntry: state.nextEntry + 1} :
    {...state, entries: state.entries.map((item, position) => position === index ? entry : item)};
}

export function reduceView(state: ViewState, action: Action): ViewState {
  if (action.type === 'retain_history') {
    const snapshot = action.snapshot;
    if (snapshot.workspace !== state.workspace || snapshot.sessionId !== state.sessionId ||
        state.entries.some(entry => entry.inspection?.scope.requestId === snapshot.scope.requestId)) return state;
    return {...state, entries: [...state.entries, {id: state.nextEntry, kind: 'notice',
      text: `历史结果 · ${snapshot.scope.objective}`, inspection: snapshot}], nextEntry: state.nextEntry + 1};
  }
  if (action.type === 'revision_receipt') {
    const inspection = state.inspection ? bindRevision(state.inspection, action.result, state.sessionId) : null;
    const entries = state.entries.map(entry => {
      const snapshot = entry.inspection; if (!snapshot?.revision) return entry;
      const original = {scope: snapshot.scope, result: null, revision: snapshot.revision};
      const bound = bindRevision(original, action.result, snapshot.sessionId);
      return bound === original ? entry : {...entry, inspection: {...snapshot, scope: bound.scope, revision: bound.revision,
        plan: revisionPlan(bound, snapshot.plan, state.entries)}};
    });
    return {...state, entries, inspection, inspectionPlan: inspection && inspection !== state.inspection
      ? revisionPlan(inspection, state.inspectionPlan, entries) : state.inspectionPlan};
  }
  if (action.type === 'review_card') {
    const result = action.result;
    if (!result.target) return state;
    if (result.summary && result.outcome !== 'unavailable') state = {...state, entries: state.entries.map(entry => {
      const snapshot = entry.inspection;
      if (!snapshot || snapshot.plan && !snapshot.revision || snapshot.sessionId !== result.target!.session_id) return entry;
      const plan = planInspection({scope: snapshot.scope, result: null, revision: snapshot.revision}, snapshot.plan, snapshot.sessionId, result);
      return plan && plan.relation !== 'review' ? {...entry, inspection: {...snapshot, plan}} : entry;
    })};
    state = {...state, inspectionPlan: planInspection(state.inspection, state.inspectionPlan, state.sessionId, result, action.problem)};
    const index = state.entries.findIndex(entry => entry.review?.target && reviewKey(entry.review.target) === reviewKey(result.target!));
    if (index < 0) return {...state, entries: [...state.entries, {id: state.nextEntry, kind: 'notice', text: result.message, review: result}], nextEntry: state.nextEntry + 1};
    return {...state, entries: state.entries.map((entry, position) => position === index ? {...entry, text: action.problem ?? result.message, review: result, reviewProblem: action.problem} : entry)};
  }
  if (action.type === 'begin') {
    let inspection = startInspection(action.id, action.text);
    const launch = action.plan?.launch;
    if (launch?.run_started && launch.run_id === action.id) inspection = {...inspection, scope: {...inspection.scope,
      taskId: launch.task_id, segment: launch.segment, taskStatus: launch.status}};
    if (action.revision) inspection = bindRevision(inspection, action.revision, state.sessionId, true);
    return append({...state, inspection, inspectionPlan: action.revision ? revisionPlan(inspection, null, state.entries) : action.plan ? planInspection(inspection, null, state.sessionId, action.plan) : null,
      phase: 'running', activeId: action.id, activeCommand: null, runMode: null, activity: 'waiting_model', live: '', segment: '', approval: null, lastResult: '', lastStatus: null, lastStopReason: null, error: null, metrics: null, contextSnapshots: [], hasStartedRun: true, lastVerified: null, lastCompletion: null, lastAudit: null}, 'task', action.text);
  }
  if (action.type === 'begin_command') return {...state, phase: 'running', activeId: action.id, activeCommand: action.command, runMode: null,
    activity: action.command === 'resume' ? 'waiting_model' : null, approval: null, error: null,
    ...(action.command === 'resume' ? {inspection: startInspection(action.id, '继续任务'), inspectionPlan: null, live: '', segment: '', lastResult: '', lastStatus: null, lastStopReason: null, metrics: null, contextSnapshots: [], hasStartedRun: true, lastVerified: null, lastCompletion: null, lastAudit: null} : {})};
  if (action.type === 'notice') return append(state, 'notice', action.text);
  if (action.type === 'cancel') return {...state, phase: 'stopping', approval: null};
  if (action.type === 'approval_sent') return afterApproval(state);

  const event = action.event;
  if (event.id === null && (event.type === 'context_snapshot' || event.type === 'metrics') &&
    (state.hasStartedRun || state.phase === 'closed')) return state;
  if (event.type === 'status' && event.phase === 'starting' && event.id && !state.activeId) {
    return {...state, phase: 'starting', activeId: event.id, activeCommand: null, activity: 'initializing'};
  }
  if (event.id && state.activeId && event.id !== state.activeId) return state;
  if (event.id && !state.activeId && ['status', 'text_delta', 'tool_call', 'tool_result', 'approval_request', 'approval_resolved', 'done', 'metrics', 'context_snapshot', 'command_result'].includes(event.type)) return state;
  switch (event.type) {
    case 'ready':
      return {...state, inspection: null, inspectionPlan: null, phase: 'starting', runMode: null, planModeKnown: false, activity: 'initializing', workspace: event.workspace, model: event.model, error: null,
        fileSource: event.file_source,
        attachOffline: event.capabilities?.includes('attach-offline') ?? false,
        ...(event.capabilities?.includes('attach-offline') ? {phase: 'idle', activeId: null, activeCommand: null, approval: null, activity: null} : {}),
        provider: event.provider ?? '', sessionId: event.session_id, sessionPath: event.session_path ?? '',
        sessionPurpose: event.session_purpose ?? 'user', resumed: event.resumed ?? false,
        skills: event.skills ?? [],
        ...(event.autonomy ? {autonomy: event.autonomy as AutonomyLevel, autonomyLabel: event.autonomy_label ?? AUTONOMY_LABELS[event.autonomy as AutonomyLevel] ?? ''} : {})};
    case 'plan_mode':
      return state.phase === 'closed' ? state : {...state, planMode: event.mode, planModeKnown: true};
    case 'task_state': {
      if (!event.id || event.id !== state.activeId) return state;
      let current = state.inspection?.scope.requestId === event.id ? state.inspection :
        event.task.status === 'running' && event.task.segment > 0 ? startInspection(event.id, event.task.objective) : null;
      if (current?.scope.segment && event.task.segment < current.scope.segment) return state;
      if (current?.scope.taskId && current.scope.taskId !== event.task.task_id) return state;
      current = current ? {...current, scope: {...current.scope, taskId: event.task.task_id, segment: event.task.segment,
        taskStatus: event.task.status, objective: event.task.objective}} : null;
      let plan = current && state.inspection?.scope.requestId !== event.id ? null : state.inspectionPlan;
      if (current && !plan) {
        const reviewed = state.entries.findLast(entry => entry.review?.launch?.task_id === event.task.task_id &&
          entry.review?.target?.plan_id === event.task.plan_id && entry.review?.target?.plan_version === event.task.plan_version)?.review;
        const known = reviewed ? planInspection(null, null, state.sessionId, reviewed) : null;
        if (known) plan = {...known, relation: 'execution'};
      }
      return {...state, runMode: event.task.mode ?? state.runMode, inspection: current ?? state.inspection, inspectionPlan: plan};
    }
    case 'task_snapshot': {
      const running = !state.attachOffline && event.task.status === 'running' && Boolean(event.task.run_id);
      const same = state.inspection?.scope.requestId === event.task.run_id && state.inspection?.scope.taskId === event.task.task_id;
      return {...state, attachedTaskId: event.task.task_id, fileSource: event.file_source,
        phase: running ? state.approval ? 'waiting_approval' : 'running' : 'idle',
        inspectionPlan: same ? state.inspectionPlan : null,
        inspection: {result: same ? state.inspection!.result : null, revision: same ? state.inspection!.revision : null,
          scope: {requestId: event.task.run_id, traceRunId: same ? state.inspection!.scope.traceRunId : null,
            taskId: event.task.task_id, segment: event.task.segment, taskStatus: event.task.status,
            status: running ? 'running' : state.attachOffline && event.task.status === 'running' ? 'unknown' : same ? state.inspection!.scope.status : 'restored', objective: event.task.objective}},
        runMode: running ? event.task.mode ?? null : null, activeId: running ? event.task.run_id : null,
        ...(!running ? {approval: null, activity: null, activeCommand: null} : {})};
    }
    case 'history_message':
      return restoreMessage(state, event.message);
    case 'notice':
      return append(state, 'notice', event.message);
    case 'command_result':
      return {...append(state, 'notice', event.text), skills: event.skills ?? state.skills,
        ...(event.autonomy ? {autonomy: event.autonomy as AutonomyLevel, autonomyLabel: AUTONOMY_LABELS[event.autonomy as AutonomyLevel] ?? state.autonomyLabel} : {}),
        phase: 'idle', activeId: null, activeCommand: null, runMode: null, activity: null, approval: null};
    case 'metrics': {
      const {v, type, id, ...metrics} = event;
      const latest = state.contextSnapshots.at(-1);
      return {...state, metrics: latest ? {...metrics, context_tokens: latest.total_tokens, context_window: latest.context_window} : metrics};
    }
    case 'context_snapshot': {
      const snapshot = event.snapshot;
      if (state.contextSnapshots.some(item => item.snapshot_id === snapshot.snapshot_id)) return state;
      return {...state, inspection: state.inspection && event.id === state.inspection.scope.requestId && snapshot.phase !== 'loaded'
        ? {...state.inspection, scope: {...state.inspection.scope, traceRunId: snapshot.run_id}} : state.inspection,
        contextSnapshots: snapshot.phase === 'loaded' ? [snapshot] : [...state.contextSnapshots, snapshot].slice(-MAX_CONTEXT_SNAPSHOTS),
        metrics: state.metrics ? {...state.metrics, context_tokens: snapshot.total_tokens, context_window: snapshot.context_window} : null};
    }
    case 'status':
      if (event.phase === 'idle') return state.activeId && state.activeId !== 'startup' ? state : {...state, phase: 'idle', activeId: null, activeCommand: null, activity: null, approval: null};
      return {...state, phase: state.phase === 'stopping' ? 'stopping' : event.phase,
        activity: event.phase === 'starting' ? 'initializing' : state.activity};
    case 'text_delta':
      return {...stream(state, event.delta), activity: 'responding'};
    case 'tool_call': {
      const next = {...flush(state), segment: ''};
      const entry: Entry = {id: next.nextEntry, kind: 'tool',
        text: `工具 › ${event.call.name}${Object.keys(event.call.args).length ? ` (${argumentSummary(event.call.args)})` : ''}`,
        call: event.call, pending: true, runId: state.activeId ?? undefined};
      return {...next, entries: [...next.entries, entry], nextEntry: next.nextEntry + 1, activity: 'using_tool'};
    }
    case 'tool_result': {
      const next = flush(state);
      const index = next.entries.findLastIndex(entry => entry.kind === 'tool' && entry.pending &&
        entry.runId === state.activeId && (event.call.id ? entry.call?.id === event.call.id : entry.call?.name === event.call.name));
      const entry: Entry = {id: index >= 0 ? next.entries[index].id : next.nextEntry, kind: 'tool',
        text: `${event.ok ? '调用已返回' : '调用错误'} › ${event.call.name}`,
        call: event.call, output: event.output, ok: event.ok, pending: false, runId: state.activeId ?? undefined, execution: event.execution};
      const updated = index < 0 ? {...next, entries: [...next.entries, entry], nextEntry: next.nextEntry + 1} :
        {...next, entries: next.entries.map((item, position) => position === index ? entry : item)};
      return {...updated, activity: ongoingActivity(updated)};
    }
    case 'approval_request':
      return {...flush(state), phase: 'waiting_approval', approval: {id: event.approval_id, tool: event.call.name, preview: event.preview, call: event.call, decisions: event.decisions,
        tier: event.tier, tierLabel: event.tier_label, rule: event.rule}};
    case 'approval_resolved':
      return state.approval && state.approval.id !== event.approval_id ? state : afterApproval(state);
    case 'done': {
      let next = state;
      const text = event.result.text;
      // done/error/truncated 的 text 是给用户的正文；cancelled/stopped 的
      // text 常是机器状态串（"cancelled"、"stopped: max_steps"），只保留已流出的部分。
      const finalAnswer = event.result.status === 'done' || event.result.status === 'error' || event.result.status === 'truncated';
      if (finalAnswer) {
        if (!state.segment) next = stream(next, text);
        else if (text.startsWith(state.segment)) next = stream(next, text.slice(state.segment.length));
        else if (text && text !== state.segment) next = stream(flush(next), text);
      }
      next = flush(next);
      if (event.result.receipt) next = append(next, 'notice', event.result.receipt);
      return retainInspection({...next, entries: next.entries.map(entry => entry.pending && entry.runId === state.activeId ?
        {...entry, pending: false, uncertain: true, output: '此调用没有确认结果；请先核对实际文件与进程状态。'} : entry),
        live: '', segment: '', activeId: null, activeCommand: null, runMode: null, activity: null, approval: null, phase: 'idle',
        inspection: state.inspection?.scope.requestId === event.id ? {...state.inspection, scope: {...state.inspection.scope, status: 'finished',
          traceRunId: event.result.run_id || state.inspection.scope.traceRunId}, result: event.result} : state.inspection,
        lastStatus: event.result.status, lastStopReason: event.result.stop_reason || null,
        lastVerified: event.result.verified ?? null,
        lastCompletion: event.result.completion ?? null,
        lastAudit: event.result.audit ?? null,
        lastResult: resultLabel(event.result.status, event.result.stop_reason)});
    }
    case 'error': {
      const rejected = state.activeId !== null && event.id === state.activeId && ['busy', 'invalid_request', 'duplicate_id', 'default_mode_unavailable', 'session_busy', 'session_unavailable'].includes(event.code);
      return {...state, error: `${event.code}: ${event.message}`, ...(rejected ? {phase: 'idle', activeId: null, activeCommand: null, runMode: null, activity: null, approval: null,
        inspection: state.inspection?.scope.requestId === event.id ? null : state.inspection} as const : {})};
    }
    case 'diagnostic':
      return append(state, 'notice', `后端提示：${event.message.trim()}`);
    case 'closed': {
      const next = flush(state);
      return retainInspection({...next, entries: next.entries.map(entry => entry.pending ?
        {...entry, pending: false, uncertain: true, output: '后端连接已断开，此调用结果未知；请先核对实际状态。'} : entry),
        inspection: state.inspection?.scope.status === 'running' ? {...state.inspection, scope: {...state.inspection.scope, status: 'unknown'}} : state.inspection,
        phase: 'closed', activeId: null, activeCommand: null, runMode: null, activity: null, approval: null});
    }
    default:
      return state;
  }
}
