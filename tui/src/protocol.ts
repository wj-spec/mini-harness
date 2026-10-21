export const MAX_FRAME_BYTES = 1024 * 1024;

export interface ToolCall {
  id: string | null;
  name: string;
  args: Record<string, unknown>;
}

export type ApprovalDecision = 'once' | 'session' | 'workspace' | 'deny';
export const BACKEND_COMMANDS = ['resume', 'permissions', 'permissions_reset', 'cost', 'tools', 'diff', 'mcp'] as const;
export type BackendCommand = typeof BACKEND_COMMANDS[number];

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

type Envelope<T extends string, P = Record<never, never>> = {
  v: 1;
  type: T;
  id: string | null;
} & P;

export type ServerEvent =
  | Envelope<'ready', {workspace: string; model: string; session_id: string; capabilities: string[]; provider?: string; session_path?: string; resumed?: boolean}>
  | Envelope<'history_message', {message: PersistedMessage}>
  | Envelope<'notice', {message: string}>
  | Envelope<'command_result', {command: BackendCommand; text: string}>
  | Envelope<'metrics', RunMetrics>
  | Envelope<'diagnostic', {message: string}>
  | Envelope<'status', {phase: 'starting' | 'idle' | 'running' | 'waiting_approval' | 'stopping'}>
  | Envelope<'text_delta', {delta: string}>
  | Envelope<'tool_call', {call: ToolCall}>
  | Envelope<'tool_result', {call: ToolCall; ok: boolean; output: string; execution?: CommandExecution}>
  | Envelope<'approval_request', {approval_id: string; call: ToolCall; preview: string; decisions?: ApprovalDecision[]}>
  | Envelope<'approval_resolved', {approval_id: string; decision: ApprovalDecision}>
  | Envelope<'done', {result: RunResult}>
  | Envelope<'ack', {action: 'cancel' | 'approval'}>
  | Envelope<'closed'>
  | Envelope<'error', {code: string; message: string}>;

export type ClientRequest = (
  | Envelope<'run', {text: string}>
  | Envelope<'command', {command: BackendCommand}>
  | Envelope<'cancel', {target: string}>
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
        (value.resumed === undefined || typeof value.resumed === 'boolean');
      break;
    case 'history_message': valid = value.id === null && message(value.message); break;
    case 'notice': valid = value.id === null && typeof value.message === 'string'; break;
    case 'command_result':
      valid = validId(value.id) && BACKEND_COMMANDS.includes(value.command as BackendCommand) && typeof value.text === 'string';
      break;
    case 'metrics':
      valid = validId(value.id) &&
        ['used_tokens', 'limit_tokens', 'context_window', 'done', 'planned'].every(key =>
          typeof value[key] === 'number' && Number.isSafeInteger(value[key]) && (value[key] as number) >= 0) &&
        (value.context_tokens === null || (typeof value.context_tokens === 'number' && Number.isSafeInteger(value.context_tokens) && value.context_tokens >= 0)) &&
        typeof value.estimated === 'boolean' && typeof value.cost_usd === 'number' && Number.isFinite(value.cost_usd) && value.cost_usd >= 0;
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
    case 'ack': valid = validId(value.id) && ['cancel', 'approval'].includes(String(value.action)); break;
    case 'closed': valid = true; break;
    case 'error': valid = typeof value.code === 'string' && typeof value.message === 'string'; break;
  }
  if (!valid) throw new Error(`后端消息字段无效：${value.type}`);
  return value as ServerEvent;
}

const booleanArgs = new Set(['--fake', '--chat-only', '--continue']);
const valueArgs = new Set(['--workspace', '--provider', '--model', '--base-url', '--max-steps']);

export function validateClientArgs(args: readonly string[]): string[] {
  const checked: string[] = [];
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index]!;
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
