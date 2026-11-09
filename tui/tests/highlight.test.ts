import assert from 'node:assert/strict';
import {test} from 'node:test';
import {highlightSpans} from '../src/markdown.js';

test('highlightSpans marks case-insensitive matches and preserves the original text', () => {
  const text = '定位 BUG 的位置，bug 已修复';
  const spans = highlightSpans(text, 'bug');
  assert.equal(spans.map(span => span.text).join(''), text);
  assert.deepEqual(spans.filter(span => span.highlight).map(span => span.text), ['BUG', 'bug']);
  assert.equal(highlightSpans('没有命中的正文', 'missing').filter(span => span.highlight).length, 0);
  assert.deepEqual(highlightSpans('正文', '   ').map(span => span.text), ['正文']);
  const chinese = highlightSpans('旧结论：关键结论在这里', '关键结论');
  assert.deepEqual(chinese.filter(span => span.highlight).map(span => span.text), ['关键结论']);
  assert.equal(highlightSpans('aaaa', 'aa').filter(span => span.highlight).length, 2);
  const base = highlightSpans('加粗命中', '命中', {bold: true});
  assert.ok(base.every(span => span.bold === true));
});
