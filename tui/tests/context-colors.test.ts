import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import test from 'node:test';
import {stripVTControlCharacters} from 'node:util';
import stringWidth from 'string-width';

function colorEscape(color: string): string {
  if (color.startsWith('#')) return `\x1b[38;2;${color.slice(1).match(/../g)!.map(channel => Number.parseInt(channel, 16)).join(';')}m`;
  if (color.startsWith('ansi256')) return `\x1b[38;5;${color.match(/\d+/g)!.at(-1)}m`;
  const code: Record<string, number> = {cyan: 36, magenta: 35, yellow: 33, green: 32,
    cyanBright: 96, magentaBright: 95, yellowBright: 93, greenBright: 92};
  return `\x1b[${code[color]}m`;
}

for (const theme of ['light', 'dark']) for (const level of [1, 2, 3]) {
  test(`${theme}/${level} context lines, sidebars, and tiny compact occupancy emit all four semantic colors`, () => {
    const env: NodeJS.ProcessEnv = {...process.env, FORCE_COLOR: String(level)};
    delete env.NO_COLOR;
    const output = execFileSync(process.execPath, ['--input-type=module', '-e', `
      import React from 'react';
      import {Box, renderToString} from 'ink';
      import {ReadingText} from './dist/src/markdown.js';
      import {contextLines} from './dist/src/context-view.js';
      import {StatusPanel} from './dist/src/agent-status.js';
      import {initialState} from './dist/src/view-state.js';
      import {contextSnapshot} from './dist/tests/context-fixture.js';
      import {getPalette} from './dist/src/theme.js';
      import {App} from './dist/src/app.js';
      const palette = getPalette('${theme}', ${level});
      const snapshot = {...contextSnapshot(), total_tokens: 1500, context_window: 1000000, input_capacity: 960000,
        sections: {system: 200, memory: 100, tools: 300, history: 900}};
      const state = {...initialState, phase: 'idle', contextSnapshots: [snapshot]};
      const side = renderToString(React.createElement(StatusPanel, {state, columns: 32, height: 32, palette}), {columns: 32});
      const compact = renderToString(React.createElement(StatusPanel, {state: {...state, contextSnapshots: [{...snapshot, phase: 'loaded'}]}, columns: 80, height: 3, palette}), {columns: 80});
      const details = renderToString(React.createElement(Box, {flexDirection: 'column'}, contextLines([snapshot], null, 75).map((line, key) => React.createElement(ReadingText, {line, key, palette}))), {columns: 75});
      const client = {subscribe: () => () => {}, start: () => {}, shutdown: async () => {}};
      const shortSide = renderToString(React.createElement(App, {client, initialTheme: '${theme}', windowSize: {columns: 120, rows: 24}, initialState: {...state,
        phase: 'running', metrics: {used_tokens: 8000, limit_tokens: 10000, estimated: false, context_tokens: 1500, context_window: 1000000, done: 0, planned: 0, cost_usd: 0},
        entries: [{id: 0, kind: 'task', text: '检查工作区'}, {id: 1, kind: 'tool', text: '读取文件', call: {id: 'c', name: 'read_file', args: {}}, ok: true}]}}), {columns: 120});
      process.stdout.write(JSON.stringify({colors: palette.contextColors, side, compact, details, shortSide}));
    `], {cwd: new URL('../../', import.meta.url), encoding: 'utf8', env});
    const result = JSON.parse(output) as {colors: Record<string, string>; side: string; compact: string; details: string; shortSide: string};
    for (const surface of ['side', 'compact', 'details', 'shortSide'] as const) {
      for (const section of ['system', 'memory', 'tools', 'history']) assert.ok(result[surface].includes(colorEscape(result.colors[section]!)), `${surface}: ${section}`);
      const text = stripVTControlCharacters(result[surface]);
      assert.match(text, /已用构成/);
      assert.match(text, /━/);
      assert.ok(text.split('\n').every(line => stringWidth(line) <= (surface === 'side' ? 32 : surface === 'compact' ? 80 : surface === 'shortSide' ? 120 : 75)));
      assert.doesNotMatch(text, /SS|MM|TT|HH|␛\[/);
    }
    const compact = stripVTControlCharacters(result.compact);
    assert.equal(compact.split('\n').length, 3);
    assert.match(compact, /S200 M100 T300 H900/);
    assert.match(compact, /输入0.2%/);
    assert.match(compact, /尚未请求模型/);
  });
}
