import assert from 'node:assert/strict';
import {test} from 'node:test';
import {transcriptLines} from '../src/workbench.js';
import {errorItems, firstLineByEntry, roundItems, searchTranscript} from '../src/history-locate.js';
import {initialState, reduceView, type Entry, type ViewState} from '../src/view-state.js';
import type {ReadingLine} from '../src/markdown.js';
import type {ServerEvent} from '../src/protocol.js';

const line = (text: string, entryId?: number): ReadingLine => ({spans: [{text}], ...(entryId === undefined ? {} : {entryId})});
const tool = (id: number, name: string, output: string, extra: Partial<Entry> = {}): Entry =>
  ({id, kind: 'tool', text: `工具 › ${name}`, call: {id: `c-${id}`, name, args: {}}, output, ok: true, ...extra});

test('tool_call carries the approval tier and done marks the originating round status', () => {
  let state = reduceView(initialState, {type: 'begin', id: 'run-1', text: '第一轮任务'});
  assert.equal(state.entries.at(-1)?.runId, 'run-1');
  state = reduceView(state, {type: 'event', event: {v: 1, type: 'tool_call', id: 'run-1', call: {id: 'c1', name: 'read_file', args: {path: 'a.txt'}}, tier: 'read'}});
  assert.equal(state.entries.at(-1)?.tier, 'read');
  state = reduceView(state, {type: 'event', event: {v: 1, type: 'tool_call', id: 'run-1', call: {id: 'c2', name: 'bash', args: {command: 'ls'}}}});
  assert.equal(state.entries.at(-1)?.tier, undefined);
  state = reduceView(state, {type: 'event', event: {v: 1, type: 'done', id: 'run-1', result: {text: '', steps: 1, status: 'done', usage: {}, cost_usd: 0, run_id: 'r-1', stop_reason: ''}} as ServerEvent});
  assert.equal(state.entries.find(entry => entry.kind === 'task')?.runStatus, 'done');
});

test('searchTranscript finds transcript lines case-insensitively with stable line numbers', () => {
  const lines = [line('第一行 准备'), line('定位 BUG 的位置'), line('无关内容'), line('bug 已修复', 3)];
  const items = searchTranscript(lines, 'bug');
  assert.deepEqual(items.map(item => item.line), [1, 3]);
  assert.match(items[0]!.label, /BUG/);
  assert.equal(searchTranscript(lines, '  ').length, 0);
  assert.equal(searchTranscript(lines, '不存在的词').length, 0);
});

test('round and error locators map entries back to their first transcript line', () => {
  const entries: Entry[] = [
    {id: 0, kind: 'task', text: '第一轮：修 bug', runId: 'run-1', runStatus: 'done'},
    tool(1, 'bash', 'ok'),
    tool(2, 'bash', 'permission denied', {ok: false}),
    {id: 3, kind: 'task', text: '第二轮：跑测试', runId: 'run-2', runStatus: 'error'},
  ];
  const state: ViewState = {...initialState, phase: 'idle', workspace: '/workspace', sessionId: 'session', entries};
  const lines = transcriptLines(state, 80, null);
  const first = firstLineByEntry(lines);
  assert.equal(first.get(0), 0);
  const rounds = roundItems(entries, lines);
  assert.equal(rounds.length, 2);
  assert.match(rounds[0]!.label, /第 1 轮 · done · 第一轮/);
  assert.match(rounds[1]!.label, /第 2 轮 · error/);
  const errors = errorItems(entries, lines);
  assert.equal(errors.length, 2);
  assert.match(errors[0]!.label, /工具错误 · bash · permission denied/);
  assert.match(errors[1]!.label, /运行 error · 第二轮/);
  assert.ok(errors[0]!.line < errors[1]!.line);
});

test('consecutive successful read-only tools collapse while raw calls stay selectable', () => {
  const entries: Entry[] = [
    tool(0, 'read_file', '文件 A 内容', {tier: 'read'}),
    tool(1, 'read_file', '文件 B 内容', {tier: 'read'}),
    tool(2, 'grep', '命中 3 处', {tier: 'read'}),
    tool(3, 'write_file', '已写入', {tier: 'write'}),
    tool(4, 'bash', 'exit 1', {tier: 'exec', ok: false}),
  ];
  const state: ViewState = {...initialState, phase: 'idle', workspace: '/workspace', sessionId: 'session', entries};
  const collapsed = transcriptLines(state, 80, null).map(item => item.spans.map(span => span.text).join('')).join('\n');
  assert.match(collapsed, /连续 3 个只读调用/);
  assert.match(collapsed, /read_file×2/);
  assert.doesNotMatch(collapsed, /文件 A 内容/);
  assert.match(collapsed, /write_file/);
  const expanded = transcriptLines(state, 80, 1).map(item => item.spans.map(span => span.text).join('')).join('\n');
  assert.match(expanded, /文件 B 内容/);
  const oldBackend: ViewState = {...state, entries: entries.map(entry => ({...entry, tier: undefined}))};
  const plain = transcriptLines(oldBackend, 80, null).map(item => item.spans.map(span => span.text).join('')).join('\n');
  assert.doesNotMatch(plain, /只读调用/);
  assert.match(plain, /文件 A 内容/);
});

test('searching a long transcript stays fast enough for interactive locating', () => {
  const entries: Entry[] = [];
  for (let index = 0; index < 3000; index += 1) {
    entries.push({id: index, kind: index % 5 === 0 ? 'task' : 'assistant', text: `第 ${index} 条记录内容 marker-${index % 97} 说明`});
  }
  const state: ViewState = {...initialState, phase: 'idle', workspace: '/workspace', sessionId: 'session', entries};
  const lines = transcriptLines(state, 80, null);
  const samples: number[] = [];
  let hits = 0;
  for (let run = 0; run < 20; run += 1) {
    const started = performance.now();
    hits = searchTranscript(lines, 'marker-42').length;
    samples.push(performance.now() - started);
  }
  samples.sort((a, b) => a - b);
  const p95 = samples[Math.floor(samples.length * 0.95)]!;
  console.log(`history-locate perf: ${lines.length} lines, ${hits} hits, p95=${p95.toFixed(2)}ms`);
  assert.ok(hits > 0);
  assert.ok(p95 < 100, `p95 ${p95}ms exceeds 100ms`);
});
