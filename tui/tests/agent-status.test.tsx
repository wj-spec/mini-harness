import assert from 'node:assert/strict';
import {test} from 'node:test';
import React from 'react';
import {renderToString} from 'ink';
import stringWidth from 'string-width';
import {AgentHeader, StatusPanel, agentStatus, budgetBar} from '../src/agent-status.js';
import {initialState, type ViewState} from '../src/view-state.js';
import {themes} from '../src/theme.js';

const ready: ViewState = {...initialState, phase: 'idle', workspace: '/项目/mini-harness', model: 'deepseek-flash', sessionId: 'session-123', provider: 'openai'};
const running = {...ready, phase: 'running' as const, activeId: 'run-1'};

test('agent status reflects observed lifecycle instead of inventing task progress', () => {
  assert.match(agentStatus(initialState).label, /启动/);
  assert.equal(agentStatus({...initialState, activeId: 'startup', activity: 'initializing'}).label, '初始化服务');
  assert.match(agentStatus(ready).label, /就绪/);
  assert.match(agentStatus({...running, activity: 'waiting_model'}).label, /等待模型/);
  assert.match(agentStatus({...running, activity: 'responding'}).label, /正在回复/);
  assert.match(agentStatus({...running, activity: 'using_tool'}).label, /工具/);
  assert.match(agentStatus({...running, phase: 'waiting_approval'}).label, /等待审批/);
  assert.match(agentStatus({...running, phase: 'stopping'}).label, /正在停止/);
  assert.match(agentStatus({...ready, lastStatus: 'stopped', lastResult: '已停止 · token_budget'}).label, /暂停/);
  assert.match(agentStatus({...ready, lastStatus: 'cancelled'}).label, /已取消/);
  assert.match(agentStatus({...ready, lastStatus: 'error'}).label, /运行失败/);
  assert.equal(agentStatus({...ready, lastStatus: 'done'}).label, '本轮结束');
  assert.doesNotMatch(agentStatus({...ready, lastStatus: 'done'}).detail, /任务完成|测试通过|100%/);
  assert.match(agentStatus({...running, phase: 'closed'}).label, /后端已断开/);
});

test('budget visualization uses only measured budget ratios and clamps overrun', () => {
  assert.equal(budgetBar(200, 1000, 10), '[██░░░░░░░░] 20%');
  assert.equal(budgetBar(1100, 1000, 5), '[█████] 110%');
  assert.equal(budgetBar(2, 0, 10), '未设总上限');
});

test('panels retain terminal-cell bounds and meaningful labels at compact and wide sizes', () => {
  const state: ViewState = {...running, activity: 'using_tool', entries: [
    {id: 0, kind: 'task', text: '修复中文输入与滚动🙂，保留公开接口。'},
    {id: 1, kind: 'tool', text: 'bash', call: {id: 'call-1', name: 'bash', args: {}}, pending: true},
  ], metrics: {used_tokens: 123_456, limit_tokens: 1_000_000, estimated: true,
    context_tokens: 45_678, context_window: 1_000_000, done: 2, planned: 4, cost_usd: 0.04}};
  for (const [columns, height] of [[32, 22], [80, 3], [24, 12]]) {
    const output = renderToString(<StatusPanel state={state} columns={columns!} height={height!} palette={themes.system}/>, {columns});
    const lines = output.split('\n');
    assert.equal(lines.length, height);
    assert.ok(lines.every(line => stringWidth(line) <= columns!));
    assert.match(output, /工具/);
    assert.doesNotMatch(output, /任务完成|测试通过|思考中/);
    if (height! > 18) {
      assert.match(output, /任务 Tokens/);
      assert.match(output, /12%/);
      assert.match(output, /2\/4/);
    }
  }
  for (const columns of [32, 80, 120]) {
    const output = renderToString(<AgentHeader state={state} columns={columns} height={3} palette={themes.system}/>, {columns});
    assert.equal(output.split('\n').length, 3);
    assert.ok(output.split('\n').every(line => stringWidth(line) <= columns));
    assert.match(output, /mini-harness/);
  }
});

test('budget and plan remain unknown when the backend has not supplied metrics', () => {
  const output = renderToString(<StatusPanel state={running} columns={32} height={20} palette={themes.system}/>, {columns: 32});
  assert.match(output, /尚无用量记录/);
  assert.doesNotMatch(output, /0%|0\/0|任务完成|测试通过/);
});
