import assert from 'node:assert/strict';
import {test} from 'node:test';
import stringWidth from 'string-width';
import {fitText, markdownLines, visibleText} from '../src/markdown.js';

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
