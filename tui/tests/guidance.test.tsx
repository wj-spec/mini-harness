import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import React from 'react';
import {render, cleanup} from 'ink-testing-library';
import {setTimeout as pause} from 'node:timers/promises';
import stringWidth from 'string-width';
import {guidanceHint, guidePages} from '../src/guidance.js';
import {GuidePanel} from '../src/guidance-panel.js';
import {initialState} from '../src/view-state.js';
import {getPalette} from '../src/theme.js';

afterEach(cleanup);
const available = {tasks: true, plan: true, inspection: true, permissions: true};
test('guidance follows lifecycle without claiming completion or replacing approval', () => {
  assert.match(guidanceHint({...initialState, phase: 'starting'}, 80), /就绪/);
  assert.match(guidanceHint({...initialState, phase: 'waiting_approval'}, 80), /核对/);
  assert.match(guidanceHint({...initialState, phase: 'running'}, 80), /纠偏/);
  assert.match(guidanceHint({...initialState, phase: 'idle', resumed: true}, 80), /尚未运行/);
  assert.match(guidanceHint({...initialState, phase: 'idle', lastStatus: 'error'}, 80), /错误/);
  assert.match(guidanceHint({...initialState, phase: 'idle', lastStatus: 'stopped', lastStopReason: 'acceptance_failed'}, 32), /plan acceptance/);
  assert.doesNotMatch(guidanceHint({...initialState, phase: 'idle', lastStatus: 'done'}, 80), /需求完成|验收通过/);
  assert.match(guidanceHint({...initialState, phase: 'running', attachedTaskId: 'task'}, 80), /断开/);
});
test('guide advertises only available navigation and real configuration paths', () => {
  const pages = guidePages({tasks: false, plan: false, inspection: false, permissions: false});
  assert.equal(pages.length, 5);
  assert.equal(pages[1]!.action, undefined);
  assert.equal(pages[2]!.action, undefined);
  assert.equal(pages[3]!.action, undefined);
  assert.match(pages[4]!.body, /start.sh --configure/);
  assert.match(pages[4]!.body, /尚未支持/);
});
for (const [columns, rows] of [[32, 12], [80, 24], [120, 40]]) {
  test(`guide fits ${columns}x${rows} and keyboard navigation never executes by reading`, async () => {
    const actions: string[] = []; let closed = 0; let toggles = 0;
    const view = render(<GuidePanel pages={guidePages(available)} columns={columns!} rows={rows!}
      palette={getPalette('dark')} active tips context="先描述任务" onAction={action => {actions.push(action);}}
      onToggle={() => {toggles++;}} onReturn={() => {closed++;}}/>);
    await pause(40);
    assert.equal(view.lastFrame()!.split('\n').length, rows);
    assert.ok(view.lastFrame()!.split('\n').every(line => stringWidth(line) <= columns!));
    view.stdin.write('\x1b[C'); await pause(25);
    assert.match(view.lastFrame()!, /计划/); assert.deepEqual(actions, []);
    view.stdin.write('\x1b[B'); await pause(25);
    view.stdin.write('\t'); await pause(25); view.stdin.write('\r'); await pause(25);
    assert.equal(toggles, 1); assert.deepEqual(actions, []);
    view.stdin.write('\x1b'); await pause(25); assert.equal(closed, 1);
  });
}
