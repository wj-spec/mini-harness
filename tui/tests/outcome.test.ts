import assert from 'node:assert/strict';
import test from 'node:test';
import {outcomePresentation} from '../src/outcome.js';
import {initialState, reduceView} from '../src/view-state.js';
import {transcriptLines} from '../src/workbench.js';
import {agentStatus} from '../src/agent-status.js';
import {verificationInspection} from '../src/inspection.js';
import {verificationContent} from '../src/sidebar/verification-content.js';
import type {RunOutcome, RunResult} from '../src/protocol.js';

export const facts = (patch: Partial<RunOutcome> = {}): RunOutcome => ({
  schema_version: 1, files: [], omitted_files: 0, checks: [], mutation_seq: 0,
  verification: 'not_run', operations: 0, tool_errors: 0, denied: false,
  audit_warning: false, prior_unknown: false, ...patch,
});
const result = (patch: Partial<RunResult> = {}): RunResult => ({
  text: '回答正文', status: 'done', steps: 1, usage: {}, cost_usd: 0,
  run_id: 'trace-1', stop_reason: '', outcome: facts(), receipt: '[运行结束] 原始证据', ...patch,
});
const text = (state: typeof initialState) => transcriptLines(state, 80, null).map(l => l.spans.map(s => s.text).join('')).join('\n');
const finish = (r: RunResult) => {
  const begun = reduceView({...initialState, sessionId: 's'}, {type: 'begin', id: 'r', text: '解释代码'});
  return reduceView(begun, {type: 'event', event: {v: 1, type: 'done', id: 'r', result: r}});
};

test('QA keeps the answer and evidence access without a receipt card', () => {
  const state = finish(result());
  assert.match(text(state), /回答正文/);
  assert.doesNotMatch(text(state), /本轮回执|原始证据|验收通过|运行结束/);
  assert.ok(state.entries.some(e => e.inspection));
  assert.equal(agentStatus(state).label, '就绪');
  assert.equal(state.inspection?.result?.receipt, '[运行结束] 原始证据');
});

test('changed files use a compact result and all consumers agree on stale validation', () => {
  const r = result({outcome: facts({files: ['a'], mutation_seq: 2, verification: 'stale'})});
  const state = finish(r);
  assert.match(text(state), /修改 1 个文件.*需重新验证/);
  assert.doesNotMatch(text(state), /原始证据|验收通过/);
  assert.match(agentStatus(state).label, /需重新验证/);
  const v = verificationInspection(state.inspection!, state.entries);
  assert.match(verificationContent({run: state.inspection!.scope, verification: v, sessionId: 's'}), /需重新验证/);
});

test('no-diff effects, denial, audit anomaly, and failures remain visible', () => {
  for (const patch of [{operations: 1}, {denied: true}, {tool_errors: 1}, {audit_warning: true}, {prior_unknown: true}]) {
    assert.equal(outcomePresentation(result({outcome: facts(patch)})).visible, true);
  }
  for (const status of ['error', 'cancelled', 'stopped'] as const) {
    const p = outcomePresentation(result({status, stop_reason: 'max_steps'}));
    assert.equal(p.visible, true);
    assert.notEqual(p.tone, 'success');
  }
});

test('legacy success text never becomes a success verdict', () => {
  const r = result({outcome: undefined, receipt: '[任务验收] 已验证 integrity=clean'});
  const p = outcomePresentation(r);
  assert.match(p.label, /历史运行记录/);
  assert.notEqual(p.tone, 'success');
  assert.doesNotMatch(text(finish(r)), /已验证 integrity/);
});

test('plan acceptance failure wins over a passed agent check', () => {
  const p = outcomePresentation(result({verified: true, outcome: facts({files: ['a'], verification: 'passed', plan_verification: 'failed'})}));
  assert.equal(p.tone, 'warning');
  assert.match(p.label, /计划检查未通过/);
});

test('replayed done and foreign requests do not duplicate result summaries', () => {
  let state = finish(result({outcome: facts({files: ['a']})}));
  const before = state.entries;
  state = reduceView(state, {type: 'event', event: {v: 1, type: 'done', id: 'r', result: result()}});
  assert.deepEqual(state.entries, before);
});

test('automatic continuation removes its terminal summary but keeps evidence reachable', () => {
  let state = finish(result({status: 'stopped', stop_reason: 'max_steps', outcome: facts({files: ['a']})}));
  state = reduceView(state, {type: 'continue_run', id: 'r'});
  assert.doesNotMatch(text(state), /任务已暂停|修改 1 个文件/);
  assert.ok(state.entries.some(e => !e.continuing && e.inspection));
});
test('worker continuation result does not create a terminal card', () => {
  const state = finish(result({status: 'stopped', stop_reason: 'max_steps', continuing: true, outcome: facts({files: ['a']})}));
  assert.doesNotMatch(text(state), /任务已暂停|修改 1 个文件/);
  assert.ok(state.entries.some(e => e.inspection));
});

test('malformed optional facts cannot manufacture a successful result', () => {
  for (const outcome of [{schema_version: 99}, facts({operations: -1}), {...facts(), denied: 'false'},
    {...facts(), verification: ['passed']}, {...facts(), plan_verification: ['passed']}]) {
    const p = outcomePresentation(JSON.parse(JSON.stringify({...result(), outcome, verified: true})));
    assert.equal(p.legacy, true);
    assert.notEqual(p.tone, 'success');
  }
});
