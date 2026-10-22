import type {ApprovalDecision, BackendCommand, CommandExecution, ContextSnapshot, PersistedMessage, RunMetrics, RunResult, ServerEvent, SessionPurpose, ToolCall} from './protocol.js';

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
};
type Approval = {id: string; tool: string; preview: string; call: ToolCall; decisions?: ApprovalDecision[]};
export type ViewState = {
  phase: 'starting' | 'idle' | 'running' | 'waiting_approval' | 'stopping' | 'closed';
  activeId: string | null;
  activeCommand: BackendCommand | null;
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
};
export type Action =
  | {type: 'event'; event: ServerEvent}
  | {type: 'begin'; id: string; text: string}
  | {type: 'begin_command'; id: string; command: BackendCommand}
  | {type: 'notice'; text: string}
  | {type: 'cancel'}
  | {type: 'approval_sent'};

export const initialState: ViewState = {
  phase: 'starting', activeId: null, activeCommand: null, activity: 'initializing', entries: [], nextEntry: 0, live: '', segment: '',
  approval: null, model: '', workspace: '', lastResult: '', lastStatus: null, lastStopReason: null, error: null,
  sessionId: '', sessionPath: '', sessionPurpose: 'user', provider: '', metrics: null, contextSnapshots: [], hasStartedRun: false, resumed: false,
};

function append(state: ViewState, kind: Entry['kind'], text: string): ViewState {
  return {...state, entries: [...state.entries, {id: state.nextEntry, kind, text}], nextEntry: state.nextEntry + 1};
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
  if (action.type === 'begin') return append({...state, phase: 'running', activeId: action.id, activeCommand: null, activity: 'waiting_model', live: '', segment: '', approval: null, lastResult: '', lastStatus: null, lastStopReason: null, error: null, metrics: null, contextSnapshots: [], hasStartedRun: true}, 'task', action.text);
  if (action.type === 'begin_command') return {...state, phase: 'running', activeId: action.id, activeCommand: action.command,
    activity: action.command === 'resume' ? 'waiting_model' : null, approval: null, error: null,
    ...(action.command === 'resume' ? {live: '', segment: '', lastResult: '', lastStatus: null, lastStopReason: null, metrics: null, contextSnapshots: [], hasStartedRun: true} : {})};
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
      return {...state, phase: 'starting', activity: 'initializing', workspace: event.workspace, model: event.model, error: null,
        provider: event.provider ?? '', sessionId: event.session_id, sessionPath: event.session_path ?? '',
        sessionPurpose: event.session_purpose ?? 'user', resumed: event.resumed ?? false};
    case 'history_message':
      return restoreMessage(state, event.message);
    case 'notice':
      return append(state, 'notice', event.message);
    case 'command_result':
      return {...append(state, 'notice', event.text), phase: 'idle', activeId: null, activeCommand: null, activity: null, approval: null};
    case 'metrics': {
      const {v, type, id, ...metrics} = event;
      const latest = state.contextSnapshots.at(-1);
      return {...state, metrics: latest ? {...metrics, context_tokens: latest.total_tokens, context_window: latest.context_window} : metrics};
    }
    case 'context_snapshot': {
      const snapshot = event.snapshot;
      if (state.contextSnapshots.some(item => item.snapshot_id === snapshot.snapshot_id)) return state;
      return {...state, contextSnapshots: snapshot.phase === 'loaded' ? [snapshot] : [...state.contextSnapshots, snapshot].slice(-MAX_CONTEXT_SNAPSHOTS),
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
      return {...flush(state), phase: 'waiting_approval', approval: {id: event.approval_id, tool: event.call.name, preview: event.preview, call: event.call, decisions: event.decisions}};
    case 'approval_resolved':
      return state.approval && state.approval.id !== event.approval_id ? state : afterApproval(state);
    case 'done': {
      let next = state;
      const text = event.result.text;
      if (!state.segment) next = stream(next, text);
      else if (text.startsWith(state.segment)) next = stream(next, text.slice(state.segment.length));
      else if (text && text !== state.segment) next = stream(flush(next), text);
      next = flush(next);
      return {...next, entries: next.entries.map(entry => entry.pending && entry.runId === state.activeId ?
        {...entry, pending: false, uncertain: true, output: '此调用没有确认结果；请先核对实际文件与进程状态。'} : entry),
        live: '', segment: '', activeId: null, activeCommand: null, activity: null, approval: null, phase: 'idle',
        lastStatus: event.result.status, lastStopReason: event.result.stop_reason || null,
        lastResult: resultLabel(event.result.status, event.result.stop_reason)};
    }
    case 'error': {
      const rejected = state.activeId !== null && event.id === state.activeId && ['busy', 'invalid_request', 'duplicate_id'].includes(event.code);
      return {...state, error: `${event.code}: ${event.message}`, ...(rejected ? {phase: 'idle', activeId: null, activeCommand: null, activity: null, approval: null} as const : {})};
    }
    case 'diagnostic':
      return append(state, 'notice', `后端提示：${event.message.trim()}`);
    case 'closed': {
      const next = flush(state);
      return {...next, entries: next.entries.map(entry => entry.pending ?
        {...entry, pending: false, uncertain: true, output: '后端连接已断开，此调用结果未知；请先核对实际状态。'} : entry),
        phase: 'closed', activeId: null, activeCommand: null, activity: null, approval: null};
    }
    default:
      return state;
  }
}
