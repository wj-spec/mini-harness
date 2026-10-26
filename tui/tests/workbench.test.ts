import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {renderToString} from 'ink';
import stringWidth from 'string-width';
import {detailLines, metricsText, ReadingPane, toolState, toolTone, transcriptLines, welcomeLines} from '../src/workbench.js';
import {initialState, type Entry} from '../src/view-state.js';
import {themes} from '../src/theme.js';
import {contextSnapshot} from './context-fixture.js';

test('context snapshot totals remain visible independently from task cumulative usage', () => {
  const state = {...initialState, contextSnapshots: [contextSnapshot()]};
  assert.match(metricsText(state), /上下文 ≈100\/1k/);
  assert.doesNotMatch(metricsText(state), /任务累计/);
  const text = metricsText({...state, metrics: {used_tokens: 8000, limit_tokens: 10000, estimated: false, context_tokens: null, context_window: 1000, done: 0, planned: 0, cost_usd: 0}});
  assert.match(text, /任务累计 8k\/10k/);
  assert.match(text, /上下文 ≈100\/1k/);
});

const command: Entry = {id: 1, kind: 'tool', text: 'bash', call: {id: 'cmd-1', name: 'bash', args: {command: 'python -m pytest -q'}}, ok: true, output: 'exit=0\nno tests ran'};

test('command cards use execution evidence and do not infer success from output text', () => {
  assert.match(toolState(command), /未记录|未确认/);
  assert.match(toolState({...command, execution: {status: 'succeeded', exit_code: 0}}), /零退出.*0/);
  assert.match(toolState({...command, ok: false, execution: {status: 'failed', exit_code: 7}}), /非零退出.*7/);
  assert.match(toolState({...command, ok: false, execution: {status: 'timed_out', exit_code: -9}}), /超时/);
  assert.match(toolState({...command, ok: false, execution: {status: 'cancelled', exit_code: -9}}), /取消/);
  assert.match(toolState({...command, execution: {status: 'unknown', exit_code: null}}), /未知/);
  const lines = detailLines({...command, ok: false, execution: {status: 'failed', exit_code: 7}}, 80).map(line => line.spans.map(span => span.text).join('')).join('\n');
  assert.match(lines, /exit=7/);
  assert.match(lines, /no tests ran/);
  assert.doesNotMatch(lines, /测试通过/);
});

test('a long remote tool name cannot hide the recorded failure status', () => {
  const entry: Entry = {...command, call: {...command.call!, name: 'mcp_external_' + 'very_long_tool_name_'.repeat(5)}, ok: false, execution: {status: 'failed', exit_code: 7}};
  const state = {...initialState, entries: [entry], nextEntry: 2};
  const heading = transcriptLines(state, 43, null)[0].spans.map(span => span.text).join('');
  assert.match(heading, /非零退出/);
  assert.match(heading, /exit=7/);
});

test('tool result semantics remain readable without color and have matching semantic tones', () => {
  for (const [execution, expected, tone] of [
    [{status: 'succeeded', exit_code: 0}, /零退出/, 'success'],
    [{status: 'failed', exit_code: 7}, /非零退出/, 'error'],
    [{status: 'timed_out', exit_code: null}, /超时/, 'warning'],
    [{status: 'cancelled', exit_code: null}, /取消/, 'warning'],
    [{status: 'unknown', exit_code: null}, /未知/, 'warning'],
  ] as const) {
    const entry: Entry = {...command, execution, ok: execution.status === 'succeeded'};
    assert.match(toolState(entry), expected);
    assert.equal(toolTone(entry), tone);
  }
  assert.equal(toolTone({...command, pending: true}), 'accent');
  assert.equal(toolTone({...command, uncertain: true}), 'warning');
  assert.equal(toolTone({...command, call: {...command.call!, name: 'read_file'}}), undefined);
});

test('scrollbar shows current position and the real Ink reading pane stays within terminal cells', () => {
  for (const columns of [32, 80, 120]) {
    const lines = Array.from({length: 40}, (_, index) => ({spans: [{text: `第 ${index + 1} 行 中文🙂`}]}));
    const frames: string[] = [];
    for (const top of [0, 16, 33]) {
      const output = renderToString(React.createElement(ReadingPane, {title: '对话 · 阅读', lines, top, height: 10, columns, palette: themes.system}), {columns});
      assert.equal(output.split('\n').length, 10);
      assert.ok(output.split('\n').every(line => stringWidth(line) <= columns));
      assert.match(output, new RegExp(`第 ${top + 1} 行`));
      frames.push(output);
    }
    const thumbRows = frames.map(frame => frame.split('\n').findIndex(line => line.includes('┃')));
    assert.ok(thumbRows[0]! < thumbRows[1]! && thumbRows[1]! < thumbRows[2]!);
  }
});

test('transcript cards separate task, model, tool and diagnostics without losing source text or entry anchors', () => {
  const lines = transcriptLines({...initialState, phase: 'idle', entries: [
    {id: 10, kind: 'task', text: '修复输入'},
    {id: 11, kind: 'assistant', text: '# 发现\n完整说明\n```ts\nconst marker = 42;\n```'},
    {...command, id: 12, execution: {status: 'failed', exit_code: 7}},
    {id: 13, kind: 'notice', text: '后端提示：服务断开'},
  ]}, 46, 12);
  const output = lines.map(line => line.spans.map(span => span.text).join('')).join('\n');
  assert.match(output, /当前任务/);
  assert.match(output, /Agent/);
  assert.match(output, /后端诊断/);
  assert.match(output, /非零退出.*7/);
  assert.match(output, /const marker = 42;/);
  assert.doesNotMatch(output, /\n提示\n/);
  for (const id of [10, 11, 12, 13]) assert.ok(lines.some(line => line.entryId === id));
  assert.ok(lines.find(line => line.entryId === 12 && line.kind === 'error'));
});

test('transcript surfaces replace enclosing frames while preserving focus and failure evidence', () => {
  const lines = transcriptLines({...initialState, entries: [
    {id: 10, kind: 'task', text: '修复输入🙂'},
    {id: 11, kind: 'assistant', text: '先复现，再修改。'},
    {...command, id: 12, execution: {status: 'failed', exit_code: 7}},
    {...command, id: 13, pending: true},
  ]}, 42, 13);
  const output = lines.map(line => line.spans.map(span => span.text).join('')).join('\n');
  assert.doesNotMatch(output, /[┌┐└┘│─╭╮╰╯]/u);
  assert.ok(lines.some(line => line.entryId === 10 && line.surface === 'task'));
  assert.ok(lines.some(line => line.entryId === 11 && line.surface === 'agent'));
  assert.ok(lines.some(line => line.entryId === 12 && line.surface === 'error'));
  assert.ok(lines.some(line => line.entryId === 13 && line.surface === 'selected' && line.selected));
  assert.match(output, /非零退出.*exit=7/);
  assert.match(output, /› 工具.*等待返回/);
  assert.equal((output.match(/Ctrl\+O/g) ?? []).length, 1);
  assert.ok(lines.every(line => stringWidth(line.spans.map(span => span.text).join('')) <= 42));
});


test('home screen leads with project start prompts and keeps context discoverable', () => {
  const state = {...initialState, phase: 'idle' as const, workspace: '/repo/mini-harness', model: 'deepseek-chat', provider: 'openai', sessionId: 's-1'};
  const wide = welcomeLines(state, 80).map(line => line.spans.map(span => span.text).join('')).join('\n');
  assert.match(wide, /开始一次编码任务/);
  assert.match(wide, /描述任务/);
  assert.match(wide, /从这些开始/);
  assert.match(wide, /读 README/);
  assert.match(wide, /\/repo\/mini-harness/);
  assert.match(wide, /deepseek-chat/);
  for (const columns of [24, 32, 64, 80, 120]) {
    const lines = welcomeLines(state, columns);
    assert.ok(lines.every(line => stringWidth(line.spans.map(span => span.text).join('')) <= columns), `overflow at ${columns}`);
  }
});
