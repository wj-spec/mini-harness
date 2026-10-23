import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React, {useState} from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {Composer} from '../src/composer.js';

afterEach(cleanup);

async function mount(options: {history?: string[]; disabled?: boolean; focused?: boolean; columns?: number; maxRows?: number} = {}) {
  const values: string[] = [];
  const submitted: string[] = [];
  function Harness() {
    const [value, setValue] = useState('');
    return <Composer {...options} columns={options.columns ?? 40} value={value} onChange={next => {values.push(next); setValue(next);}} onSubmit={next => {submitted.push(next); setValue('');}} history={options.history ?? []}/>;
  }
  const view = render(<Harness/>);
  await pause(25);
  const key = async (input: string) => {view.stdin.write(input); await pause(15);};
  return {...view, key, values, submitted};
}

test('Chinese multiline input submits only on Enter and resets after submit', async () => {
  const {key, values, submitted} = await mount();
  await key('第一行');
  await key('\n');
  await key('第二行');
  assert.equal(values.at(-1), '第一行\n第二行');
  assert.deepEqual(submitted, []);
  await key('\r');
  assert.deepEqual(submitted, ['第一行\n第二行']);
  await key('下一轮');
  await key('\r');
  assert.deepEqual(submitted, ['第一行\n第二行', '下一轮']);
});

test('bracketed paste keeps newlines and never submits', async () => {
  const {key, values, submitted} = await mount();
  await key('\u001b[200~粘贴第一行\r\n第二行\u001b[201~');
  assert.equal(values.at(-1), '粘贴第一行\n第二行');
  assert.deepEqual(submitted, []);
});

test('moves and deletes whole graphemes', async () => {
  const {key, values} = await mount();
  await key('中👨‍👩‍👧‍👦e\u0301文');
  await key('\u001b[D');
  await key('\u007f');
  assert.equal(values.at(-1), '中👨‍👩‍👧‍👦文');
  await key('\u001b[D');
  await key('\u001b[3~');
  assert.equal(values.at(-1), '中文');
});

test('history restores the draft and does not steal multiline vertical navigation', async () => {
  const {key, values} = await mount({history: ['第一轮', '第二轮']});
  await key('草稿');
  await key('\u001b[A');
  assert.equal(values.at(-1), '第二轮');
  await key('\u001b[A');
  assert.equal(values.at(-1), '第一轮');
  await key('\u001b[B');
  await key('\u001b[B');
  assert.equal(values.at(-1), '草稿');
  await key('\n');
  await key('尾行');
  await key('\u001b[A');
  assert.equal(values.at(-1), '草稿\n尾行');
  await key('头');
  assert.equal(values.at(-1), '草稿头\n尾行');
});

test('busy state keeps editable draft and disables submit; unfocused ignores typing and paste', async () => {
  const busy = await mount({disabled: true});
  await busy.key('下一条草稿');
  await busy.key('\r');
  assert.equal(busy.values.at(-1), '下一条草稿');
  assert.deepEqual(busy.submitted, []);
  assert.match(busy.lastFrame() ?? '', /草稿|运行/);
  busy.unmount();
  const blurred = await mount({focused: false});
  await blurred.key('不能输入');
  await blurred.key('\u001b[200~不能粘贴\u001b[201~');
  assert.deepEqual(blurred.values, []);
});

test('unfocused busy input does not claim that its draft is editable', async () => {
  const view = await mount({disabled: true, focused: false});
  assert.doesNotMatch(view.lastFrame() ?? '', /草稿可编辑/);
});

test('same-tick keystrokes append to the latest draft before submitting', async () => {
  const view = await mount();
  view.stdin.write('a');
  view.stdin.write('b');
  view.stdin.write('c');
  view.stdin.write('\r');
  await pause(25);
  assert.deepEqual(view.submitted, ['abc']);
});

test('same-tick paste fragments append without submitting their newlines', async () => {
  const view = await mount();
  view.stdin.write('\u001b[200~第一行\n\u001b[201~');
  view.stdin.write('\u001b[200~第二行\u001b[201~');
  view.stdin.write('尾');
  await pause(25);
  assert.equal(view.values.at(-1), '第一行\n第二行尾');
  assert.deepEqual(view.submitted, []);
});

test('same-tick cursor movement and deletion edit the latest position', async () => {
  const view = await mount();
  await view.key('中文ab');
  view.stdin.write('\u001b[D');
  view.stdin.write('X');
  view.stdin.write('\u007f');
  view.stdin.write('Y');
  view.stdin.write('\r');
  await pause(25);
  assert.deepEqual(view.submitted, ['中文aYb']);
});

test('Tab completes local commands and unrelated global keys do not insert text', async () => {
  const {key, values, lastFrame} = await mount();
  await key('/');
  assert.match(lastFrame() ?? '', /help|cost|命令/);
  await key('to');
  await key('\t');
  assert.equal(values.at(-1), '/tools');
  for (const input of ['\u001b[5~', '\u001b[6~', '\u0011', '\u000f']) await key(input);
  assert.equal(values.at(-1), '/tools');
});

test('new local commands are discoverable through completion', async () => {
  const {key, values} = await mount();
  await key('/ski');
  await key('\t');
  assert.equal(values.at(-1), '/skills');
});

test('context inspection is discoverable through local command completion', async () => {
  const {key, values, submitted} = await mount();
  await key('/conte');
  await key('\t');
  assert.equal(values.at(-1), '/context');
  assert.deepEqual(submitted, []);
});

test('Home, End and Ctrl+U affect the current line', async () => {
  const {key, values} = await mount();
  await key('头行');
  await key('\n');
  await key('尾行');
  await key('\u001b[H');
  await key('中');
  assert.equal(values.at(-1), '头行\n中尾行');
  await key('\u001b[F');
  await key('\u0015');
  assert.equal(values.at(-1), '头行\n');
});

test('an accent arriving as a separate key still moves as one grapheme', async () => {
  const {key, values} = await mount();
  await key('e');
  await key('\u0301');
  await key('\u001b[D');
  await key('X');
  assert.equal(values.at(-1), 'Xe\u0301');
});

test('resume and migrated commands are discoverable and complete without sending', async () => {
  const withCommands = await mount();
  await withCommands.key('/resume');
  assert.match(withCommands.lastFrame() ?? '', /继续|预算/);
  assert.doesNotMatch(withCommands.lastFrame() ?? '', /尚未接入/);
  await withCommands.key('\u0015');
  await withCommands.key('/per');
  await withCommands.key('\t');
  assert.equal(withCommands.values.at(-1), '/permissions');
  await withCommands.key(' re');
  await withCommands.key('\t');
  assert.equal(withCommands.values.at(-1), '/permissions reset');
  assert.deepEqual(withCommands.submitted, []);
  withCommands.unmount();
});

test('one-row input retains an overflow marker', async () => {
  const single = await mount({maxRows: 1, columns: 14});
  await single.key('中文'.repeat(10));
  assert.equal((single.lastFrame() ?? '').split('\n').length, 1);
  assert.match(single.lastFrame() ?? '', /↑/);
});

test('viewport has fixed height and shows overflow position without cutting wide characters', async () => {
  const {key, lastFrame} = await mount({columns: 18, maxRows: 3});
  await key('中文'.repeat(35));
  const rows = (lastFrame() ?? '').split('\n');
  assert.equal(rows.length, 3);
  assert.ok(rows.every(row => stringWidth(row) <= 18));
  assert.match(lastFrame() ?? '', /行|↑|↓/);
});
