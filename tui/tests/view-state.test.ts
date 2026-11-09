import assert from 'node:assert/strict';
import test from 'node:test';
import {initialState, reduceView} from '../src/view-state.js';
import type {PlanReviewResult, PlanRevisionResult, ServerEvent} from '../src/protocol.js';
import {contextSnapshot, loadedContextSnapshot} from './context-fixture.js';

const call = {id: 'read-1', name: 'read_file', args: {path: 'hello.py'}};


test('revision results cannot change the bound source, task, segment or confirmed artifact', () => {
  const source = {session_id: 's', plan_task_id: 'plan-task', plan_id: 'plan', plan_version: 1, plan_hash: 'a'.repeat(64)};
  const receipt: PlanRevisionResult = {target: source, feedback: 'update', revision_id: 'b'.repeat(64), result_target: null,
    outcome: 'running', code: null, message: 'running', task: {task_id: 'revision-task', status: 'running', segment: 2, run_id: 'r2', run_started: true}};
  let state = reduceView({...initialState, sessionId: 's'}, {type: 'begin', id: 'r2', text: 'revise', revision: receipt});
  const original = state.inspection;
  for (const wrong of [
    {...receipt, target: {...source, session_id: 'other'}}, {...receipt, target: {...source, plan_hash: 'c'.repeat(64)}},
    {...receipt, revision_id: 'c'.repeat(64)}, {...receipt, task: {...receipt.task!, task_id: 'other'}},
    {...receipt, task: {...receipt.task!, segment: 1}}, {...receipt, task: {...receipt.task!, run_id: 'r1'}},
    {...receipt, result_target: {...source, plan_version: 3}},
  ]) {
    state = reduceView(state, {type: 'revision_receipt', result: wrong}); assert.deepEqual(state.inspection, original);
  }
  const produced = {...source, plan_version: 2, plan_hash: 'd'.repeat(64)};
  const submitted = {...receipt, outcome: 'submitted' as const, result_target: produced, task: {...receipt.task!, run_started: false, status: 'completed'}};
  state = reduceView(state, {type: 'revision_receipt', result: submitted});
  const confirmed = state.inspection;
  state = reduceView(state, {type: 'revision_receipt', result: receipt}); assert.deepEqual(state.inspection, confirmed);
  state = reduceView(state, {type: 'revision_receipt', result: {...submitted, result_target: {...produced, plan_hash: 'e'.repeat(64)}}});
  assert.deepEqual(state.inspection, confirmed); assert.equal(state.inspectionPlan?.relation, 'revision-result');
  assert.deepEqual(state.inspectionPlan?.target, produced);
  state = reduceView(state, {type: 'event', event: {v: 1, type: 'done', id: 'r2', result}});
  const snapshot = state.entries.find(entry => entry.inspection)?.inspection;
  assert.deepEqual(snapshot?.revision, confirmed?.revision);
  state = reduceView(state, {type: 'begin', id: 'next', text: 'next'});
  state = reduceView(state, {type: 'revision_receipt', result: submitted});
  assert.equal(state.inspection?.scope.requestId, 'next'); assert.equal(state.inspectionPlan, null);
  assert.deepEqual(state.entries.find(entry => entry.inspection)?.inspection?.plan?.target, produced);
});

test('completed result retains its own inspection facts after the next run starts', () => {
  let state = reduceView({...initialState, workspace: '/repo', sessionId: 's'}, {type: 'begin', id: 'old', text: '原任务'});
  const send = (value: Record<string, unknown>) => {state = reduceView(state, {type: 'event', event: {v: 1, id: 'old', ...value} as ServerEvent});};
  send({type: 'tool_result', call: {id: 'write', name: 'write_file', args: {path: 'old.ts'}}, ok: true, output: 'written'});
  send({type: 'tool_result', call: {id: 'verify', name: 'bash', args: {command: 'old-test', verification: true}}, ok: true,
    output: 'failed old check', execution: {status: 'failed', exit_code: 2}});
  send({type: 'done', result: {text: '未完成', status: 'stopped', stop_reason: 'verification_failed', run_id: 'trace-old',
    steps: 2, usage: {}, cost_usd: 0, verified: false}});
  const snapshot = state.entries.find(entry => entry.inspection)?.inspection;
  assert.ok(snapshot);
  assert.equal(snapshot.scope.requestId, 'old'); assert.equal(snapshot.scope.traceRunId, 'trace-old');
  assert.equal(snapshot.scope.objective, '原任务'); assert.deepEqual(snapshot.files, ['old.ts']);
  assert.equal(snapshot.verification.verified, false); assert.equal(snapshot.verification.commands[0]?.execution?.exit_code, 2);
  assert.equal(snapshot.plan, null);
  state = reduceView(state, {type: 'begin', id: 'new', text: '新任务'});
  assert.equal(state.inspection?.scope.requestId, 'new');
  assert.deepEqual(state.entries.find(entry => entry.inspection)?.inspection, snapshot);
  state = reduceView(state, {type: 'event', event: {v: 1, type: 'closed', id: null}});
  const disconnected = state.entries.find(entry => entry.inspection?.scope.requestId === 'new')?.inspection;
  assert.equal(disconnected?.scope.status, 'unknown'); assert.equal(disconnected?.verification.verified, null);
});

test('a late plan receipt enriches only its original result and a later query or disconnect cannot erase it', () => {
  let state = reduceView({...initialState, workspace: '/repo', sessionId: 's'}, {type: 'begin', id: 'plan-run', text: '生成计划'});
  state = reduceView(state, {type: 'event', event: {v: 1, type: 'task_state', id: 'plan-run', task: {
    task_id: 't-plan', kind: 'run', status: 'completed', segment: 1, objective: '生成计划', goal_id: null, updated_at: ''}}});
  state = reduceView(state, {type: 'event', event: {v: 1, type: 'done', id: 'plan-run', result: {text: '', status: 'stopped',
    stop_reason: 'plan_submitted', steps: 1, usage: {}, cost_usd: 0, run_id: 'trace-plan'}}});
  const review: PlanReviewResult = {outcome: 'ready', target: {session_id: 's', plan_task_id: 't-plan', plan_id: 'p1', plan_version: 1, plan_hash: 'a'.repeat(64)},
    replacement_target: null, code: null, message: '计划已生成', launch: null, body_page: '', next_cursor: null, available_actions: ['approve-execute'],
    summary: {objective: '原计划', status: 'proposed', executable: true, step_count: 1, criterion_count: 0, question_count: 0}};
  state = reduceView(state, {type: 'review_card', result: review});
  const snapshot = state.entries.find(entry => entry.inspection)?.inspection;
  assert.deepEqual(snapshot?.plan?.target, review.target);
  assert.equal(snapshot?.plan?.relation, 'planning');
  state = reduceView(state, {type: 'review_card', result: {...review, target: {...review.target!, plan_task_id: 'other', plan_id: 'p-other'}}});
  state = reduceView(state, {type: 'event', event: {v: 1, type: 'closed', id: null}});
  assert.deepEqual(state.entries.find(entry => entry.inspection)?.inspection, snapshot);
});
const result = {text: '# 结果\n已读取。', status: 'done' as const, steps: 2, usage: {}, cost_usd: 0, run_id: 'internal', stop_reason: ''};
const event = (type: string, fields: Record<string, unknown> = {}) => ({v: 1, type, id: 'run-1', ...fields}) as ServerEvent;

test('restored history and task hints do not invent an inspected run', () => {
  let state = reduceView(initialState, {type: 'event', event: event('ready', {id: null, workspace: '/repo', model: 'fake', session_id: 'restored',
    resumed: true, capabilities: [], tasks: [{task_id: 't-old', kind: 'run', status: 'completed', worker_status: null, stale: false}]})});
  state = reduceView(state, {type: 'event', event: event('history_message', {id: null, message: {role: 'user', content: '历史任务'}})});
  assert.equal(state.inspection, null);
  state = reduceView(state, {type: 'event', event: event('task_snapshot', {id: null, task: {task_id: 't-old', kind: 'run', status: 'paused', segment: 3,
    run_id: null, objective: '明确 attach 的任务', goal_id: null, worktree: null}, approvals: []})});
  assert.equal(state.inspection?.scope.status, 'restored');
  assert.equal(state.inspection?.scope.taskId, 't-old');
  assert.equal(state.inspection?.scope.traceRunId, null);
  assert.equal(state.inspection?.result, null);
});

test('inspection retains exact completed evidence through queries and marks disconnection as unknown', () => {
  let state = reduceView(initialState, {type: 'begin', id: 'run-1', text: '工作'});
  const task = {task_id: 't-1', kind: 'run', status: 'running', segment: 2, objective: '工作', goal_id: null, updated_at: 'now'};
  state = reduceView(state, {type: 'event', event: event('task_state', {task})});
  state = reduceView(state, {type: 'event', event: event('task_state', {task: {...task, task_id: 't-wrong'}})});
  assert.equal(state.inspection?.scope.taskId, 't-1');
  state = reduceView(state, {type: 'event', event: event('task_state', {task: {...task, segment: 1}})});
  assert.equal(state.inspection?.scope.segment, 2);
  state = reduceView(state, {type: 'event', event: event('done', {result: {...result, verified: false}})});
  const inspection = state.inspection;
  state = reduceView(state, {type: 'begin_command', id: 'query', command: 'cost'});
  state = reduceView(state, {type: 'event', event: event('command_result', {id: 'query', command: 'cost', text: '费用'})});
  assert.equal(state.inspection, inspection);
  assert.equal(state.inspection?.result?.verified, false);
  state = reduceView(state, {type: 'begin', id: 'run-2', text: '新任务'});
  state = reduceView(state, {type: 'event', event: event('closed', {id: null})});
  assert.equal(state.inspection?.scope.status, 'unknown');
  assert.equal(state.inspection?.result, null);
  assert.equal(state.inspection?.scope.taskId, null);
});

test('done surfaces the harness delivery receipt as a separate notice', () => {
  let state = reduceView(initialState, {type: 'begin', id: 'run-1', text: 'work'});
  const receipt = '[运行结束] status=done steps=1 tokens=10 cost=$0.0000\n[任务验收] 未确认，请核对以下证据';
  state = reduceView(state, {type: 'event', event: event('done', {result: {...result, receipt}})});
  const notices = state.entries.filter(entry => entry.kind === 'notice');
  assert.ok(notices.some(entry => entry.text.includes('[运行结束]') && entry.text.includes('[任务验收]')));
  let plain = reduceView(initialState, {type: 'begin', id: 'run-1', text: 'work'});
  plain = reduceView(plain, {type: 'event', event: event('done', {result})});
  assert.equal(plain.entries.filter(entry => entry.kind === 'notice').length, 0);
});

test('session purpose follows ready metadata and defaults older backends to ordinary use', () => {
  const fields = {id: null, workspace: '/repo', model: 'm', session_id: 's', capabilities: []};
  const testSession = reduceView(initialState, {type: 'event', event: event('ready', {...fields, session_purpose: 'test'})});
  assert.equal(Reflect.get(testSession, 'sessionPurpose'), 'test');
  const userSession = reduceView(testSession, {type: 'event', event: event('ready', {...fields, session_purpose: 'user'})});
  assert.equal(Reflect.get(userSession, 'sessionPurpose'), 'user');
  const legacy = reduceView(testSession, {type: 'event', event: event('ready', fields)});
  assert.equal(Reflect.get(legacy, 'sessionPurpose'), 'user');
});

test('loaded context refreshes startup without starting a task or appending run replay', () => {
  let state = reduceView(initialState, {type: 'event', event: event('ready', {id: null, workspace: '/repo', model: 'fake', session_id: 's', capabilities: []})});
  const first = loadedContextSnapshot();
  const metrics = {id: null, used_tokens: 0, limit_tokens: 1000, estimated: true, context_tokens: first.total_tokens,
    context_window: first.context_window, done: 0, planned: 0, cost_usd: 0};
  state = reduceView(state, {type: 'event', event: event('context_snapshot', {id: null, snapshot: first})});
  state = reduceView(state, {type: 'event', event: event('metrics', metrics)});
  assert.equal(state.phase, 'starting');
  assert.equal(state.activeId, null);
  assert.deepEqual(state.entries, []);
  assert.equal(state.metrics?.used_tokens, 0);
  state = reduceView(state, {type: 'event', event: event('status', {id: 'startup', phase: 'starting'})});
  const refreshed = {...first, snapshot_id: 'loaded-mcp', sections: {...first.sections, tools: 40}, total_tokens: 60,
    remaining_tokens: 840, blocks: first.blocks.map(block => block.section === 'tools' ? {...block, tokens: 40} : block)};
  state = reduceView(state, {type: 'event', event: event('context_snapshot', {id: null, snapshot: refreshed})});
  assert.deepEqual(state.contextSnapshots, [refreshed]);
  assert.equal(state.metrics?.context_tokens, 60);
  state = reduceView(state, {type: 'event', event: event('status', {id: null, phase: 'idle'})});
  assert.equal(state.phase, 'idle');
  assert.equal(state.activeId, null);
  for (const start of [{type: 'begin', id: 'run-1', text: 'work'} as const,
    {type: 'begin_command', id: 'run-1', command: 'resume'} as const]) {
    let running = reduceView(state, start);
    assert.deepEqual(running.contextSnapshots, []);
    assert.equal(running.metrics, null);
    running = reduceView(running, {type: 'event', event: event('context_snapshot', {id: null, snapshot: first})});
    running = reduceView(running, {type: 'event', event: event('metrics', metrics)});
    assert.deepEqual(running.contextSnapshots, []);
    assert.equal(running.metrics, null);
    running = reduceView(running, {type: 'event', event: event('context_snapshot', {snapshot: contextSnapshot()})});
    assert.equal(running.contextSnapshots[0]?.phase, 'request');
    running = reduceView(running, {type: 'event', event: event('done', {result})});
    const completed = running;
    running = reduceView(running, {type: 'event', event: event('context_snapshot', {id: null, snapshot: first})});
    running = reduceView(running, {type: 'event', event: event('metrics', metrics)});
    assert.equal(running, completed);
  }
});

test('context snapshots follow one run without changing cumulative token accounting', () => {
  let state = reduceView(initialState, {type: 'begin', id: 'run-1', text: 'work'});
  const metrics = {used_tokens: 1000, limit_tokens: 10000, estimated: false, context_tokens: 500, context_window: 1000, done: 0, planned: 0, cost_usd: 0};
  state = reduceView(state, {type: 'event', event: event('metrics', metrics)});
  const snapshot = contextSnapshot();
  state = reduceView(state, {type: 'event', event: event('context_snapshot', {snapshot})});
  assert.deepEqual(state.contextSnapshots, [snapshot]);
  assert.equal(state.metrics?.used_tokens, 1000);
  assert.equal(state.metrics?.estimated, false);
  assert.equal(state.metrics?.context_tokens, 100);
  state = reduceView(state, {type: 'event', event: event('metrics', {...metrics, used_tokens: 1200, context_tokens: null})});
  assert.equal(state.metrics?.context_tokens, 100);
  state = reduceView(state, {type: 'event', event: event('context_snapshot', {id: 'old-run', snapshot: {...snapshot, snapshot_id: 'old'}})});
  assert.deepEqual(state.contextSnapshots, [snapshot]);
  state = reduceView(state, {type: 'event', event: event('done', {result})});
  state = reduceView(state, {type: 'event', event: event('context_snapshot', {snapshot: {...snapshot, snapshot_id: 'late'}})});
  assert.deepEqual(state.contextSnapshots, [snapshot]);
  state = reduceView(state, {type: 'begin', id: 'run-2', text: 'next'});
  assert.deepEqual(state.contextSnapshots, []);
});

test('context replay is bounded, deduplicated, retained for local commands, and reset for resume', () => {
  let state = reduceView(initialState, {type: 'begin', id: 'run-1', text: 'work'});
  for (let index = 0; index < 105; index += 1) {
    state = reduceView(state, {type: 'event', event: event('context_snapshot', {snapshot: {...contextSnapshot(), snapshot_id: `s-${index}`}})});
  }
  assert.equal(state.contextSnapshots.length, 100);
  assert.equal(state.contextSnapshots[0].snapshot_id, 's-5');
  state = reduceView(state, {type: 'event', event: event('context_snapshot', {snapshot: {...contextSnapshot(), snapshot_id: 's-104'}})});
  assert.equal(state.contextSnapshots.length, 100);
  state = reduceView(state, {type: 'event', event: event('done', {result})});
  state = reduceView(state, {type: 'begin_command', id: 'command-1', command: 'cost'});
  assert.equal(state.contextSnapshots.length, 100);
  state = reduceView(state, {type: 'begin_command', id: 'resume-1', command: 'resume'});
  assert.deepEqual(state.contextSnapshots, []);
});

test('retains complete Markdown messages and pairs tool results with their call', () => {
  let state = reduceView(initialState, {type: 'begin', id: 'run-1', text: '读取代码'});
  state = reduceView(state, {type: 'event', event: event('text_delta', {delta: '先读取\n再分析。'})});
  state = reduceView(state, {type: 'event', event: event('tool_call', {call})});
  const full = '第一行\n' + '完整结果'.repeat(1000) + '\n最后一行';
  state = reduceView(state, {type: 'event', event: event('tool_result', {call, ok: true, output: full})});
  state = reduceView(state, {type: 'event', event: event('text_delta', {delta: result.text})});
  state = reduceView(state, {type: 'event', event: event('done', {result})});
  state = reduceView(state, {type: 'event', event: event('done', {result})});
  const tools = state.entries.filter(entry => entry.kind === 'tool');
  assert.equal(tools.length, 1);
  assert.equal(tools[0].output, full);
  assert.equal(tools[0].pending, false);
  assert.deepEqual(state.entries.filter(entry => entry.kind === 'assistant').map(entry => entry.text), ['先读取\n再分析。', result.text]);
});

test('does not overwrite an earlier run when providers reuse a tool call id', () => {
  let state = reduceView(initialState, {type: 'begin', id: 'run-1', text: 'first'});
  for (const value of [event('tool_call', {call}), event('tool_result', {call, ok: true, output: 'first'}), event('done', {result: {...result, text: ''}})]) {
    state = reduceView(state, {type: 'event', event: value});
  }
  state = reduceView(state, {type: 'begin', id: 'run-2', text: 'second'});
  state = reduceView(state, {type: 'event', event: {...event('tool_call', {call}), id: 'run-2'}});
  state = reduceView(state, {type: 'event', event: {...event('tool_result', {call, ok: false, output: 'second'}), id: 'run-2'}});
  assert.deepEqual(state.entries.filter(entry => entry.kind === 'tool').map(entry => entry.output), ['first', 'second']);
});

test('preserves complete approval preview for a separate review panel and clears it on cancel', () => {
  let state = reduceView(initialState, {type: 'begin', id: 'run-1', text: 'edit'});
  const preview = 'preview\n'.repeat(200);
  state = reduceView(state, {type: 'event', event: event('approval_request', {approval_id: 'approval-1', call, preview})});
  assert.equal(state.approval?.preview, preview);
  assert.deepEqual(state.approval?.call, call);
  assert.equal(state.entries.some(entry => entry.text.includes(preview)), false);
  state = reduceView(state, {type: 'cancel'});
  assert.equal(state.approval, null);
});

test('shows only backend supplied telemetry and ignores stale metrics', () => {
  let state = reduceView(initialState, {type: 'event', event: event('ready', {id: null, workspace: '/repo', model: 'test', provider: 'fake', session_id: 'session', session_path: '/repo/.vibe/sessions/session', capabilities: []})});
  assert.equal(state.provider, 'fake');
  assert.equal(state.sessionPath, '/repo/.vibe/sessions/session');
  state = reduceView(state, {type: 'begin', id: 'run-1', text: 'work'});
  assert.equal(state.metrics, null);
  const metrics = {used_tokens: 1024, limit_tokens: 1_000_000, estimated: true, context_tokens: 800, context_window: 1_000_000, done: 1, planned: 3, cost_usd: 0.01};
  state = reduceView(state, {type: 'event', event: event('metrics', metrics)});
  assert.deepEqual(state.metrics, metrics);
  state = reduceView(state, {type: 'event', event: {...event('metrics', {...metrics, used_tokens: 999}), id: 'old-run'}});
  assert.equal(state.metrics?.used_tokens, 1024);
});

test('a disconnected tool is marked unknown and keeps diagnostics visible', () => {
  let state = reduceView(initialState, {type: 'begin', id: 'run-1', text: 'work'});
  state = reduceView(state, {type: 'event', event: event('tool_call', {call})});
  state = reduceView(state, {type: 'event', event: event('diagnostic', {id: null, message: '需要配置 API key'})});
  state = reduceView(state, {type: 'event', event: event('closed', {id: null})});
  assert.equal(state.entries.find(entry => entry.kind === 'tool')?.uncertain, true);
  assert.equal(state.entries.find(entry => entry.kind === 'tool')?.pending, false);
  assert.ok(state.entries.some(entry => entry.text.includes('需要配置 API key')));
});

test('restores persisted messages and pairs results without replaying a run', () => {
  let state = reduceView(initialState, {type: 'event', event: event('ready', {id: null, workspace: '/repo', model: 'fake', session_id: 'saved', capabilities: [], resumed: true})});
  for (const message of [
    {role: 'user', content: '修复已保存任务', tool_calls: []},
    {role: 'assistant', content: '先读入口', tool_calls: [call]},
    {role: 'tool', content: '原始完整内容', name: call.name, tool_call_id: call.id, tool_calls: []},
    {role: 'assistant', content: '', tool_calls: [{id: 'unknown', name: 'bash', args: {command: 'make build'}}]},
  ]) state = reduceView(state, {type: 'event', event: event('history_message', {id: null, message})});
  state = reduceView(state, {type: 'event', event: event('status', {id: null, phase: 'idle'})});
  assert.equal(state.activeId, null);
  assert.equal(state.phase, 'idle');
  assert.deepEqual(state.entries.filter(entry => entry.kind === 'task').map(entry => entry.text), ['修复已保存任务']);
  assert.equal(state.entries.filter(entry => entry.kind === 'tool').length, 2);
  assert.equal(state.entries.find(entry => entry.call?.id === call.id)?.output, '原始完整内容');
  assert.equal(state.entries.find(entry => entry.call?.id === 'unknown')?.uncertain, true);
  assert.ok(state.entries.every(entry => !entry.pending));
});

test('startup approval can be cancelled without confusing its id with a model run', () => {
  let state = reduceView(initialState, {type: 'event', event: event('ready', {id: null, workspace: '/repo', model: 'fake', session_id: 'saved', capabilities: []})});
  state = reduceView(state, {type: 'event', event: event('status', {id: 'startup', phase: 'starting'})});
  state = reduceView(state, {type: 'event', event: event('approval_request', {id: 'startup', approval_id: 'mcp-start', call: {id: 'mcp-call', name: 'mcp_start', args: {}}, preview: '启动本地服务', decisions: ['once', 'session', 'deny']})});
  assert.equal(state.activeId, 'startup');
  assert.equal(state.approval?.id, 'mcp-start');
  state = reduceView(state, {type: 'cancel'});
  state = reduceView(state, {type: 'event', event: event('approval_resolved', {id: 'startup', approval_id: 'mcp-start', decision: 'deny'})});
  assert.equal(state.phase, 'stopping');
  state = reduceView(state, {type: 'event', event: event('status', {id: null, phase: 'idle'})});
  assert.equal(state.phase, 'idle');
  assert.equal(state.activeId, null);
});

test('ready does not permit new runs before restoration and startup status finish', () => {
  let state = reduceView(initialState, {type: 'event', event: event('ready', {id: null, workspace: '/repo', model: 'fake', session_id: 'saved', resumed: true, capabilities: []})});
  assert.equal(state.phase, 'starting');
  state = reduceView(state, {type: 'event', event: event('history_message', {id: null, message: {role: 'user', content: '恢复期间尚不能提交', tool_calls: []}})});
  state = reduceView(state, {type: 'event', event: event('status', {id: 'startup', phase: 'starting'})});
  state = reduceView(state, {type: 'event', event: event('approval_request', {id: 'startup', approval_id: 'mcp-ready-race', call, preview: '初始化审批', decisions: ['once', 'deny']})});
  assert.equal(state.approval?.id, 'mcp-ready-race');
  assert.equal(state.activeId, 'startup');
});

test('preserves structured command outcomes without interpreting tool output', () => {
  let state = reduceView(initialState, {type: 'begin', id: 'run-1', text: 'run tests'});
  const command = {id: 'cmd-1', name: 'bash', args: {command: 'exit 7'}};
  state = reduceView(state, {type: 'event', event: event('tool_call', {call: command})});
  state = reduceView(state, {type: 'event', event: event('tool_result', {call: command, ok: false, output: 'test text says success', execution: {status: 'failed', exit_code: 7}})});
  assert.deepEqual(state.entries.find(entry => entry.call?.id === 'cmd-1')?.execution, {status: 'failed', exit_code: 7});
});

test('restored command cards retain persisted execution metadata', () => {
  const call = {id: 'saved-cmd', name: 'bash', args: {command: 'exit 7'}};
  let state = reduceView(initialState, {type: 'event', event: event('history_message', {id: null, message: {role: 'assistant', content: '', tool_calls: [call]}})});
  state = reduceView(state, {type: 'event', event: event('history_message', {id: null, message: {role: 'tool', content: '真实失败输出', name: 'bash', tool_call_id: call.id, tool_calls: [], execution: {status: 'failed', exit_code: 7}}})});
  assert.deepEqual(state.entries[0].execution, {status: 'failed', exit_code: 7});
  assert.equal(state.entries[0].ok, false);
});

test('tracks model response and pending tools without equating returned tools with a finished task', () => {
  let state = reduceView(initialState, {type: 'begin', id: 'run-1', text: 'work'});
  assert.equal(state.activity, 'waiting_model');
  assert.equal(state.activeCommand, null);
  assert.equal(state.lastStatus, null);
  state = reduceView(state, {type: 'event', event: event('text_delta', {delta: '读取入口'})});
  assert.equal(state.activity, 'responding');
  state = reduceView(state, {type: 'event', event: event('tool_call', {call})});
  const second = {...call, id: 'read-2'};
  state = reduceView(state, {type: 'event', event: event('tool_call', {call: second})});
  assert.equal(state.activity, 'using_tool');
  state = reduceView(state, {type: 'event', event: event('tool_result', {call, ok: true, output: 'read'})});
  assert.equal(state.activity, 'using_tool');
  state = reduceView(state, {type: 'event', event: event('tool_result', {call: second, ok: false, output: 'failed'})});
  assert.equal(state.activity, 'waiting_model');
  assert.equal(state.phase, 'running');
  state = reduceView(state, {type: 'event', event: event('done', {result: {...result, status: 'stopped', stop_reason: 'token_budget'}})});
  assert.equal(state.activity, null);
  assert.equal(state.lastStatus, 'stopped');
  assert.equal(state.lastStopReason, 'token_budget');
  state = reduceView(state, {type: 'event', event: event('status', {id: null, phase: 'idle'})});
  assert.equal(state.lastStatus, 'stopped');
  state = reduceView(state, {type: 'begin', id: 'run-2', text: 'next'});
  assert.equal(state.lastStatus, null);
  assert.equal(state.lastStopReason, null);
});

test('cancelled and stopped runs keep partial output but do not print machine status as an answer', () => {
  let cancelled = reduceView(initialState, {type: 'begin', id: 'run-1', text: 'work'});
  cancelled = reduceView(cancelled, {type: 'event', event: event('text_delta', {delta: '部分输出'})});
  cancelled = reduceView(cancelled, {type: 'event', event: event('done', {result: {...result, text: 'cancelled', status: 'cancelled'}})});
  assert.deepEqual(cancelled.entries.map(entry => entry.text), ['work', '部分输出']);
  assert.equal(cancelled.lastStatus, 'cancelled');

  let stopped = reduceView(initialState, {type: 'begin', id: 'run-1', text: 'work'});
  stopped = reduceView(stopped, {type: 'event', event: event('done', {result: {...result, text: 'stopped: max_steps', status: 'stopped'}})});
  assert.deepEqual(stopped.entries.map(entry => entry.text), ['work']);

  let finished = reduceView(initialState, {type: 'begin', id: 'run-1', text: 'work'});
  finished = reduceView(finished, {type: 'event', event: event('done', {result})});
  assert.deepEqual(finished.entries.map(entry => entry.text), ['work', result.text]);
});

test('information commands do not pretend to wait for a model and keep the last task outcome', () => {
  let state = reduceView(initialState, {type: 'begin', id: 'run-1', text: 'work'});
  state = reduceView(state, {type: 'event', event: event('done', {result: {...result, status: 'error', stop_reason: 'provider_error'}})});
  state = reduceView(state, {type: 'begin_command', id: 'cost-1', command: 'cost'});
  assert.equal(state.activeCommand, 'cost');
  assert.equal(state.activity, null);
  state = reduceView(state, {type: 'event', event: event('status', {id: 'cost-1', phase: 'running'})});
  assert.equal(state.activity, null);
  assert.equal(state.lastStatus, 'error');
  state = reduceView(state, {type: 'event', event: event('command_result', {id: 'cost-1', command: 'cost', text: 'usage'})});
  assert.equal(state.activeCommand, null);
  assert.equal(state.activity, null);
  assert.equal(state.lastStatus, 'error');
  state = reduceView(state, {type: 'begin_command', id: 'resume-1', command: 'resume'});
  assert.equal(state.activeCommand, 'resume');
  assert.equal(state.activity, 'waiting_model');
  assert.equal(state.lastStatus, null);
});

test('approval restores actual activity and initialization stays separate from a running model', () => {
  let state = reduceView(initialState, {type: 'event', event: event('status', {id: 'startup', phase: 'starting'})});
  assert.equal(state.activity, 'initializing');
  state = reduceView(state, {type: 'event', event: event('approval_request', {id: 'startup', approval_id: 'startup-approval', call, preview: 'start'})});
  assert.equal(state.phase, 'waiting_approval');
  state = reduceView(state, {type: 'approval_sent'});
  assert.equal(state.phase, 'starting');
  assert.equal(state.activity, 'initializing');
  state = reduceView(state, {type: 'event', event: event('status', {id: null, phase: 'idle'})});
  assert.equal(state.activity, null);
  state = reduceView(state, {type: 'begin', id: 'run-1', text: 'work'});
  state = reduceView(state, {type: 'event', event: event('tool_call', {call})});
  state = reduceView(state, {type: 'event', event: event('approval_request', {approval_id: 'approval-1', call, preview: 'read'})});
  state = reduceView(state, {type: 'approval_sent'});
  assert.equal(state.activity, 'using_tool');
  state = reduceView(state, {type: 'event', event: event('approval_resolved', {approval_id: 'approval-1', decision: 'once'})});
  assert.equal(state.activity, 'using_tool');
});

test('restored history and stale events do not set an active model state; disconnect keeps its error', () => {
  let state = reduceView(initialState, {type: 'event', event: event('ready', {id: null, workspace: '/repo', model: 'fake', session_id: 'saved', resumed: true, capabilities: []})});
  state = reduceView(state, {type: 'event', event: event('history_message', {id: null, message: {role: 'assistant', content: 'saved output', tool_calls: []}})});
  state = reduceView(state, {type: 'event', event: event('status', {id: null, phase: 'idle'})});
  assert.equal(state.activity, null);
  assert.equal(state.activeId, null);
  const idle = state;
  for (const value of [event('text_delta', {delta: 'late'}), event('tool_call', {call}), event('command_result', {command: 'cost', text: 'late'})]) {
    state = reduceView(state, {type: 'event', event: value});
    assert.deepEqual(state, idle);
  }
  state = reduceView(state, {type: 'event', event: event('error', {id: null, code: 'backend_exit', message: 'backend gone'})});
  state = reduceView(state, {type: 'event', event: event('closed', {id: null})});
  assert.equal(state.activity, null);
  assert.equal(state.error, 'backend_exit: backend gone');
});

test('session plan mode follows plan_mode frames and defaults to inactive', () => {
  assert.deepEqual(initialState.planMode, {active: false, pending: false, wanted: null});
  let state = reduceView(initialState, {type: 'event', event: event('ready', {id: null, workspace: '/repo', model: 'm', session_id: 's', capabilities: ['tasks']})});
  assert.deepEqual(state.planMode, {active: false, pending: false, wanted: null});
  state = reduceView(state, {type: 'event', event: event('plan_mode', {id: null, mode: {active: false, pending: true, wanted: true}})});
  assert.deepEqual(state.planMode, {active: false, pending: true, wanted: true});
  state = reduceView(state, {type: 'event', event: event('plan_mode', {id: null, mode: {active: true, pending: false, wanted: true}})});
  assert.deepEqual(state.planMode, {active: true, pending: false, wanted: true});
  const running = reduceView(state, {type: 'begin', id: 'run-1', text: '规划'});
  assert.deepEqual(running.planMode, {active: true, pending: false, wanted: true});
  const completed = reduceView(running, {type: 'event', event: event('done', {result})});
  assert.deepEqual(completed.planMode, {active: true, pending: false, wanted: true});
  const off = reduceView(completed, {type: 'event', event: event('plan_mode', {id: null, mode: {active: false, pending: false, wanted: false}})});
  assert.deepEqual(off.planMode, {active: false, pending: false, wanted: false});
});
