import assert from 'node:assert/strict';
import {test} from 'node:test';
import {editText, graphemes, lineBoundary, moveVertical, wrapEditor} from '../src/editor.js';

test('edits complete graphemes including emoji and combining accents', () => {
  const text = '中👨‍👩‍👧‍👦e\u0301文';
  assert.equal(graphemes(text).length, 4);
  assert.deepEqual(editText(text, 2, 'backspace'), {text: '中e\u0301文', cursor: 1});
  assert.deepEqual(editText(text, 2, 'delete'), {text: '中👨‍👩‍👧‍👦文', cursor: 2});
  assert.deepEqual(editText(text, 1, 'insert', '新\r\n行'), {text: '中新\n行👨‍👩‍👧‍👦e\u0301文', cursor: 4});
  assert.deepEqual(editText('e', 1, 'insert', '\u0301'), {text: 'e\u0301', cursor: 1});
});

test('line boundaries and vertical movement preserve a display column', () => {
  const text = '中文ab\nx\n下一行';
  assert.equal(lineBoundary(text, 3, 'start'), 0);
  assert.equal(lineBoundary(text, 3, 'end'), 4);
  assert.equal(moveVertical(text, 3, 1), 6);
  assert.equal(moveVertical(text, 6, 1), 7);
  assert.equal(moveVertical(text, 9, -1), 6);
  assert.deepEqual(editText(text, 8, 'clearLine'), {text: '中文ab\nx\n一行', cursor: 7});
});

test('wraps at terminal cells without splitting a CJK character or emoji', () => {
  const result = wrapEditor('中文ab\n👩‍💻好', 8, 4);
  assert.deepEqual(result.rows.map(row => row.text), ['中文', 'ab', '👩‍💻好', '']);
  assert.equal(result.cursorRow, 3);
  assert.equal(result.cursorColumn, 0);
});
