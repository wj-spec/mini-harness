import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React, {useState} from 'react';
import {cleanup, render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {CommandMenu} from '../src/command-menu.js';
import {Composer} from '../src/composer.js';
import {COMMAND_COMPLETIONS, COMMAND_DESCRIPTIONS, commandMatches} from '../src/protocol.js';

afterEach(cleanup);

test('every command carries an honest description', () => {
  for (const command of COMMAND_COMPLETIONS) assert.ok(COMMAND_DESCRIPTIONS[command], `${command} 缺少说明`);
  assert.equal(commandMatches('/co').length, 4);
  assert.equal(commandMatches('任务').length, 0);
});

test('menu shows descriptions and scrolls the window with the highlight', () => {
  const matches = [...COMMAND_COMPLETIONS];
  const {lastFrame} = render(<CommandMenu matches={matches} index={11} rows={5} columns={80} accentColor="cyan" foregroundColor="white" secondaryColor="gray" surfaceColor="black"/>);
  const frame = lastFrame() ?? '';
  assert.doesNotMatch(frame, /[┌┐└┘│─╭╮╰╯]/u);
  assert.match(frame, /› \/session/);
  assert.match(frame, /查看会话路径/);
  assert.match(frame, /\/skills {2,}查看与切换技能/);
  assert.doesNotMatch(frame, /\/help/);
  assert.doesNotMatch(frame, /\/exit/);
  for (const line of frame.split('\n')) assert.ok(stringWidth(line) <= 80, line);
});

async function mountMenu() {
  const values: string[] = [];
  const submitted: string[] = [];
  function Harness() {
    const [value, setValue] = useState('');
    const [index, setIndex] = useState(0);
    const matches = commandMatches(value);
    const position = Math.min(index, Math.max(0, matches.length - 1));
    const menu = matches.length ? {
      count: matches.length,
      index: position,
      onMove: (delta: number) => setIndex(current => (current + delta + matches.length) % matches.length),
      onAccept: () => {const next = matches[position]; setIndex(0); values.push(next); setValue(next);},
    } : undefined;
    return <Composer columns={80} maxRows={3} value={value} onChange={next => {setIndex(0); values.push(next); setValue(next);}} onSubmit={next => {submitted.push(next); setValue('');}} history={[]} menu={menu}/>;
  }
  const view = render(<Harness/>);
  await pause(25);
  const key = async (input: string) => {view.stdin.write(input); await pause(15);};
  return {...view, key, values, submitted};
}

test('slash opens the menu with guidance and arrows scroll the selection', async () => {
  const {key, lastFrame} = await mountMenu();
  await key('/');
  assert.match(lastFrame() ?? '', /↑↓ 选择 · Tab 填充 · 1\/20/);
  await key('\u001b[B');
  await key('\u001b[B');
  assert.match(lastFrame() ?? '', /3\/20/);
  await key('\u001b[A');
  assert.match(lastFrame() ?? '', /2\/20/);
});

test('Tab fills the highlighted command while scrolling', async () => {
  const {key, values, submitted} = await mountMenu();
  await key('/p');
  await key('\u001b[B');
  await key('\u001b[B');
  await key('\t');
  assert.equal(values.at(-1), '/project');
  assert.deepEqual(submitted, []);
});

test('Enter always submits so command aliases keep working', async () => {
  const {key, submitted} = await mountMenu();
  await key('/co');
  await key('\r');
  assert.deepEqual(submitted, ['/co']);
  await key('/help');
  await key('\r');
  assert.deepEqual(submitted, ['/co', '/help']);
});

test('Enter does not steal history navigation outside the menu', async () => {
  const {key, values, submitted} = await mountMenu();
  await key('任务甲');
  await key('\r');
  await key('\u001b[A');
  assert.equal(values.at(-1), '任务甲');
  assert.deepEqual(submitted, ['任务甲']);
});
