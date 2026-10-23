import assert from 'node:assert/strict';
import test from 'node:test';
import {copyToClipboard, MAX_COPY_BYTES} from '../src/clipboard.js';

test('uses the macOS clipboard command first', () => {
  const calls: [string, string[], string][] = [];
  const method = copyToClipboard('你好，世界', {
    platform: 'darwin',
    run: (command, args, input) => {calls.push([command, args, input]); return true;},
    write: () => {throw new Error('must not fall back');},
  });
  assert.equal(method, 'pbcopy');
  assert.deepEqual(calls, [['pbcopy', [], '你好，世界']]);
});

test('falls back to OSC 52 when no clipboard command is available', () => {
  let written = '';
  const method = copyToClipboard('回复内容', {
    platform: 'linux',
    run: () => false,
    write: sequence => {written = sequence;},
  });
  assert.equal(method, 'osc52');
  assert.match(written, /^\u001b\]52;c;[A-Za-z0-9+/=]+\u0007$/);
  const encoded = written.slice('\u001b]52;c;'.length, -1);
  assert.equal(Buffer.from(encoded, 'base64').toString('utf8'), '回复内容');
});

test('empty text is not copied anywhere', () => {
  let touched = false;
  const method = copyToClipboard('', {
    platform: 'darwin',
    run: () => {touched = true; return true;},
    write: () => {touched = true;},
  });
  assert.equal(method, null);
  assert.equal(touched, false);
});

test('oversized text is truncated to the byte cap before copying', () => {
  let copied = '';
  const method = copyToClipboard('x'.repeat(MAX_COPY_BYTES + 100), {
    platform: 'darwin',
    run: (_command, _args, input) => {copied = input; return true;},
    write: () => {},
  });
  assert.equal(method, 'pbcopy');
  assert.equal(Buffer.byteLength(copied, 'utf8'), MAX_COPY_BYTES);
});
