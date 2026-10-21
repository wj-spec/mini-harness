import React from 'react';
import {render as renderInk, renderToString} from 'ink';
import {pathToFileURL} from 'node:url';
import {App} from '../src/app.js';
import type {BackendClient} from '../src/backend.js';
import {initialState, type ViewState} from '../src/view-state.js';
import {contextSnapshot} from './context-fixture.js';
import type {ThemeName} from '../src/theme.js';

export function fixtureState(scenario: string): ViewState {
  const state: ViewState = {...initialState, phase: 'idle', activity: null, workspace: '/workspace/mini-harness', model: '视觉测试 · 不调用模型', sessionId: 'visual-fixture', provider: 'fixture'};
  if (scenario === 'welcome') return state;
  state.entries = [
    {id: 0, kind: 'task', text: '解释项目入口，并给出一个小范围改进建议。'},
    {id: 1, kind: 'assistant', text: '我会先读取入口文件，确认实际调用关系。'},
    {id: 2, kind: 'tool', text: 'read_file · 调用已返回', call: {id: 't1', name: 'read_file', args: {path: 'app/__main__.py'}}, ok: true, output: 'from app.agent import Agent\n\n' + Array.from({length: 60}, (_, i) => `# source line ${i + 1}`).join('\n')},
    {id: 3, kind: 'assistant', text: '## 入口与职责\n\n- **Python 后端**负责 Agent loop、工具、审批和会话。\n- **Ink 界面**负责输入、流式反馈与阅读。\n\n```python\nasync def run_task(text):\n    return await agent.run(text)\n```\n\n建议先为取消流程补一条可复现的回归，再验证实际执行结果。'},
  ];
  state.nextEntry = 4;
  state.lastResult = '本轮结束 · 视觉测试';
  state.lastStatus = 'done';
  state.metrics = {used_tokens: 163400, limit_tokens: 1000000, estimated: true, context_tokens: 24600, context_window: 1000000, done: 2, planned: 4, cost_usd: 0.012};
  if (scenario === 'context') {
    const sample = contextSnapshot();
    const before = {...sample, phase: 'before_compaction' as const, context_window: 1_000_000, input_capacity: 960_000,
      total_tokens: 198_000, sections: {system: 6000, memory: 0, tools: 12000, history: 180000},
      caps: {system: 8000, memory: 64000, tools: 16000, history: 942000, history_nominal: 872000},
      reserves: {output: 32000, scratch: 8000}, remaining_tokens: 762000,
      blocks: sample.blocks.map(block => ({...block, tokens: block.section === 'system' ? 6000 : block.section === 'tools' ? 12000 : 180000})),
      delta_tokens: 198000, section_deltas: {system: 6000, memory: 0, tools: 12000, history: 180000}};
    const after = {...before, snapshot_id: 'snapshot-2', phase: 'after_compaction' as const, total_tokens: 98000,
      sections: {...before.sections, history: 80000}, remaining_tokens: 862000,
      blocks: before.blocks.map(block => block.section === 'history' ? {...block, tokens: 80000} : block),
      delta_tokens: -100000, section_deltas: {system: 0, memory: 0, tools: 0, history: -100000}};
    state.contextSnapshots = [before, after];
    state.metrics.context_tokens = after.total_tokens;
  }
  if (scenario === 'running') {
    state.phase = 'running'; state.activeId = 'fixture-run'; state.activity = 'using_tool'; state.lastStatus = null; state.lastResult = '';
    state.entries = [
      {id: 0, kind: 'task', text: '修复计算器的加法错误，保留原测试，并重新验证。'},
      {id: 1, kind: 'assistant', text: '已读取入口和现有测试。先复现失败，再核对修改结果。'},
      {id: 2, kind: 'tool', text: '测试失败', call: {id: 'check-1', name: 'bash', args: {command: 'python -m pytest -q', verification: true}}, ok: false, execution: {status: 'failed', exit_code: 1}, output: '1 failed · test_add: expected 5, got -1'},
      {id: 3, kind: 'tool', text: '修改已写入', call: {id: 'edit-1', name: 'edit_file', args: {path: 'calculator.py'}}, ok: true, output: 'edited calculator.py (1 replacement)'},
      {id: 4, kind: 'assistant', text: '已修正运算符，正在运行原测试。执行结果返回后再确认。'},
      {id: 5, kind: 'tool', text: '等待测试结果', call: {id: 'check-2', name: 'bash', args: {command: 'python -m pytest -q', verification: true}}, pending: true, runId: 'fixture-run'},
    ];
    state.nextEntry = 6;
  }
  if (scenario === 'history') {
    state.entries = [{id: 0, kind: 'task', text: '检查历史输出与滚动。'}, {id: 1, kind: 'assistant', text: Array.from({length: 140}, (_, index) => `history-line-${String(index).padStart(3, '0')} · 已保存的历史输出`).join('\n')}];
    state.nextEntry = 2;
  }
  if (scenario === 'approval') {
    state.phase = 'waiting_approval'; state.activeId = 'fixture-run'; state.activity = 'using_tool'; state.lastStatus = null; state.lastResult = '';
    state.approval = {id: 'fixture-approval', tool: 'write_file', decisions: ['once', 'session', 'workspace', 'deny'], call: {id: 't2', name: 'write_file', args: {path: 'tests/test_cancel.py', content: '# visual fixture only'}}, preview: '写入 tests/test_cancel.py\n新增一条取消流程回归测试\n\n' + Array.from({length: 35}, (_, i) => `${String(i + 1).padStart(2)}  测试预览，第 ${i + 1} 行内容。`).join('\n')};
  }
  if (scenario === 'error') {state.phase = 'closed'; state.lastStatus = 'error'; state.error = 'provider_error: 连接已断开；当前任务尚未完成，请核对已有文件变化。'; state.lastResult = '运行失败';}
  if (scenario === 'stopped') {state.lastResult = '已取消'; state.lastStatus = 'cancelled'; state.entries.push({id: 4, kind: 'tool', text: 'bash · 结果未知', call: {id: 't3', name: 'bash', args: {command: 'python -m pytest -q'}}, uncertain: true}); state.nextEntry = 5;}
  if (scenario === 'paused') {state.lastStatus = 'stopped'; state.lastStopReason = 'token_budget'; state.lastResult = '已停止 · token_budget';}
  return state;
}

const client: BackendClient = {
  subscribe: () => () => {}, start: () => {}, run: () => 'fixture-run', command: () => 'fixture-command', cancel: () => {}, approve: () => {}, shutdown: async () => {},
};

export function VisualFixture({scenario = 'welcome', columns = 80, rows = 24, theme = 'system'}: {scenario?: string; columns?: number; rows?: number; theme?: ThemeName}) {
  return <App client={client} initialState={fixtureState(scenario)} windowSize={{columns, rows}} initialTheme={theme}/>;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [scenario = 'welcome', columns = '80', rows = '24'] = process.argv.slice(2);
  const themeOption = process.argv.find(value => value.startsWith('--theme='))?.slice('--theme='.length);
  const theme = themeOption === 'light' || themeOption === 'dark' ? themeOption : 'system';
  const tree = <VisualFixture scenario={scenario} columns={Number(columns)} rows={Number(rows)} theme={theme}/>;
  if (process.argv.includes('--snapshot')) {
    process.stdout.write(`${renderToString(tree, {columns: Number(columns)})}\n`);
  } else {
    const instance = renderInk(tree, {alternateScreen: true, exitOnCtrlC: false});
    await instance.waitUntilExit();
  }
}
