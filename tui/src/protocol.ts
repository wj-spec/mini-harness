import {validInspectionSnapshot, validFileSource, type FileSource, type InspectionSnapshot} from './inspection.js';

export const MAX_FRAME_BYTES = 1024 * 1024;

export interface ToolCall {
  id: string | null;
  name: string;
  args: Record<string, unknown>;
}

export type ApprovalDecision = 'once' | 'session' | 'workspace' | 'deny';
export const TOOL_TIERS = ['read', 'write', 'exec', 'network', 'remote_action'] as const;
export type ToolTier = typeof TOOL_TIERS[number];
const validToolTier = (value: unknown) => typeof value === 'string' && TOOL_TIERS.includes(value as ToolTier);
export type SessionPurpose = 'user' | 'test';
export type AutonomyLevel = 'ask' | 'edits' | 'workspace' | 'full';
export const AUTONOMY_LEVELS = ['ask', 'edits', 'workspace', 'full'] as const;
export const AUTONOMY_LABELS: Record<AutonomyLevel, string> = {
  ask: '每次询问',
  edits: '自动改文件（工作区）',
  workspace: '工作区自动（写入+执行）',
  full: '完全执行（写入+执行+网络）',
};
export const BACKEND_COMMANDS = ['resume', 'permissions', 'permissions_reset', 'cost', 'tools', 'diff', 'mcp', 'skills', 'evidence', 'autonomy', 'tasks', 'approvals', 'plan', 'explore', 'notifications', 'trace', 'priority', 'queue', 'inspections', 'sessions', 'search', 'rounds'] as const;
export {COMMAND_COMPLETIONS, COMMAND_DESCRIPTIONS, commandMatches} from './commands.js';
export type BackendCommand = typeof BACKEND_COMMANDS[number];
export type SkillsCommandArgs = {action?: 'list' | 'refresh' | 'activate' | 'unload'; name?: string};
export type InspectionHistoryArgs = {cursor?: string; task_id?: string};
export type TaskCatalogArgs = {cursor?: string};
export type SessionCatalogArgs = TaskCatalogArgs;
export type PermissionCommandArgs = {action: 'list'} | {action: 'revoke'; grant_id: string};
export type PermissionGrant = {id: string; kind: 'workspace' | 'session'; scope: 'workspace' | 'session';
  tool: string | null; target: string; workspace: string; validity: 'process'; revocable: true};
export type PermissionPersistent = {source: 'user' | 'project'; path: string; rule: string;
  validity: 'file'; revocable: false; reason: string};
export type PermissionOverview = {
  autonomy: {level: string; label: string; source: 'cli' | 'user-policy' | 'default'; requested: string; cap: string | null};
  grants: PermissionGrant[]; persistent: PermissionPersistent[]; error: string | null;
  revoked: {id: string; kind: 'workspace' | 'session'; scope: string; tool: string | null; target: string} | null;
  workspace: string; session_id: string;
};
export const permissionGrantId = (value: unknown): value is string => typeof value === 'string' && /^(?:ws|ss)-[0-9a-f]{12}$/.test(value);
export function validatePermissionArgs(value: unknown): asserts value is PermissionCommandArgs {
  if (!record(value)) throw new Error('权限参数无效');
  if (value.action === 'list' && Object.keys(value).length === 1) return;
  if (value.action === 'revoke' && Object.keys(value).length === 2 && permissionGrantId(value.grant_id)) return;
  throw new Error('权限参数只支持 {action:list} 或 {action:revoke,grant_id}');
}
export function validPermissionOverview(value: unknown): value is PermissionOverview {
  const bounded = (v: unknown, limit: number) => typeof v === 'string' && [...v].length <= limit;
  if (!record(value) || !record(value.autonomy)) return false;
  const autonomy = value.autonomy;
  if (!['cli', 'user-policy', 'default'].includes(String(autonomy.source)) ||
      !bounded(autonomy.level, 32) || !bounded(autonomy.label, 64) || !bounded(autonomy.requested, 32) ||
      (autonomy.cap !== null && !bounded(autonomy.cap, 32))) return false;
  if (!Array.isArray(value.grants) || value.grants.length > 100) return false;
  if (!value.grants.every(item => record(item) && permissionGrantId(item.id) &&
      ['workspace', 'session'].includes(String(item.kind)) && ['workspace', 'session'].includes(String(item.scope)) &&
      (item.tool === null || bounded(item.tool, 128)) && bounded(item.target, 512) && bounded(item.workspace, 1024) &&
      item.validity === 'process' && item.revocable === true)) return false;
  if (!Array.isArray(value.persistent) || value.persistent.length > 100) return false;
  if (!value.persistent.every(item => record(item) && ['user', 'project'].includes(String(item.source)) &&
      bounded(item.path, 1024) && bounded(item.rule, 1024) && item.validity === 'file' &&
      item.revocable === false && bounded(item.reason, 512))) return false;
  if (value.error !== null && !bounded(value.error, 1000)) return false;
  if (value.revoked !== null && (typeof value.revoked !== 'object' || !record(value.revoked) ||
      !permissionGrantId(value.revoked.id) || !['workspace', 'session'].includes(String(value.revoked.kind)) ||
      (value.revoked.tool !== null && !bounded(value.revoked.tool, 128)) || !bounded(value.revoked.target, 512))) return false;
  return bounded(value.workspace, 1024) && bounded(value.session_id, 128);
}
export type SearchCommandArgs = {query: string; cursor?: string};
export type HistorySearchMatch = {session_id: string; identity: string; title: string; updated_at: string;
  role: 'user' | 'assistant' | 'tool'; snippet: string; ts: string | null; message_index: number};
export type HistorySearchPage = {workspace: string; session_id: string; purpose: SessionPurpose; query: string;
  matches: HistorySearchMatch[]; next_cursor: string | null; skipped: number; truncated: boolean};
export const searchCursor = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_-]{1,512}$/.test(value);
export function validateSearchArgs(value: unknown): asserts value is SearchCommandArgs {
  if (!record(value) || Object.keys(value).some(key => key !== 'query' && key !== 'cursor')) throw new Error('检索参数无效');
  if (typeof value.query !== 'string' || !value.query.trim() || [...value.query].length > 200) throw new Error('检索关键词须为 1-200 个字符');
  if (value.cursor !== undefined && !searchCursor(value.cursor)) throw new Error('检索游标无效');
}
export function validHistorySearchPage(value: unknown): value is HistorySearchPage {
  const text = (item: unknown, limit: number) => typeof item === 'string' && [...item].length <= limit;
  if (!record(value) || typeof value.workspace !== 'string' || !validId(value.session_id) ||
      !['user', 'test'].includes(String(value.purpose)) || !text(value.query, 200) ||
      value.next_cursor !== null && !searchCursor(value.next_cursor) ||
      !Number.isSafeInteger(value.skipped) || Number(value.skipped) < 0 || typeof value.truncated !== 'boolean' ||
      !Array.isArray(value.matches) || value.matches.length > 20) return false;
  return value.matches.every(item => record(item) && validId(item.session_id) &&
    typeof item.identity === 'string' && /^[0-9a-f]{64}$/.test(item.identity) &&
    text(item.title, 200) && text(item.updated_at, 128) && ['user', 'assistant', 'tool'].includes(String(item.role)) &&
    text(item.snippet, 280) && (item.ts === null || text(item.ts, 128)) &&
    Number.isSafeInteger(item.message_index) && Number(item.message_index) >= 0);
}
export type RoundsLedgerRound = {index: number; run_id: string; goal: string; goal_truncated: boolean;
  task_kind: string | null; status: string; stop_reason: string | null; steps: number; cost_usd: number;
  verified: boolean | null; saved_at: string | null};
export type RoundsLedgerPage = {workspace: string; session_id: string; purpose: SessionPurpose; total: number;
  skipped: number; truncated: boolean; rounds: RoundsLedgerRound[]};
export function validRoundsLedgerPage(value: unknown): value is RoundsLedgerPage {
  const text = (item: unknown, limit: number) => typeof item === 'string' && [...item].length <= limit;
  if (!record(value) || typeof value.workspace !== 'string' || !validId(value.session_id) ||
      !['user', 'test'].includes(String(value.purpose)) || !Number.isSafeInteger(value.total) || Number(value.total) < 0 ||
      !Number.isSafeInteger(value.skipped) || Number(value.skipped) < 0 || typeof value.truncated !== 'boolean' ||
      !Array.isArray(value.rounds) || value.rounds.length > 50) return false;
  return value.rounds.every(item => record(item) && Number.isSafeInteger(item.index) && Number(item.index) >= 1 &&
    text(item.run_id, 128) && text(item.goal, 1000) && typeof item.goal_truncated === 'boolean' &&
    (item.task_kind === null || text(item.task_kind, 32)) && text(item.status, 32) &&
    (item.stop_reason === null || text(item.stop_reason, 200)) &&
    Number.isSafeInteger(item.steps) && Number(item.steps) >= 0 &&
    typeof item.cost_usd === 'number' && Number.isFinite(item.cost_usd) && item.cost_usd >= 0 &&
    (item.verified === null || typeof item.verified === 'boolean') && (item.saved_at === null || text(item.saved_at, 128)));
}
export type SessionCatalogRow = {session_id: string; identity: string; title: string; updated_at: string;
  message_count: number; preview: string; truncated: boolean};
export type SessionCatalogPage = {workspace: string; session_id: string; purpose: SessionPurpose; records: SessionCatalogRow[];
  next_cursor: string | null; skipped: number};
export function validSessionCatalogPage(value: unknown): value is SessionCatalogPage {
  const text = (v: unknown, length: number) => typeof v === 'string' && [...v].length <= length;
  return record(value) && typeof value.workspace === 'string' && validId(value.session_id) &&
    ['user', 'test'].includes(String(value.purpose)) && (value.next_cursor === null || taskCatalogCursor(value.next_cursor)) &&
    Number.isSafeInteger(value.skipped) && Number(value.skipped) >= 0 &&
    Array.isArray(value.records) && value.records.length <= 5 && value.records.every(item => record(item) &&
      validId(item.session_id) && typeof item.identity === 'string' && /^[0-9a-f]{64}$/.test(item.identity) &&
      text(item.title, 1000) && text(item.updated_at, 128) && text(item.preview, 6000) &&
      Number.isSafeInteger(item.message_count) && Number(item.message_count) >= 0 && typeof item.truncated === 'boolean');
}
export function validateSessionCatalogArgs(value: unknown): asserts value is SessionCatalogArgs {
  if (!record(value) || Object.keys(value).some(key => key !== 'cursor') ||
      'cursor' in value && !taskCatalogCursor(value.cursor)) throw new Error('会话目录参数无效');
}
export type TaskCatalogRow = {task_id: string; objective: string; kind: string; mode: string; status: string; priority: number;
  controls?: {attach: 'live' | 'offline' | null; attach_reason: string | null; stop: boolean; stop_reason: string | null};
  created_at: string; updated_at: string; pending_approvals: number | null; worker_error: string | null;
  worker: {status: string; foreign_host: boolean; pid_alive: boolean | null; stale: boolean | null} | null};
export type TaskCatalogPage = {workspace: string; session_id: string; records: TaskCatalogRow[];
  next_cursor: string | null; skipped: number; problems: string[]};
export const taskCatalogCursor = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_-]{1,512}$/.test(value);
export function validateTaskCatalogArgs(value: unknown): asserts value is TaskCatalogArgs {
  if (!record(value) || Object.keys(value).some(key => key !== 'cursor') ||
      'cursor' in value && !taskCatalogCursor(value.cursor)) throw new Error('任务目录参数无效');
}
export function validTaskCatalogPage(value: unknown): value is TaskCatalogPage {
  const boundedText = (text: unknown, limit: number) => typeof text === 'string' && [...text].length <= limit;
  const shortText = (text: unknown) => typeof text === 'string' && text.length > 0 && boundedText(text, 64);
  if (!record(value) || typeof value.workspace !== 'string' || !validId(value.session_id) ||
      value.next_cursor !== null && !taskCatalogCursor(value.next_cursor) ||
      !Number.isSafeInteger(value.skipped) || Number(value.skipped) < 0 ||
      !Array.isArray(value.problems) || value.problems.length > 3 || value.problems.some(text => !boundedText(text, 500)) ||
      !Array.isArray(value.records) || value.records.length > 5) return false;
  return value.records.every(item => record(item) && validId(item.task_id) && typeof item.objective === 'string' &&
    Boolean(item.objective.trim()) && boundedText(item.objective, 4000) &&
    ['kind', 'mode', 'status', 'created_at', 'updated_at'].every(key => shortText(item[key])) &&
    Number.isSafeInteger(item.priority) && Number(item.priority) >= 0 && Number(item.priority) <= 100 &&
    (item.pending_approvals === null || Number.isSafeInteger(item.pending_approvals) && Number(item.pending_approvals) >= 0) &&
    (item.worker_error === null || boundedText(item.worker_error, 500)) &&
    (item.controls === undefined || record(item.controls) && [null, 'live', 'offline'].includes(item.controls.attach as string | null) &&
      typeof item.controls.stop === 'boolean' && (item.controls.attach_reason === null || boundedText(item.controls.attach_reason, 500)) &&
      (item.controls.stop_reason === null || boundedText(item.controls.stop_reason, 500))) &&
    (item.worker === null || record(item.worker) && shortText(item.worker.status) && typeof item.worker.foreign_host === 'boolean' &&
      (item.worker.foreign_host ? item.worker.pid_alive === null && item.worker.stale === null :
        typeof item.worker.pid_alive === 'boolean' && typeof item.worker.stale === 'boolean'))) &&
    new Set(value.records.map(item => item.task_id)).size === value.records.length;
}
export type InspectionHistoryPage = {workspace: string; session_id: string; task_id?: string; records: InspectionSnapshot[]; next_cursor: string | null; skipped: number};
export const inspectionCursor = (value: unknown): value is string => typeof value === 'string' && /^\d{20}-[0-9a-f]{64}\.json$/.test(value);
export function validateInspectionArgs(value: unknown): asserts value is InspectionHistoryArgs {
  if (!record(value) || Object.keys(value).some(key => !['cursor', 'task_id'].includes(key)) ||
      value.cursor !== undefined && !inspectionCursor(value.cursor) || value.task_id !== undefined && !validId(value.task_id)) throw new Error('历史查询参数无效');
}
export type AutonomyCommandArgs = {level: AutonomyLevel};
export interface ReviewRef {
  session_id: string;
  plan_task_id: string;
  plan_id: string;
  plan_version: number;
  plan_hash: string;
}
export function reviewKey(target: ReviewRef): string {
  return JSON.stringify([target.session_id, target.plan_task_id, target.plan_id, target.plan_version, target.plan_hash]);
}
export type ReviewAction = 'approve-execute' | 'approve-only' | 'dismiss';
export type PlanReviewArgs = {action: 'review'; target?: ReviewRef; cursor?: number}
  | {action: 'approve-execute' | 'approve' | 'dismiss'; target: ReviewRef};
export type PlanRevisionArgs = {action: 'request-changes' | 'query-revision'; target: ReviewRef; feedback: string}
  | {action: 'resume-revision'; target: ReviewRef; feedback: string; expected_segment: number};
export type PlanRevisionHistoryArgs = {action: 'list-revisions'; target: ReviewRef; cursor?: string};
export type PlanCommandArgs = PlanReviewArgs | PlanRevisionArgs | PlanRevisionHistoryArgs | {action?: 'show' | 'list' | 'step' | 'edit-step' | 'diff' | 'acceptance' | 'card' | 'prepare-background' | 'approve' | 'reject' | 'revise' | 'mode-on' | 'mode-off' | 'mode-status' | 'request-changes' | 'dismiss' | 'markdown'; objective?: string; step_id?: string; title?: string; kind?: 'read' | 'edit' | 'run' | 'verify'; files?: string[]; depends_on?: string[]; version_a?: number; version_b?: number; feedback?: string};

export interface PlanRevisionResult {
  outcome: 'absent' | 'pending' | 'running' | 'paused' | 'incomplete' | 'submitted' | 'unavailable';
  code: string | null; message: string; target: ReviewRef | null; result_target: ReviewRef | null;
  revision_id: string | null; feedback: string;
  task: {task_id: string; status: string; segment: number; run_id: string | null; run_started: boolean} | null;
}

export interface PlanRevisionHistory {
  target: ReviewRef | null; records: PlanRevisionResult[]; next_cursor: string | null;
  code: string | null; message: string;
}

interface ReviewSummary {
  objective: string; status: string; executable: boolean;
  step_count: number; criterion_count: number; question_count: number;
}
interface ReviewContent {
  target: ReviewRef | null; replacement_target: ReviewRef | null;
  code: string | null; message: string; summary: ReviewSummary | null;
  body_page: string; next_cursor: number | null; available_actions: ReviewAction[];
}
export type PlanReviewResult = ReviewContent & (
  | {outcome: 'ready' | 'approved' | 'dismissed'; target: ReviewRef; summary: ReviewSummary; launch: null}
  | {outcome: 'unavailable'; code: string; launch: null; available_actions: []}
  | {outcome: 'approved_not_started'; target: ReviewRef; launch: {
    task_id: string; run_id: null; segment: 0; status: string; run_started: false}}
  | {outcome: 'launched'; target: ReviewRef; available_actions: []; launch: {
    task_id: string; run_id: string; segment: number; status: string; run_started: boolean}}
);
export type ExploreCommandArgs = {prompt: string};
export type NotificationsCommandArgs = {kind?: string};
export type TraceCommandArgs = {hours?: number};
export type PriorityCommandArgs = {task_id: string; priority: number};
export type CommandArgs = SkillsCommandArgs | AutonomyCommandArgs | PlanCommandArgs | ExploreCommandArgs | NotificationsCommandArgs | TraceCommandArgs | PriorityCommandArgs | InspectionHistoryArgs | TaskCatalogArgs | PermissionCommandArgs | SearchCommandArgs;
export function validateNotificationsArgs(value: unknown): asserts value is NotificationsCommandArgs {
  if (!record(value) || Object.keys(value).some(key => key !== 'kind') ||
      (value.kind !== undefined && (typeof value.kind !== 'string' || !value.kind.trim()))) {
    throw new Error('notifications 参数只允许非空 kind');
  }
}
export function validatePriorityArgs(value: unknown): asserts value is PriorityCommandArgs {
  if (!record(value) || Object.keys(value).some(key => key !== 'task_id' && key !== 'priority') ||
      typeof value.task_id !== 'string' || !value.task_id.trim() ||
      typeof value.priority !== 'number' || !Number.isSafeInteger(value.priority) ||
      value.priority < 0 || value.priority > 100) {
    throw new Error('priority 参数只允许非空 task_id 与 0-100 的 priority');
  }
}
export function validateTraceArgs(value: unknown): asserts value is TraceCommandArgs {
  if (!record(value) || Object.keys(value).some(key => key !== 'hours') ||
      (value.hours !== undefined &&
       (typeof value.hours !== 'number' || !Number.isFinite(value.hours) || value.hours <= 0 || value.hours > 8760))) {
    throw new Error('trace 参数只允许 0-8760 的 hours');
  }
}
export function validateExploreArgs(value: unknown): asserts value is ExploreCommandArgs {
  if (!record(value) || Object.keys(value).some(key => key !== 'prompt') ||
      typeof value.prompt !== 'string' || !value.prompt.trim()) {
    throw new Error('explore 参数只允许非空的 prompt');
  }
}
export function validatePlanArgs(value: unknown): asserts value is PlanCommandArgs {
  if (record(value) && value.action === 'list-revisions') {
    if (Object.keys(value).some(key => !['action', 'target', 'cursor'].includes(key)) || !reviewRef(value.target) ||
        ('cursor' in value && (typeof value.cursor !== 'string' || !/^[0-9a-f]{64}$/.test(value.cursor)))) {
      throw new Error('修订记录需要准确 target 和有效 cursor');
    }
    return;
  }
  if (record(value) && (value.action === 'query-revision' || value.action === 'resume-revision' ||
      (value.action === 'request-changes' && 'target' in value))) {
    const resume = value.action === 'resume-revision';
    const keys = resume ? ['action', 'target', 'feedback', 'expected_segment'] : ['action', 'target', 'feedback'];
    if (Object.keys(value).length !== keys.length || Object.keys(value).some(key => !keys.includes(key)) ||
        !reviewRef(value.target) || typeof value.feedback !== 'string' || !value.feedback.trim() ||
        Array.from(value.feedback.trim()).length > 4000 ||
        (resume && (!nonnegativeInteger(value.expected_segment) || value.expected_segment < 1))) {
      throw new Error('plan 修订需要准确 target、1–4000 字反馈；续做须指定已观察的 segment');
    }
    return;
  }
  if (record(value) && (value.action === 'review' || value.action === 'approve-execute' || 'target' in value)) {
    const query = value.action === 'review';
    const keys = query ? ['action', 'target', 'cursor'] : ['action', 'target'];
    if (!['review', 'approve-execute', 'approve', 'dismiss'].includes(String(value.action)) ||
        Object.keys(value).some(key => !keys.includes(key)) ||
        ((!query || 'target' in value) && !reviewRef(value.target)) ||
        ('cursor' in value && (!nonnegativeInteger(value.cursor) || (!value.target && value.cursor !== 0)))) {
      throw new Error('plan 参数无效：审阅动作需要准确 target 和有效 cursor');
    }
    return;
  }
  if (!record(value) || Object.keys(value).some(key => !['action', 'objective', 'step_id', 'title', 'kind', 'files', 'depends_on', 'version_a', 'version_b', 'feedback'].includes(key)) ||
      (value.action !== undefined && !['show', 'list', 'step', 'edit-step', 'diff', 'acceptance', 'card', 'prepare-background', 'approve', 'reject', 'revise', 'mode-on', 'mode-off', 'mode-status', 'request-changes', 'dismiss', 'markdown'].includes(String(value.action))) ||
      (value.objective !== undefined && (typeof value.objective !== 'string' || !value.objective.trim())) ||
      (value.step_id !== undefined && (typeof value.step_id !== 'string' || !value.step_id.trim())) ||
      (value.kind !== undefined && !['read', 'edit', 'run', 'verify'].includes(String(value.kind))) ||
      (value.files !== undefined && (!Array.isArray(value.files) || value.files.length === 0 ||
        value.files.length > 20 || value.files.some(item => typeof item !== 'string' || !item.trim()))) ||
      (value.depends_on !== undefined && (!Array.isArray(value.depends_on) || value.depends_on.length > 20 ||
        value.depends_on.some(item => typeof item !== 'string' || !item.trim()))) ||
      (value.version_a !== undefined && (typeof value.version_a !== 'number' || !Number.isSafeInteger(value.version_a) || value.version_a < 1)) ||
      (value.version_b !== undefined && (typeof value.version_b !== 'number' || !Number.isSafeInteger(value.version_b) || value.version_b < 1)) ||
      (value.feedback !== undefined && (typeof value.feedback !== 'string' || !value.feedback.trim() || value.action !== 'request-changes')) ||
      (value.action === 'revise' && typeof value.objective !== 'string') ||
      (value.action === 'step' && typeof value.step_id !== 'string') ||
      (value.action === 'request-changes' && typeof value.feedback !== 'string') ||
      (value.action === 'diff' && (typeof value.version_a !== 'number' || typeof value.version_b !== 'number'))) {
    throw new Error('plan 参数无效：支持 show|list|step|edit-step|diff|acceptance|card|prepare-background|approve|reject|revise|mode-on|mode-off|mode-status|request-changes|dismiss|markdown');
  }
}
export function validateAutonomyArgs(value: unknown): asserts value is AutonomyCommandArgs {
  if (!record(value) || Object.keys(value).some(key => key !== 'level') ||
      !AUTONOMY_LEVELS.includes(value.level as AutonomyLevel)) {
    throw new Error('autonomy 参数只允许 level=ask|edits|workspace|full');
  }
}
export interface SkillInfo {name: string; summary: string; active: boolean; source?: string}

function skillInfo(value: unknown): value is SkillInfo {
  return record(value) && typeof value.name === 'string' && value.name.length > 0 && value.name.length <= 200 &&
    typeof value.summary === 'string' && value.summary.length <= 2000 &&
    typeof value.active === 'boolean' &&
    (value.source === undefined || (typeof value.source === 'string' && value.source.length <= 2000));
}

function skillList(value: unknown): value is SkillInfo[] {
  return Array.isArray(value) && value.length <= 500 && value.every(skillInfo);
}

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

export interface ApprovalRule {
  source: string;
  tool: string;
  decision?: string | null;
  scopes?: string[] | null;
  note?: string;
  command_prefix?: string[];
}

export interface CompletionSummary {
  allowed: boolean;
  codes: string[];
  messages: string[];
}

export interface AuditSummary {
  status: string;
  integrity: string;
  verified: boolean;
  findings?: string[];
  unattributed?: string[];
  changed_files?: number;
  coverage?: string;
  contract_audit?: string;
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
  verified?: boolean | null;
  completion?: CompletionSummary | null;
  audit?: AuditSummary | null;
}

export type RunTaskMeta = {
  kind: 'goal' | 'run';
  mode?: 'chat' | 'act' | 'goal' | 'plan';
  objective?: string;
  task_id?: string;
  goal_id?: string;
  plan_id?: string;
  plan_version?: number;
};

export interface TaskHint {
  task_id: string;
  kind: string;
  status: string;
  worker_status: string | null;
  stale: boolean;
  mode?: string;
  parent_id?: string | null;
  plan_id?: string | null;
  pending_approvals?: number;
  priority?: number;
}

export interface AttachTaskSnapshot {
  task_id: string;
  kind: string;
  status: string;
  segment: number;
  run_id: string | null;
  objective: string;
  goal_id: string | null;
  mode?: string;
  plan_id?: string | null;
  plan_version?: number | null;
  worktree: {path: string; branch: string; base_commit: string} | null;
}

export interface AttachApprovalItem {
  approval_id: string;
  tool: string;
  tier: string | null;
  requester: string;
  expires_at: string | null;
}

export interface TaskSnapshot {
  task_id: string;
  kind: string;
  status: string;
  segment: number;
  objective: string;
  goal_id: string | null;
  updated_at: string;
  mode?: string;
  plan_id?: string | null;
  plan_version?: number | null;
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

export interface PlanModeState {
  active: boolean;
  pending: boolean;
  wanted: boolean | null;
}

type Envelope<T extends string, P = Record<never, never>> = {
  v: 1;
  type: T;
  id: string | null;
} & P;

export type ServerEvent =
  | Envelope<'ready', {workspace: string; model: string; session_id: string; capabilities: string[]; file_source?: FileSource | null; provider?: string; session_path?: string; session_purpose?: SessionPurpose; session_identity?: string; resumed?: boolean; skills?: SkillInfo[]; autonomy?: string; autonomy_label?: string; tasks?: TaskHint[]}>
  | Envelope<'plan_mode', {mode: PlanModeState}>
  | Envelope<'plan_review', PlanReviewResult>
  | Envelope<'plan_revision', PlanRevisionResult>
  | Envelope<'plan_revision_history', PlanRevisionHistory>
  | Envelope<'inspection_history', InspectionHistoryPage>
  | Envelope<'task_catalog', TaskCatalogPage>
  | Envelope<'session_catalog', SessionCatalogPage>
  | Envelope<'permission_overview', PermissionOverview>
  | Envelope<'history_search', HistorySearchPage>
  | Envelope<'rounds_ledger', RoundsLedgerPage>
  | Envelope<'history_message', {message: PersistedMessage}>
  | Envelope<'notice', {message: string}>
  | Envelope<'command_result', {command: BackendCommand; text: string; skills?: SkillInfo[]; autonomy?: string}>
  | Envelope<'metrics', RunMetrics>
  | Envelope<'context_snapshot', {snapshot: ContextSnapshot}>
  | Envelope<'diagnostic', {message: string}>
  | Envelope<'status', {phase: 'starting' | 'idle' | 'running' | 'waiting_approval' | 'stopping'}>
  | Envelope<'text_delta', {delta: string}>
  | Envelope<'tool_call', {call: ToolCall; tier?: ToolTier}>
  | Envelope<'tool_result', {call: ToolCall; ok: boolean; output: string; execution?: CommandExecution}>
  | Envelope<'approval_request', {approval_id: string; call: ToolCall; preview: string; decisions?: ApprovalDecision[]; tier?: string; tier_label?: string; rule?: ApprovalRule}>
  | Envelope<'approval_resolved', {approval_id: string; decision: ApprovalDecision}>
  | Envelope<'done', {result: RunResult}>
  | Envelope<'task_state', {task: TaskSnapshot}>
  | Envelope<'task_snapshot', {task: AttachTaskSnapshot; approvals: AttachApprovalItem[]; file_source?: FileSource | null}>
  | Envelope<'approval_list', {approvals: AttachApprovalItem[]}>
  | Envelope<'subagent_event', {task_id: string; status: string; summary?: string}>
  | Envelope<'ack', {action: 'cancel' | 'approval' | 'steer'}>
  | Envelope<'closed'>
  | Envelope<'error', {code: string; message: string}>;

export type ClientRequest = (
  | Envelope<'run', {text: string; task?: RunTaskMeta}>
  | Envelope<'command', {command: Exclude<BackendCommand, 'skills' | 'autonomy' | 'plan' | 'explore' | 'notifications' | 'trace' | 'priority' | 'inspections' | 'tasks'>}>
  | Envelope<'command', {command: 'tasks' | 'sessions'; args?: TaskCatalogArgs}>
  | Envelope<'command', {command: 'permissions'; args?: PermissionCommandArgs}>
  | Envelope<'command', {command: 'search'; args: SearchCommandArgs}>
  | Envelope<'command', {command: 'rounds'; args?: Record<string, never>}>
  | Envelope<'command', {command: 'inspections'; args?: InspectionHistoryArgs}>
  | Envelope<'command', {command: 'skills'; args?: SkillsCommandArgs}>
  | Envelope<'command', {command: 'autonomy'; args: AutonomyCommandArgs}>
  | Envelope<'command', {command: 'plan'; args?: PlanCommandArgs}>
  | Envelope<'command', {command: 'explore'; args: ExploreCommandArgs}>
  | Envelope<'command', {command: 'notifications'; args?: NotificationsCommandArgs}>
  | Envelope<'command', {command: 'trace'; args?: TraceCommandArgs}>
  | Envelope<'command', {command: 'priority'; args: PriorityCommandArgs}>
  | Envelope<'cancel', {target: string}>
  | Envelope<'steer', {target: string; text: string}>
  | Envelope<'approval', {approval_id: string; decision: ApprovalDecision}>
  | Envelope<'approval_decide', {approval_id: string; decision: ApprovalDecision}>
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

export function execution(value: unknown): value is CommandExecution {
  if (!record(value) || !['succeeded', 'failed', 'timed_out', 'cancelled', 'unknown'].includes(String(value.status)) ||
      !(value.exit_code === null || (typeof value.exit_code === 'number' && Number.isSafeInteger(value.exit_code)))) return false;
  if (value.status === 'succeeded') return value.exit_code === 0;
  if (value.status === 'failed') return typeof value.exit_code === 'number' && value.exit_code !== 0;
  if (value.status === 'unknown') return value.exit_code === null;
  return true;
}

function stringList(value: unknown, maxLength = 50): value is string[] {
  return Array.isArray(value) && value.length <= maxLength && value.every(item => typeof item === 'string');
}

function completionSummary(value: unknown): value is CompletionSummary {
  return record(value) && typeof value.allowed === 'boolean' && stringList(value.codes) && stringList(value.messages);
}

function auditSummary(value: unknown): value is AuditSummary {
  return record(value) && typeof value.status === 'string' && typeof value.integrity === 'string' &&
    typeof value.verified === 'boolean' &&
    (value.findings === undefined || stringList(value.findings)) &&
    (value.unattributed === undefined || stringList(value.unattributed)) &&
    (value.changed_files === undefined || safeInteger(value.changed_files)) &&
    (value.coverage === undefined || typeof value.coverage === 'string') &&
    (value.contract_audit === undefined || typeof value.contract_audit === 'string');
}

function approvalRule(value: unknown): value is ApprovalRule {
  return record(value) && typeof value.source === 'string' && value.source.length <= 40 &&
    typeof value.tool === 'string' && value.tool.length <= 200 &&
    (value.decision === undefined || value.decision === null || typeof value.decision === 'string') &&
    (value.scopes === undefined || value.scopes === null || stringList(value.scopes)) &&
    (value.note === undefined || typeof value.note === 'string') &&
    (value.command_prefix === undefined || stringList(value.command_prefix));
}

function autonomyLevel(value: unknown): value is AutonomyLevel {
  return typeof value === 'string' && AUTONOMY_LEVELS.includes(value as AutonomyLevel);
}

function runResult(value: unknown): value is RunResult {
  return record(value) && typeof value.text === 'string' &&
    typeof value.steps === 'number' && Number.isInteger(value.steps) && value.steps >= 0 &&
    ['done', 'stopped', 'cancelled', 'error', 'truncated'].includes(String(value.status)) &&
    record(value.usage) && typeof value.cost_usd === 'number' && Number.isFinite(value.cost_usd) &&
    typeof value.run_id === 'string' && typeof value.stop_reason === 'string' &&
    (value.verified === undefined || value.verified === null || typeof value.verified === 'boolean') &&
    (value.completion === undefined || value.completion === null || completionSummary(value.completion)) &&
    (value.audit === undefined || value.audit === null || auditSummary(value.audit));
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

function attachTaskSnapshot(value: unknown): value is AttachTaskSnapshot {
  return record(value) &&
    typeof value.task_id === 'string' && validId(value.task_id) &&
    typeof value.kind === 'string' && typeof value.status === 'string' &&
    typeof value.segment === 'number' && Number.isSafeInteger(value.segment) && value.segment >= 0 &&
    (value.run_id === null || (typeof value.run_id === 'string' && validId(value.run_id))) &&
    typeof value.objective === 'string' &&
    (value.goal_id === null || typeof value.goal_id === 'string') &&
    (value.mode === undefined || typeof value.mode === 'string') &&
    (value.plan_id === undefined || value.plan_id === null || typeof value.plan_id === 'string') &&
    (value.plan_version === undefined || value.plan_version === null || typeof value.plan_version === 'number') &&
    (value.worktree === null || (record(value.worktree) &&
      typeof value.worktree.path === 'string' && typeof value.worktree.branch === 'string' &&
      typeof value.worktree.base_commit === 'string'));
}

function attachApprovals(value: unknown): value is AttachApprovalItem[] {
  return Array.isArray(value) && value.every(item => record(item) &&
    typeof item.approval_id === 'string' && validId(item.approval_id) &&
    typeof item.tool === 'string' &&
    (item.tier === null || typeof item.tier === 'string') &&
    typeof item.requester === 'string' &&
    (item.expires_at === null || typeof item.expires_at === 'string'));
}

function taskHint(value: unknown): value is TaskHint {
  return record(value) &&
    typeof value.task_id === 'string' && validId(value.task_id) &&
    typeof value.kind === 'string' && typeof value.status === 'string' &&
    (value.worker_status === null || typeof value.worker_status === 'string') &&
    typeof value.stale === 'boolean' &&
    (value.mode === undefined || typeof value.mode === 'string') &&
    (value.parent_id === undefined || value.parent_id === null || typeof value.parent_id === 'string') &&
    (value.plan_id === undefined || value.plan_id === null || typeof value.plan_id === 'string') &&
    (value.pending_approvals === undefined || (typeof value.pending_approvals === 'number' &&
      Number.isSafeInteger(value.pending_approvals) && value.pending_approvals >= 0)) &&
    (value.priority === undefined || (typeof value.priority === 'number' &&
      Number.isSafeInteger(value.priority) && value.priority >= 0 && value.priority <= 100));
}

function taskSnapshot(value: unknown): value is TaskSnapshot {
  return record(value) &&
    typeof value.task_id === 'string' && validId(value.task_id) &&
    typeof value.kind === 'string' &&
    typeof value.status === 'string' &&
    typeof value.segment === 'number' && Number.isSafeInteger(value.segment) && value.segment >= 0 &&
    typeof value.objective === 'string' &&
    (value.goal_id === null || typeof value.goal_id === 'string') &&
    (value.plan_id === undefined || value.plan_id === null || typeof value.plan_id === 'string') &&
    (value.plan_version === undefined || value.plan_version === null || nonnegativeInteger(value.plan_version) && value.plan_version > 0) &&
    typeof value.updated_at === 'string' && (value.mode === undefined || typeof value.mode === 'string');
}

function nonnegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

export function reviewRef(value: unknown): value is ReviewRef {
  if (!record(value) || Object.keys(value).length !== 5) return false;
  return ['session_id', 'plan_task_id', 'plan_id'].every(key =>
    typeof value[key] === 'string' && (value[key] as string).length > 0 &&
    (value[key] as string).length <= 128 && (value[key] as string).trim() === value[key] &&
    !/[\x00-\x1f]/.test(value[key] as string)) &&
    /^t-\d{8}-\d{6}-[0-9a-f]{6}$/.test(String(value.plan_task_id)) &&
    nonnegativeInteger(value.plan_version) && value.plan_version > 0 &&
    typeof value.plan_hash === 'string' && /^[0-9a-f]{64}$/.test(value.plan_hash);
}

function reviewSummary(value: unknown): value is ReviewSummary {
  return record(value) && Object.keys(value).length === 6 && typeof value.objective === 'string' &&
    ['draft', 'proposed', 'approved', 'executing', 'superseded', 'legacy'].includes(String(value.status)) &&
    typeof value.executable === 'boolean' &&
    ['step_count', 'criterion_count', 'question_count'].every(key => nonnegativeInteger(value[key]));
}

function planReviewResult(value: Record<string, unknown>): boolean {
  if (!validId(value.id) || !(value.target === null || reviewRef(value.target)) ||
      !(value.replacement_target === null || reviewRef(value.replacement_target)) ||
      !(value.code === null || (typeof value.code === 'string' && value.code.length > 0)) ||
      typeof value.message !== 'string' || !(value.summary === null || reviewSummary(value.summary)) ||
      typeof value.body_page !== 'string' || Array.from(value.body_page).length > 16000 ||
      !(value.next_cursor === null || nonnegativeInteger(value.next_cursor)) ||
      !Array.isArray(value.available_actions) || new Set(value.available_actions).size !== value.available_actions.length ||
      !value.available_actions.every(action => ['approve-execute', 'approve-only', 'dismiss'].includes(action))) return false;
  if (value.outcome === 'unavailable') {
    return typeof value.code === 'string' && value.launch === null && value.available_actions.length === 0;
  }
  if (!reviewRef(value.target)) return false;
  if (['ready', 'approved', 'dismissed'].includes(String(value.outcome))) {
    return value.launch === null && reviewSummary(value.summary);
  }
  const launch = value.launch;
  if (!record(launch) || Object.keys(launch).length !== 5 || typeof launch.task_id !== 'string' ||
      !/^t-\d{8}-\d{6}-[0-9a-f]{6}$/.test(launch.task_id) || typeof launch.run_started !== 'boolean' ||
      !['reserved', 'created', 'queued', 'running', 'paused', 'completed', 'failed', 'cancelled', 'interrupted'].includes(String(launch.status))) return false;
  if (value.outcome === 'approved_not_started') {
    return launch.run_id === null && launch.segment === 0 && launch.run_started === false;
  }
  return value.outcome === 'launched' && value.available_actions.length === 0 &&
    validId(launch.run_id) && nonnegativeInteger(launch.segment) && launch.segment > 0 &&
    launch.status !== 'reserved' && (!launch.run_started || launch.run_id === value.id);
}

function planRevisionResult(value: Record<string, unknown>): boolean {
  if (!validId(value.id) || !(value.target === null || reviewRef(value.target)) ||
      !(value.result_target === null || reviewRef(value.result_target)) ||
      !(value.code === null || (typeof value.code === 'string' && value.code.length > 0)) ||
      typeof value.message !== 'string' || typeof value.feedback !== 'string' ||
      Array.from(value.feedback).length > 4000) return false;
  if (value.outcome === 'unavailable') {
    return typeof value.code === 'string' && value.task === null && value.result_target === null && value.revision_id === null;
  }
  if (!reviewRef(value.target) || !value.feedback.trim() || typeof value.revision_id !== 'string' ||
      !/^[0-9a-f]{64}$/.test(value.revision_id)) return false;
  if (value.outcome === 'absent') return value.task === null && value.result_target === null && value.code === null;
  const task = value.task;
  if (task === null) return value.outcome === 'pending' && value.result_target === null;
  if (!record(task) || Object.keys(task).length !== 5 || typeof task.task_id !== 'string' ||
      !/^t-\d{8}-\d{6}-[0-9a-f]{6}$/.test(task.task_id) || !nonnegativeInteger(task.segment) ||
      typeof task.run_started !== 'boolean' ||
      !['created', 'queued', 'running', 'paused', 'completed', 'failed', 'cancelled', 'interrupted'].includes(String(task.status))) return false;
  if (task.segment === 0) return value.outcome === 'pending' && task.run_id === null && task.run_started === false && value.result_target === null;
  if (!validId(task.run_id) || (task.run_started && (task.run_id !== value.id || task.status !== 'running'))) return false;
  if (value.outcome === 'submitted') {
    const output = value.result_target;
    return reviewRef(output) && output.session_id === value.target.session_id && output.plan_task_id === value.target.plan_task_id &&
      output.plan_id === value.target.plan_id && output.plan_version === value.target.plan_version + 1;
  }
  if (value.result_target !== null) return false;
  if (value.outcome === 'running') return task.status === 'running';
  if (value.outcome === 'paused') return ['paused', 'interrupted'].includes(String(task.status)) && task.run_started === false;
  return value.outcome === 'incomplete' && value.code === 'revision_not_submitted' && task.status !== 'running' && task.run_started === false;
}

function planRevisionHistory(value: Record<string, unknown>): boolean {
  if (!validId(value.id) || !(value.target === null || reviewRef(value.target)) ||
      !(value.code === null || typeof value.code === 'string' && value.code.length > 0) || typeof value.message !== 'string' ||
      !Array.isArray(value.records) || value.records.length > 10 ||
      !(value.next_cursor === null || typeof value.next_cursor === 'string' && /^[0-9a-f]{64}$/.test(value.next_cursor))) return false;
  if (value.code !== null) return value.records.length === 0 && value.next_cursor === null;
  const target = value.target;
  if (!reviewRef(target) || value.next_cursor !== null && value.records.length !== 10) return false;
  const keys = new Set<string>();
  return value.records.every(item => {
    if (!record(item) || !planRevisionResult({...item, id: value.id}) || !reviewRef(item.target) ||
        !item.feedback || record(item.task) && item.task.run_started !== false ||
        item.target.session_id !== target.session_id || item.target.plan_task_id !== target.plan_task_id || item.target.plan_id !== target.plan_id) return false;
    const key = JSON.stringify([reviewKey(item.target), item.feedback]);
    if (keys.has(key)) return false;
    keys.add(key); return true;
  });
}

function planModeState(value: unknown): value is PlanModeState {
  return record(value) && Object.keys(value).length === 3 &&
    typeof value.active === 'boolean' && typeof value.pending === 'boolean' &&
    (value.wanted === null || typeof value.wanted === 'boolean');
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
  if ('file_source' in value && value.file_source !== null && !validFileSource(value.file_source)) throw new Error('文件来源身份无效');
  switch (value.type) {
    case 'ready':
      valid = value.id === null && typeof value.workspace === 'string' &&
        typeof value.model === 'string' && typeof value.session_id === 'string' &&
        Array.isArray(value.capabilities) && value.capabilities.every(item => typeof item === 'string') &&
        (value.provider === undefined || typeof value.provider === 'string') &&
        (value.session_path === undefined || typeof value.session_path === 'string') &&
        (value.session_purpose === undefined || value.session_purpose === 'user' || value.session_purpose === 'test') &&
        (value.tasks === undefined || (Array.isArray(value.tasks) && value.tasks.every(taskHint))) &&
        (value.resumed === undefined || typeof value.resumed === 'boolean') &&
        (value.skills === undefined || skillList(value.skills)) &&
        (value.autonomy === undefined || autonomyLevel(value.autonomy)) &&
        (value.autonomy_label === undefined || (typeof value.autonomy_label === 'string' && value.autonomy_label.length <= 80));
      break;
    case 'plan_mode': valid = value.id === null && planModeState(value.mode); break;
    case 'plan_review': valid = planReviewResult(value); break;
    case 'plan_revision': valid = planRevisionResult(value); break;
    case 'plan_revision_history': valid = planRevisionHistory(value); break;
    case 'task_catalog': valid = validId(value.id) && validTaskCatalogPage(value); break;
    case 'session_catalog': valid = validId(value.id) && validSessionCatalogPage(value); break;
    case 'permission_overview': valid = validId(value.id) && validPermissionOverview(value); break;
    case 'history_search': valid = validId(value.id) && validHistorySearchPage(value); break;
    case 'rounds_ledger': valid = validId(value.id) && validRoundsLedgerPage(value); break;
    case 'inspection_history':
      valid = validId(value.id) && typeof value.workspace === 'string' && validId(value.session_id) &&
        (value.task_id === undefined || validId(value.task_id)) &&
        (value.next_cursor === null || inspectionCursor(value.next_cursor)) && Number.isSafeInteger(value.skipped) && Number(value.skipped) >= 0 &&
        Array.isArray(value.records) && value.records.length <= 3 && value.records.every(item => validInspectionSnapshot(item) &&
          item.workspace === value.workspace && item.sessionId === value.session_id && (value.task_id === undefined || item.scope.taskId === value.task_id));
      break;
    case 'history_message': valid = value.id === null && message(value.message); break;
    case 'notice': valid = value.id === null && typeof value.message === 'string'; break;
    case 'command_result':
      valid = validId(value.id) && BACKEND_COMMANDS.includes(value.command as BackendCommand) && typeof value.text === 'string' &&
        (value.skills === undefined || skillList(value.skills)) &&
        (value.autonomy === undefined || autonomyLevel(value.autonomy));
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
    case 'tool_call': valid = validId(value.id) && toolCall(value.call) && (value.tier === undefined || validToolTier(value.tier)); break;
    case 'tool_result':
      valid = validId(value.id) && toolCall(value.call) && typeof value.ok === 'boolean' && typeof value.output === 'string' &&
        (value.execution === undefined || execution(value.execution));
      break;
    case 'approval_request':
      valid = validId(value.id) && validId(value.approval_id) && toolCall(value.call) && typeof value.preview === 'string' &&
        (value.decisions === undefined || (Array.isArray(value.decisions) && value.decisions.includes('deny') &&
          value.decisions.every(decision => approvalDecisions.includes(String(decision))) &&
          new Set(value.decisions).size === value.decisions.length)) &&
        (value.tier === undefined || typeof value.tier === 'string') &&
        (value.tier_label === undefined || (typeof value.tier_label === 'string' && value.tier_label.length <= 80)) &&
        (value.rule === undefined || approvalRule(value.rule));
      break;
    case 'approval_resolved':
      valid = validId(value.id) && validId(value.approval_id) && approvalDecisions.includes(String(value.decision));
      break;
    case 'done': valid = validId(value.id) && runResult(value.result); break;
    case 'task_state': valid = validId(value.id) && taskSnapshot(value.task); break;
    case 'task_snapshot': valid = value.id === null && attachTaskSnapshot(value.task) && attachApprovals(value.approvals); break;
    case 'approval_list': valid = (value.id === null || validId(value.id)) && attachApprovals(value.approvals); break;
    case 'subagent_event': valid = value.id === null && typeof value.task_id === 'string' && validId(value.task_id) && typeof value.status === 'string' && (value.summary === undefined || typeof value.summary === 'string'); break;
    case 'ack': valid = validId(value.id) && ['cancel', 'approval', 'steer'].includes(String(value.action)); break;
    case 'closed': valid = true; break;
    case 'error': valid = typeof value.code === 'string' && typeof value.message === 'string'; break;
  }
  if (!valid) throw new Error(`后端消息字段无效：${value.type}`);
  return value as ServerEvent;
}

const booleanArgs = new Set(['--fake', '--chat-only', '--continue', '--list-sessions', '--test-session', '--sandbox', '--sandbox-network']);
const valueArgs = new Set(['--workspace', '--provider', '--model', '--base-url', '--max-steps', '--session', '--session-identity', '--sandbox-network-allow', '--autonomy']);

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
    if (arg === '--autonomy' && !AUTONOMY_LEVELS.includes(value as AutonomyLevel)) {
      throw new Error('--autonomy 必须是 ask|edits|workspace|full');
    }
    checked.push(arg, value);
    index += 1;
  }
  return checked;
}
