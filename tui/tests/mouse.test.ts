import assert from 'node:assert/strict';
import {test} from 'node:test';
import {TerminalMouseParser} from '../src/mouse.js';

test('SGR wheel recognizes vertical direction and modifier keys only', () => {
  const parser = new TerminalMouseParser();
  const events = parser.push('\u001b[<64;12;8M\u001b[<65;12;8M\u001b[<92;12;8M\u001b[<93;12;8M');
  assert.deepEqual(events, [
    {delta: -3, column: 12, row: 8}, {delta: 3, column: 12, row: 8},
    {delta: -3, column: 12, row: 8}, {delta: 3, column: 12, row: 8},
  ]);
  for (const sequence of ['\u001b[<0;12;8M', '\u001b[<35;12;8M', '\u001b[<66;12;8M', '\u001b[<67;12;8M', '\u001b[<96;12;8M', '\u001b[<65;12;8m']) {
    assert.deepEqual(parser.push(sequence), []);
  }
});

test('fragmented and merged reports survive every split without consuming typed text', () => {
  const sequence = '\u001b[<64;12;8M';
  for (let position = 0; position <= sequence.length; position += 1) {
    const parser = new TerminalMouseParser();
    assert.deepEqual([...parser.push('文字' + sequence.slice(0, position)), ...parser.push(sequence.slice(position) + 'y\r')], [{delta: -3, column: 12, row: 8}]);
  }
  const parser = new TerminalMouseParser();
  assert.deepEqual([...Buffer.from(sequence)].flatMap(byte => parser.push(Uint8Array.of(byte))), [{delta: -3, column: 12, row: 8}]);
});

test('bracketed paste ignores fake mouse reports even when delimiters are fragmented', () => {
  const parser = new TerminalMouseParser();
  assert.deepEqual(parser.push('\u001b[20'), []);
  assert.deepEqual(parser.push('0~任务\u001b[<64;12;8M\u001b[200~\u001b[<65;12;8M\u001b[20'), []);
  assert.deepEqual(parser.push('1~\u001b[<65;12;8M'), [{delta: 3, column: 12, row: 8}]);
});

test('malformed or oversized reports are ignored with bounded recovery', () => {
  const parser = new TerminalMouseParser();
  for (const sequence of ['\u001b[<64;0;8M', '\u001b[<64;-1;8M', '\u001b[<64;12;0M', '\u001b[<64;9999999;8M', '\u001b[<192;12;8M', '\u001b[<64;12;8:1M', '\u001b[<64;' + '9'.repeat(1_000_000)]) {
    assert.deepEqual(parser.push(sequence), []);
  }
  assert.deepEqual(parser.push('\u001b[<65;12;8M'), [{delta: 3, column: 12, row: 8}]);
  assert.deepEqual(parser.push('\u001b[<' + '9'.repeat(200) + 'M\u001b[<64;12;8M'), [{delta: -3, column: 12, row: 8}]);
});
