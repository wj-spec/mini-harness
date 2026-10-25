export const MAX_FRAME_BYTES = 1024 * 1024;

export interface ToolCall {
  id: string | null;
  name: string;
  args: Record<string, unknown>;
}

export type ApprovalDecision = 'once' | 'session' | 'workspace' | 'deny';
export type SessionPurpose = 'user' | 'test';
export const BACKEND_COMMANDS = ['resume', 'permissions', 'permissions_reset', 'cost', 'tools', 'diff', 'mcp', 'skills'] as const;
export const COMMAND_COMPLETIONS = ['/help', '/resume', '/context', '/continue', '/cost', '/tools', '/diff', '/permissions',
  '/permissions reset', '/mcp', '/skills', '/session', '/theme', '/followup', '/copy', '/project', '/about', '/sidebar', '/sidebar split', '/sidebar setup', '/quit', '/exit'] as const;
export const COMMAND_DESCRIPTIONS: Record<string, string> = {
  '/help': '查看操作帮助', '/resume': '继续已保存的任务', '/context': '查看上下文与预算', '/continue': '同 /resume，继续任务',
  '/cost': '查看本轮与会话成本', '/tools': '查看工具调用统计', '/diff': '查看工作区改动', '/permissions': '查看审批规则',
  '/permissions reset': '恢复默认审批规则', '/mcp': '查看 MCP 服务', '/skills': '查看与切换技能', '/session': '查看会话路径',
  '/theme': '切换主题', '/followup': '排队后续任务', '/copy': '复制最近回复', '/project': '管理项目背景',
  '/about': '管理个人偏好', '/sidebar': '打开右侧变更侧栏', '/sidebar split': '窗口内右侧分屏（跟随窗口）', '/sidebar setup': '写入 Ghostty 右侧配置', '/quit': '退出', '/exit': '退出',
};
export function commandMatches(value: string): string[] {
  return value.startsWith('/') && !value.includes('\n') ? COMMAND_COMPLETIONS.filter(command => command.startsWith(value)) : [];
}
export type BackendCommand = typeof BACKEND_COMMANDS[number];
export type SkillsCommandArgs = {action?: 'list' | 'refresh' | 'activate' | 'unload'; name?: string};

export function validateSkillsArgs(value: unknown): asserts value is SkillsCommandArgs {
  if (!record(value) || Object.keys(value).some(key => key !== 'action' && key !== 'name')) {
    throw new Error('skills 参数只允许 action 与 name');
  }
  const action = value.action ?? 'list';
  if ((value.action !== undefined && typeof value.action !== 'string') ||
      !['list', 'refresh', 'activate', 'unload'].includes(String(action))) throw new Error('skills 操作参数无效');
  if (action === 'activate' || action === 'unload') {
    if (typeof value.name !== 'string' || !value.name.trim()) throw new Error('skills 激活或卸载必须指定技能名称');
  } else if ('name' in value) throw new Error('skills 列表与重扫不接受名称参数');
}

export interface PersistedMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string;
  tool_calls?: ToolCall[];
  usage?: Record<string, unknown> | null;
  finish_reason?: string | null;
  name?: string | null;
  tool_call_id?: string | null;
  execution?: CommandExecution | null;
}

export interface CommandExecution {
  status: 'succeeded' | 'failed' | 'timed_out' | 'cancelled' | 'unknown';
  exit_code: number | null;
}

export interface RunResult {
  receipt?: string;
  text: string;
  steps: number;
  status: 'done' | 'stopped' | 'cancelled' | 'error' | 'truncated';
  usage: Record<string, unknown>;
  cost_usd: number;
  run_id: string;
  stop_reason: string;
}

export interface RunMetrics {
  used_tokens: number;
  limit_tokens: number;
  estimated: boolean;
  context_tokens: number | null;
  context_window: number;
  done: number;
  planned: number;
  cost_usd: number;
}

export const CONTEXT_SECTIONS = ['system', 'memory', 'tools', 'history'] as const;
export type ContextSection = typeof CONTEXT_SECTIONS[number];
export type ContextCounts = Record<ContextSection, number>;
export interface ContextBlock {
  id: string;
  section: ContextSection;
  kind: string;
  label: string;
  source: string;
  tokens: number;
  chars: number;
  content_hash: string;
}
export interface ContextSnapshot {
  schema_version: 1;
  snapshot_id: string;
  request_id: string;
  run_id: string;
  model: string;
  ts: number;
  step: number;
  attempt: number;
  phase: 'loaded' | 'request' | 'before_compaction' | 'after_compaction';
  reason: string;
  estimated: true;
  count_method: 'heuristic_cjk_v1';
  context_window: number;
  input_capacity: number;
  total_tokens: number;
  sections: ContextCounts;
  caps: Record<string, number>;
  reserves: {output: number; scratch: number};
  remaining_tokens: number;
  over_budget: boolean;
  blocks: ContextBlock[];
  delta_tokens: number;
  section_deltas: ContextCounts;
}

type Envelope<T extends string, P = Record<never, never>> = {
  v: 1;
  type: T;
  id: string | null;
} & P;

export type ServerEvent =
  | Envelope<'ready', {workspace: string; model: string; session_id: string; capabilities: string[]; provider?: string; session_path?: string; session_purpose?: SessionPurpose; resumed?: boolean}>
  | Envelope<'history_message', {message: PersistedMessage}>
  | Envelope<'notice', {message: string}>
  | Envelope<'command_result', {command: BackendCommand; text: string}>
  | Envelope<'metrics', RunMetrics>
  | Envelope<'context_snapshot', {snapshot: ContextSnapshot}>
  | Envelope<'diagnostic', {message: string}>
  | Envelope<'status', {phase: 'starting' | 'idle' | 'running' | 'waiting_approval' | 'stopping'}>
  | Envelope<'text_delta', {delta: string}>
  | Envelope<'tool_call', {call: ToolCall}>
  | Envelope<'tool_result', {call: ToolCall; ok: boolean; output: string; execution?: CommandExecution}>
  | Envelope<'approval_request', {approval_id: string; call: ToolCall; preview: string; decisions?: ApprovalDecision[]}>
  | Envelope<'approval_resolved', {approval_id: string; decision: ApprovalDecision}>
  | Envelope<'done', {result: RunResult}>
  | Envelope<'ack', {action: 'cancel' | 'approval' | 'steer'}>
  | Envelope<'closed'>
  | Envelope<'error', {code: string; message: string}>;

export type ClientRequest = (
  | Envelope<'run', {text: string}>
  | Envelope<'command', {command: Exclude<BackendCommand, 'skills'>}>
  | Envelope<'command', {command: 'skills'; args?: SkillsCommandArgs}>
  | Envelope<'cancel', {target: string}>
  | Envelope<'steer', {target: string; text: string}>
  | Envelope<'approval', {approval_id: string; decision: ApprovalDecision}>
  | Envelope<'shutdown'>
) & {id: string};

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function validId(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 128;
}

function toolCall(value: unknown): value is ToolCall {
  return record(value) && (value.id === null || typeof value.id === 'string') &&
    typeof value.name === 'string' && value.name.length > 0 && record(value.args);
}

function message(value: unknown): value is PersistedMessage {
  return record(value) && ['system', 'user', 'assistant', 'tool'].includes(String(value.role)) &&
    typeof value.content === 'string' &&
    (value.tool_calls === undefined || (Array.isArray(value.tool_calls) && value.tool_calls.every(toolCall))) &&
    (value.usage === undefined || value.usage === null || record(value.usage)) &&
    (value.execution === undefined || value.execution === null || execution(value.execution)) &&
    ['finish_reason', 'name', 'tool_call_id'].every(key => value[key] === undefined || value[key] === null || typeof value[key] === 'string');
}

const approvalDecisions: readonly string[] = ['once', 'session', 'workspace', 'deny'];

function execution(value: unknown): value is CommandExecution {
  if (!record(value) || !['succeeded', 'failed', 'timed_out', 'cancelled', 'unknown'].includes(String(value.status)) ||
      !(value.exit_code === null || (typeof value.exit_code === 'number' && Number.isSafeInteger(value.exit_code)))) return false;
  if (value.status === 'succeeded') return value.exit_code === 0;
  if (value.status === 'failed') return typeof value.exit_code === 'number' && value.exit_code !== 0;
  if (value.status === 'unknown') return value.exit_code === null;
  return true;
}

function runResult(value: unknown): value is RunResult {
  return record(value) && typeof value.text === 'string' &&
    typeof value.steps === 'number' && Number.isInteger(value.steps) && value.steps >= 0 &&
    ['done', 'stopped', 'cancelled', 'error', 'truncated'].includes(String(value.status)) &&
    record(value.usage) && typeof value.cost_usd === 'number' && Number.isFinite(value.cost_usd) &&
    typeof value.run_id === 'string' && typeof value.stop_reason === 'string';
}

function safeInteger(value: unknown, signed = false): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && (signed || value >= 0);
}

function contextCounts(value: unknown, signed = false): value is ContextCounts {
  return record(value) && Object.keys(value).length === CONTEXT_SECTIONS.length &&
    CONTEXT_SECTIONS.every(section => safeInteger(value[section], signed));
}

function contextSnapshot(value: unknown): value is ContextSnapshot {
  if (!record(value) || value.schema_version !== 1 || value.estimated !== true || value.count_method !== 'heuristic_cjk_v1' ||
      !['snapshot_id', 'request_id', 'run_id'].every(key => typeof value[key] === 'string' && (value[key] as string).length > 0) ||
      typeof value.model !== 'string' || typeof value.reason !== 'string' ||
      typeof value.ts !== 'number' || !Number.isFinite(value.ts) || value.ts < 0 ||
      !['loaded', 'request', 'before_compaction', 'after_compaction'].includes(String(value.phase)) ||
      !['step', 'attempt', 'context_window', 'input_capacity', 'total_tokens', 'remaining_tokens'].every(key => safeInteger(value[key])) ||
      !contextCounts(value.sections) || !contextCounts(value.section_deltas, true) || !safeInteger(value.delta_tokens, true) ||
      !record(value.caps) || !Object.values(value.caps).every(cap => safeInteger(cap)) ||
      !record(value.reserves) || !safeInteger(value.reserves.output) || !safeInteger(value.reserves.scratch) ||
      typeof value.over_budget !== 'boolean' || !Array.isArray(value.blocks)) return false;
  if (value.phase === 'loaded' && (value.step !== 0 || value.attempt !== 0 || value.reason !== 'session_loaded')) return false;
  const sections = value.sections;
  const total = CONTEXT_SECTIONS.reduce((sum, section) => sum + sections[section], 0);
  if (!Number.isSafeInteger(total) || total !== value.total_tokens ||
      value.remaining_tokens !== Math.max(0, (value.input_capacity as number) - total) ||
      value.over_budget !== (total > (value.input_capacity as number))) return false;
  const blockTotals: ContextCounts = {system: 0, memory: 0, tools: 0, history: 0};
  const ids = new Set<string>();
  for (const block of value.blocks) {
    if (!record(block) || typeof block.id !== 'string' || !block.id || ids.has(block.id) ||
        !CONTEXT_SECTIONS.includes(block.section as ContextSection) ||
        !['kind', 'label', 'source', 'content_hash'].every(key => typeof block[key] === 'string') ||
        !safeInteger(block.tokens) || !safeInteger(block.chars)) return false;
    ids.add(block.id);
    blockTotals[block.section as ContextSection] += block.tokens;
  }
  return CONTEXT_SECTIONS.every(section => blockTotals[section] === sections[section]);
}

export function parseServerEvent(line: string): ServerEvent {
  if (Buffer.byteLength(line, 'utf8') > MAX_FRAME_BYTES) {
    throw new Error('后端消息超过 1 MiB 帧大小上限');
  }
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    throw new Error('后端输出了无效 JSON');
  }
  if (!record(value) || value.v !== 1 || typeof value.type !== 'string' ||
      !(value.id === null || validId(value.id))) {
    throw new Error('后端协议版本或消息标识无效');
  }
  let valid = false;
  switch (value.type) {
    case 'ready':
      valid = value.id === null && typeof value.workspace === 'string' &&
        typeof value.model === 'string' && typeof value.session_id === 'string' &&
        Array.isArray(value.capabilities) && value.capabilities.every(item => typeof item === 'string') &&
        (value.provider === undefined || typeof value.provider === 'string') &&
        (value.session_path === undefined || typeof value.session_path === 'string') &&
        (value.session_purpose === undefined || value.session_purpose === 'user' || value.session_purpose === 'test') &&
        (value.resumed === undefined || typeof value.resumed === 'boolean');
      break;
    case 'history_message': valid = value.id === null && message(value.message); break;
    case 'notice': valid = value.id === null && typeof value.message === 'string'; break;
    case 'command_result':
      valid = validId(value.id) && BACKEND_COMMANDS.includes(value.command as BackendCommand) && typeof value.text === 'string';
      break;
    case 'metrics':
      valid = (validId(value.id) || (value.id === null && value.used_tokens === 0 &&
        value.cost_usd === 0 && value.context_tokens !== null)) &&
        ['used_tokens', 'limit_tokens', 'context_window', 'done', 'planned'].every(key =>
          typeof value[key] === 'number' && Number.isSafeInteger(value[key]) && (value[key] as number) >= 0) &&
        (value.context_tokens === null || (typeof value.context_tokens === 'number' && Number.isSafeInteger(value.context_tokens) && value.context_tokens >= 0)) &&
        typeof value.estimated === 'boolean' && typeof value.cost_usd === 'number' && Number.isFinite(value.cost_usd) && value.cost_usd >= 0;
      break;
    case 'context_snapshot':
      valid = contextSnapshot(value.snapshot) && (value.snapshot.phase === 'loaded' ? value.id === null : validId(value.id));
      break;
    case 'diagnostic': valid = value.id === null && typeof value.message === 'string'; break;
    case 'status':
      valid = ['starting', 'idle', 'running', 'waiting_approval', 'stopping'].includes(String(value.phase)) &&
        (value.phase !== 'starting' || validId(value.id));
      break;
    case 'text_delta': valid = validId(value.id) && typeof value.delta === 'string'; break;
    case 'tool_call': valid = validId(value.id) && toolCall(value.call); break;
    case 'tool_result':
      valid = validId(value.id) && toolCall(value.call) && typeof value.ok === 'boolean' && typeof value.output === 'string' &&
        (value.execution === undefined || execution(value.execution));
      break;
    case 'approval_request':
      valid = validId(value.id) && validId(value.approval_id) && toolCall(value.call) && typeof value.preview === 'string' &&
        (value.decisions === undefined || (Array.isArray(value.decisions) && value.decisions.includes('deny') &&
          value.decisions.every(decision => approvalDecisions.includes(String(decision))) &&
          new Set(value.decisions).size === value.decisions.length));
      break;
    case 'approval_resolved':
      valid = validId(value.id) && validId(value.approval_id) && approvalDecisions.includes(String(value.decision));
      break;
    case 'done': valid = validId(value.id) && runResult(value.result); break;
    case 'ack': valid = validId(value.id) && ['cancel', 'approval', 'steer'].includes(String(value.action)); break;
    case 'closed': valid = true; break;
    case 'error': valid = typeof value.code === 'string' && typeof value.message === 'string'; break;
  }
  if (!valid) throw new Error(`后端消息字段无效：${value.type}`);
  return value as ServerEvent;
}

const booleanArgs = new Set(['--fake', '--chat-only', '--continue', '--list-sessions', '--test-session', '--sandbox', '--sandbox-network']);
const valueArgs = new Set(['--workspace', '--provider', '--model', '--base-url', '--max-steps', '--session', '--sandbox-network-allow']);

export function validateClientArgs(args: readonly string[]): string[] {
  const checked: string[] = [];
  const sessionModes = new Set<string>();
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index]!;
    if (['--continue', '--session', '--list-sessions'].includes(arg)) {
      sessionModes.add(arg);
      if (sessionModes.size > 1) throw new Error('--continue、--session、--list-sessions 不能同时使用');
    }
    if (booleanArgs.has(arg)) {
      checked.push(arg);
      continue;
    }
    if (!valueArgs.has(arg)) {
      throw new Error(`Ink 入口不支持参数 ${arg}；使用 --help 查看支持项`);
    }
    const value = args[index + 1];
    if (value === undefined || value.startsWith('--') || value.length === 0) {
      throw new Error(`参数 ${arg} 缺少值`);
    }
    if (arg === '--max-steps' && (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)))) {
      throw new Error('--max-steps 必须是非负整数（0 表示不限）');
    }
    checked.push(arg, value);
    index += 1;
  }
  return checked;
}
