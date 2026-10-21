import assert from 'node:assert/strict';
import {test} from 'node:test';
import {PassThrough, Writable} from 'node:stream';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {render, Text, useInput, usePaste} from 'ink';
import {createTerminalInput} from '../src/terminal-input.js';
import {useTranscriptMouse} from '../src/use-transcript-scroll.js';

function terminalSource() {
  const raw: boolean[] = [];
  let refs = 0;
  let unrefs = 0;
  const stream = Object.assign(new PassThrough(), {isTTY: true, setRawMode(enabled: boolean) {raw.push(enabled);}, ref() {refs += 1;}, unref() {unrefs += 1;}});
  return {stream, raw, refs: () => refs, unrefs: () => unrefs};
}

test('terminal wrapper preserves ordinary UTF-8 bytes, keys and text adjacent to mouse reports', async () => {
  const {stream} = terminalSource();
  const stdin = createTerminalInput(stream);
  const output: Buffer[] = [];
  stdin.on('data', chunk => output.push(Buffer.from(chunk)));
  const original = Buffer.from('前中👩‍💻e\u0301\u001b[A\u001b[1;5D\r\n\u001b[<64;12;8M后');
  for (const byte of original) stream.write(Buffer.of(byte));
  await pause(30);
  assert.deepEqual(Buffer.concat(output), original);
  stdin.dispose();
  stream.destroy();
});

test('recognized SGR prefixes wait for their completion across slow chunks', async () => {
  const {stream} = terminalSource();
  const stdin = createTerminalInput(stream);
  const output: Buffer[] = [];
  stdin.on('data', chunk => output.push(Buffer.from(chunk)));
  stream.write('前\u001b[<64;12;');
  await pause(40);
  assert.equal(Buffer.concat(output).toString(), '前');
  stream.write('8M后\u001b[<65;12;');
  await pause(40);
  assert.equal(Buffer.concat(output).toString(), '前\u001b[<64;12;8M后');
  stream.write('8M');
  await pause(15);
  assert.equal(Buffer.concat(output).toString(), '前\u001b[<64;12;8M后\u001b[<65;12;8M');
  stdin.dispose();
  stream.destroy();
});

test('pasted SGR-looking text remains opaque and byte-exact even across slow fragments', async () => {
  const {stream} = terminalSource();
  const stdin = createTerminalInput(stream);
  const output: Buffer[] = [];
  stdin.on('data', chunk => output.push(Buffer.from(chunk)));
  stream.write('\u001b[200~粘贴\u001b[<64;12;');
  await pause(40);
  assert.equal(Buffer.concat(output).toString(), '\u001b[200~粘贴\u001b[<64;12;');
  stream.write('8M\n末尾\u001b[201~');
  await pause(15);
  assert.equal(Buffer.concat(output).toString(), '\u001b[200~粘贴\u001b[<64;12;8M\n末尾\u001b[201~');
  stdin.dispose();
  stream.destroy();
});

test('slow SGR chunks reaching real Ink never pollute the editor and still scroll', async () => {
  const {stream} = terminalSource();
  const stdin = createTerminalInput(stream);
  const stdout = Object.assign(new Writable({write(_chunk, _encoding, done) {done();}}), {isTTY: true, columns: 80, rows: 24});
  const typed: string[] = [];
  const pasted: string[] = [];
  const scrolled: number[] = [];
  function Harness() {
    useTranscriptMouse({onScroll: delta => scrolled.push(delta), top: 1, bottom: 20, left: 1, right: 80});
    useInput(input => {if (input) typed.push(input);});
    usePaste(input => pasted.push(input));
    return <Text>会话</Text>;
  }
  const view = render(<Harness/>, {stdin, stdout, stderr: stdout, debug: true, patchConsole: false, exitOnCtrlC: false, kittyKeyboard: {mode: 'disabled'}});
  try {
    await pause(25);
    stream.write('前\u001b[<64;12;');
    await pause(40);
    stream.write('8M后\u001b[<65;12;8M');
    await pause(15);
    assert.equal(typed.join(''), '前后');
    assert.deepEqual(scrolled, [-3, 3]);
    stream.write('\u001b[200~粘贴\u001b[<64;12;');
    await pause(40);
    stream.write('8M\u001b[201~');
    await pause(15);
    assert.equal(typed.join(''), '前后');
    assert.deepEqual(pasted, ['粘贴\u001b[<64;12;8M']);
    assert.deepEqual(scrolled, [-3, 3]);
  } finally {
    view.unmount();
    view.cleanup();
    stdin.dispose();
    stream.destroy();
    stdout.destroy();
  }
});

test('dispose releases source listeners and timers without destroying the terminal', async () => {
  const source = terminalSource();
  const initial = {data: source.stream.listenerCount('data'), end: source.stream.listenerCount('end'), error: source.stream.listenerCount('error')};
  const stdin = createTerminalInput(source.stream);
  const output: Buffer[] = [];
  stdin.on('data', chunk => output.push(Buffer.from(chunk)));
  stdin.setRawMode(true);
  stdin.ref();
  stdin.unref();
  stdin.setRawMode(false);
  source.stream.write('\u001b[');
  await pause(1);
  stdin.dispose();
  stdin.dispose();
  await pause(30);
  assert.deepEqual(source.raw, [true, false]);
  assert.equal(source.refs(), 1);
  assert.equal(source.unrefs(), 1);
  assert.equal(source.stream.destroyed, false);
  assert.equal(source.stream.listenerCount('data'), initial.data);
  assert.equal(source.stream.listenerCount('end'), initial.end);
  assert.equal(source.stream.listenerCount('error'), initial.error);
  assert.equal(Buffer.concat(output).length, 0);
  source.stream.destroy();
});

test('oversized SGR packets have bounded storage and recover for later reports', async () => {
  const {stream} = terminalSource();
  const stdin = createTerminalInput(stream);
  const output: Buffer[] = [];
  stdin.on('data', chunk => output.push(Buffer.from(chunk)));
  stream.write('\u001b[<' + '9'.repeat(1_000_000));
  stream.write('M\u001b[<65;12;8M正常');
  await pause(20);
  assert.equal(Buffer.concat(output).toString(), '\u001b[<65;12;8M正常');
  stdin.dispose();
  stream.destroy();
});
