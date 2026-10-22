import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {test} from 'node:test';
import {stripVTControlCharacters} from 'node:util';
import stringWidth from 'string-width';
import {fitText, markdownLines, visibleText, wrapSpans} from '../src/markdown.js';

test('wrapping preserves context color boundaries across equal text formatting', () => {
  const lines = wrapSpans([{text: '━━', contextSection: 'system'}, {text: '━━━', contextSection: 'tools'}], 3);
  assert.deepEqual(lines, [[{text: '━━', contextSection: 'system'}, {text: '━', contextSection: 'tools'}], [{text: '━━', contextSection: 'tools'}]]);
});

test('renders headings, lists, emphasis and fenced source as readable terminal lines', () => {
  const lines = markdownLines('# Result\n- **fixed** `state`\n```ts\nconst answer = 42;\n```', 50);
  assert.equal(lines[0]?.kind, 'heading');
  assert.equal(lines[0]?.spans.map(span => span.text).join(''), 'Result');
  assert.ok(lines[1]?.spans.some(span => span.bold && span.text === 'fixed'));
  assert.ok(lines[1]?.spans.some(span => span.code && span.text === 'state'));
  const output = lines.map(line => line.spans.map(span => span.text).join('')).join('\n');
  assert.match(output, /• fixed state/);
  assert.match(output, /const answer = 42/);
  assert.doesNotMatch(output, /```|\*\*/);
  assert.doesNotMatch(output, /[┌└│]/);
  assert.ok(lines.filter(line => line.kind === 'code').every(line => line.surface === 'code'));
});

test('shaded reading lines fill their terminal width without losing Chinese or emoji', () => {
  const environment: NodeJS.ProcessEnv = {...process.env, FORCE_COLOR: '3'};
  delete environment.NO_COLOR;
  const output = execFileSync(process.execPath, ['--input-type=module', '-e', `
    import React from 'react';
    import {renderToString} from 'ink';
    import {ReadingText} from './dist/src/markdown.js';
    import {themes} from './dist/src/theme.js';
    process.stdout.write(renderToString(React.createElement(ReadingText, {
      line: {spans: [{text: '中文🙂'}], surface: 'task'}, palette: themes.light, columns: 18,
    }), {columns: 18}));
  `], {encoding: 'utf8', cwd: new URL('../../', import.meta.url), env: environment});
  assert.equal(stringWidth(stripVTControlCharacters(output)), 18);
  assert.match(output, /\u001b\[48;2;231;237;244m中文🙂 {12}/);
});

test('wraps CJK and emoji by terminal columns without losing content', () => {
  const source = '中文测试🙂'.repeat(20);
  const lines = markdownLines(source, 19).map(line => line.spans.map(span => span.text).join(''));
  assert.equal(lines.join(''), source);
  assert.ok(lines.every(line => stringWidth(line) <= 19));
  assert.ok(stringWidth(fitText('/一个很长的项目路径/mini-harness', 20, true)) <= 20);
});

test('untrusted terminal escapes remain visible rather than clearing the screen', () => {
  assert.equal(visibleText('before\u001b[2Jafter'), 'before␛[2Jafter');
});
