import assert from 'node:assert/strict';
import {test} from 'node:test';
import {normalizeSelection, selectedText, selectionCells, sliceCells, splitCells} from '../src/selection.js';
import type {ReadingLine} from '../src/markdown.js';

const line = (text: string): ReadingLine => ({spans: [{text}]});

test('selection is normalized regardless of drag direction', () => {
  const reversed = {anchor: {line: 2, cell: 5}, head: {line: 0, cell: 1}};
  assert.deepEqual(normalizeSelection(reversed), {start: {line: 0, cell: 1}, end: {line: 2, cell: 5}});
});

test('selectedText joins lines and keeps only selected cells', () => {
  const lines = [line('hello world'), line('second line'), line('third')];
  const text = selectedText(lines, {anchor: {line: 0, cell: 6}, head: {line: 1, cell: 6}});
  assert.equal(text, 'world\nsecond');
});

test('CJK cells are sliced by display width, not character count', () => {
  assert.equal(sliceCells('你好世界', 0, 4), '你好');
  assert.equal(sliceCells('你好世界', 1, 4), '你好');
  assert.equal(selectedText([line('你好世界')], {anchor: {line: 0, cell: 0}, head: {line: 0, cell: 2}}), '你');
});

test('splitCells assigns boundary-crossing characters to the highlighted middle segment', () => {
  assert.deepEqual(splitCells('你好世界', 1, 3), {before: '', middle: '你好', after: '世界'});
  assert.deepEqual(splitCells('abc', 1, 2), {before: 'a', middle: 'b', after: 'c'});
});

test('selectionCells marks every covered line, full lines except the edges', () => {
  const selection = {anchor: {line: 1, cell: 2}, head: {line: 3, cell: 4}};
  assert.equal(selectionCells(selection, 0), null);
  assert.deepEqual(selectionCells(selection, 1), {start: 2, end: Number.POSITIVE_INFINITY});
  assert.deepEqual(selectionCells(selection, 2), {start: 0, end: Number.POSITIVE_INFINITY});
  assert.deepEqual(selectionCells(selection, 3), {start: 0, end: 4});
  assert.equal(selectionCells(selection, 4), null);
  assert.equal(selectionCells(null, 1), null);
});
