import assert from 'node:assert/strict';
import {test} from 'node:test';
import {TerminalMouseParser} from '../src/mouse.js';

test('SGR wheel recognizes vertical direction and modifier keys', () => {
  const parser = new TerminalMouseParser();
  const events = parser.push('\u001b[<64;12;8M\u001b[<65;12;8M\u001b[<92;12;8M\u001b[<93;12;8M');
  assert.deepEqual(events, [
    {type: 'wheel', delta: -3, column: 12, row: 8}, {type: 'wheel', delta: 3, column: 12, row: 8},
    {type: 'wheel', delta: -3, column: 12, row: 8}, {type: 'wheel', delta: 3, column: 12, row: 8},
  ]);
  for (const sequence of ['\u001b[<66;12;8M', '\u001b[<67;12;8M', '\u001b[<96;12;8M']) {
    assert.deepEqual(parser.push(sequence), []);
  }
});

test('SGR button reports distinguish press, drag and release for the left button', () => {
  const parser = new TerminalMouseParser();
  assert.deepEqual(parser.push('\u001b[<0;12;8M'), [{type: 'press', button: 0, column: 12, row: 8}]);
  assert.deepEqual(parser.push('\u001b[<32;14;8M'), [{type: 'drag', button: 0, column: 14, row: 8}]);
  assert.deepEqual(parser.push('\u001b[<0;14;8m'), [{type: 'release', button: 0, column: 14, row: 8}]);
  assert.deepEqual(parser.push('\u001b[<2;3;4M'), [{type: 'press', button: 2, column: 3, row: 4}]);
  for (const sequence of ['\u001b[<35;12;8M', '\u001b[<3;12;8m']) assert.deepEqual(parser.push(sequence), []);
});

test('fragmented and merged reports survive every split without consuming typed text', () => {
  const sequence = '\u001b[<32;12;8M';
  for (let position = 0; position <= sequence.length; position += 1) {
    const parser = new TerminalMouseParser();
    assert.deepEqual([...parser.push('文字' + sequence.slice(0, position)), ...parser.push(sequence.slice(position) + 'y\r')], [{type: 'drag', button: 0, column: 12, row: 8}]);
  }
  const parser = new TerminalMouseParser();
  assert.deepEqual([...Buffer.from(sequence)].flatMap(byte => parser.push(Uint8Array.of(byte))), [{type: 'drag', button: 0, column: 12, row: 8}]);
});

test('bracketed paste ignores fake mouse reports even when delimiters are fragmented', () => {
  const parser = new TerminalMouseParser();
  assert.deepEqual(parser.push('\u001b[20'), []);
  assert.deepEqual(parser.push('0~任务\u001b[<64;12;8M\u001b[200~\u001b[<65;12;8M\u001b[20'), []);
  assert.deepEqual(parser.push('1~\u001b[<65;12;8M'), [{type: 'wheel', delta: 3, column: 12, row: 8}]);
});

test('malformed or oversized reports are ignored with bounded recovery', () => {
  const parser = new TerminalMouseParser();
  for (const sequence of ['\u001b[<64;0;8M', '\u001b[<64;-1;8M', '\u001b[<64;12;0M', '\u001b[<64;9999999;8M', '\u001b[<192;12;8M', '\u001b[<64;12;8:1M', '\u001b[<64;' + '9'.repeat(1_000_000)]) {
    assert.deepEqual(parser.push(sequence), []);
  }
  assert.deepEqual(parser.push('\u001b[<65;12;8M'), [{type: 'wheel', delta: 3, column: 12, row: 8}]);
  assert.deepEqual(parser.push('\u001b[<' + '9'.repeat(200) + 'M\u001b[<64;12;8M'), [{type: 'wheel', delta: -3, column: 12, row: 8}]);
});

test('legacy X10 reports decode press, drag and release too', () => {
  const parser = new TerminalMouseParser();
  const x10 = (raw: number, column: number, row: number) => String.fromCharCode(27) + '[M' + String.fromCharCode(raw + 32, column + 32, row + 32);
  assert.deepEqual(parser.push(x10(0, 12, 8)), [{type: 'press', button: 0, column: 12, row: 8}]);
  assert.deepEqual(parser.push(x10(32, 14, 8)), [{type: 'drag', button: 0, column: 14, row: 8}]);
  assert.deepEqual(parser.push(x10(3, 14, 8)), [{type: 'release', button: 0, column: 14, row: 8}]);
  assert.deepEqual(parser.push(x10(64, 12, 8)), [{type: 'wheel', delta: -3, column: 12, row: 8}]);
});
