import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import React, {useState} from 'react';
import {render, cleanup} from 'ink-testing-library';
import {setTimeout as pause} from 'node:timers/promises';
import stringWidth from 'string-width';
import {DeferredHome} from '../src/deferred-home.js';
import {getPalette} from '../src/theme.js';
afterEach(cleanup);

function Harness({columns = 80, rows = 24, calls, onDemo}: {
  columns?: number; rows?: number; calls: string[]; onDemo?: () => void;
}) {
  const [draft, setDraft] = useState('');
  return <DeferredHome workspace="/Users/example/mini-harness" columns={columns} rows={rows}
    palette={getPalette('dark')} draft={draft} onChange={setDraft}
    onBlocked={() => calls.push('blocked')}
    onConnect={() => calls.push('connect')} onDemo={onDemo} onQuit={() => calls.push('quit')}/>;
}

for (const [columns, rows] of [[32, 12], [80, 24]] as const) {
  test(`deferred home fits ${columns}x${rows} and keeps the draft without submitting`, async () => {
    const calls: string[] = [];
    const v = render(<Harness columns={columns} rows={rows} calls={calls}/>);
    const key = async (s: string) => {v.stdin.write(s); await pause(30);};
    await pause(40);
    assert.match(v.lastFrame()!, /尚未连接模型/);
    assert.match(v.lastFrame()!, /不能提交真实任务/);
    await key('中文草稿'); await key('\r');
    assert.deepEqual(calls, ['blocked']);
    assert.match(v.lastFrame()!, /中文草稿/);
    assert.equal(v.lastFrame()!.split('\n').length, rows);
    assert.ok(v.lastFrame()!.split('\n').every(line => stringWidth(line) <= columns));
    await key('\x1b');
    assert.deepEqual(calls, ['blocked', 'connect']);
  });
}

test('demo is offered from the deferred home when available', async () => {
  const calls: string[] = [];
  const v = render(<Harness calls={calls} onDemo={() => calls.push('demo')}/>);
  await pause(40);
  assert.match(v.lastFrame()!, /先体验 Demo/);
});
