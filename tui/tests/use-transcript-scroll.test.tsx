import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import {PassThrough, Writable} from 'node:stream';
import React from 'react';
import {render as inkRender, Text, useInput} from 'ink';
import {cleanup, render} from 'ink-testing-library';
import {useTranscriptMouse} from '../src/use-transcript-scroll.js';

afterEach(cleanup);

type HarnessProps = {onScroll: (delta: number) => void; onInput?: (input: string) => void; top?: number; bottom?: number; enabled?: boolean};
function Harness({onScroll, onInput, top = 4, bottom = 15, enabled = true}: HarnessProps) {
  useTranscriptMouse({onScroll, top, bottom, left: 2, right: 78, enabled});
  useInput(input => onInput?.(input));
  return <Text>会话</Text>;
}

test('wheel scrolls only within the current reader bounds and never becomes input', async () => {
  const scrolled: number[] = [];
  const typed: string[] = [];
  const view = render(<Harness onScroll={delta => scrolled.push(delta)} onInput={input => typed.push(input)}/>);
  await pause(25);
  for (const sequence of ['\u001b[<64;2;4M', '\u001b[<65;78;15M', '\u001b[<65;1;5M', '\u001b[<65;79;5M', '\u001b[<65;4;3M', '\u001b[<65;4;16M']) view.stdin.write(sequence);
  await pause(10);
  assert.deepEqual(scrolled, [-3, 3]);
  assert.deepEqual(typed, []);
  view.rerender(<Harness onScroll={delta => scrolled.push(delta * 10)} top={10} bottom={20}/>);
  await pause(10);
  view.stdin.write('\u001b[<65;10;5M\u001b[<65;10;18M');
  await pause(10);
  assert.deepEqual(scrolled, [-3, 3, 30]);
  view.rerender(<Harness onScroll={delta => scrolled.push(delta)} enabled={false}/>);
  await pause(10);
  view.stdin.write('\u001b[<65;10;5M');
  await pause(10);
  assert.deepEqual(scrolled, [-3, 3, 30]);
});

test('real readable streams enable mouse once and restore modes/listeners on unmount', async () => {
  const stdin = Object.assign(new PassThrough(), {isTTY: true, setRawMode() {}, ref() {}, unref() {}});
  let output = '';
  const stdout = Object.assign(new Writable({write(chunk, _encoding, done) {output += String(chunk); done();}}), {isTTY: true, columns: 80, rows: 24});
  const scrolled: number[] = [];
  const view = inkRender(<Harness onScroll={delta => scrolled.push(delta)}/>, {stdin, stdout, stderr: stdout, debug: true, patchConsole: false, exitOnCtrlC: false, kittyKeyboard: {mode: 'disabled'}});
  try {
    await pause(30);
    assert.match(output, /\u001b\[\?1000h\u001b\[\?1006h/);
    assert.equal((output.match(/\u001b\[\?1000h/g) ?? []).length, 1);
    assert.equal(stdin.listenerCount('data'), 1);
    stdin.write('\u001b[<64;10;5M');
    stdin.write('\u001b[<65;10;5M');
    await pause(15);
    assert.deepEqual(scrolled, [-3, 3]);
  } finally {
    view.unmount();
    view.cleanup();
  }
  await pause(10);
  assert.match(output, /\u001b\[\?1006l\u001b\[\?1000l/);
  assert.equal(stdin.listenerCount('data'), 0);
  stdin.destroy();
  stdout.destroy();
});

test('wheel decoding survives delayed fragments from a terminal stream', async () => {
  const scrolled: number[] = [];
  const view = render(<Harness onScroll={delta => scrolled.push(delta)}/>);
  await pause(25);
  view.stdin.write('\u001b[<64;12;');
  await pause(35);
  view.stdin.write('8M');
  await pause(10);
  assert.deepEqual(scrolled, [-3]);
});
