import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import React from 'react';
import {render, cleanup} from 'ink-testing-library';
import {setTimeout as pause} from 'node:timers/promises';
import stringWidth from 'string-width';
import {ModelPanel} from '../src/model-panel.js';
import {getPalette} from '../src/theme.js';
import {validModelOverview} from '../src/model-settings.js';
afterEach(cleanup);
const overview = {model: 'old', base_url: 'https://example.com/v1', provider: 'openai',
  trust_env: false, key_present: true, sources: {model: '默认值', base_url: '默认值', api_key: '默认值', trust_env: '默认值', provider: '默认值'},
  path: '/project/.env', revision: 'a'.repeat(64), saved_model: 'old', applied: false, message: '', workspace: '/project', session_id: 's'};
test('model overview rejects any credential payload', () => {
  assert.ok(validModelOverview(overview));
  assert.equal(validModelOverview({...overview, api_key: 'secret'}), false);
});
for (const [columns, rows] of [[32,12], [80,24], [120,40]]) {
  test('model editor hides keys and fits '+columns+'x'+rows, async () => {
    const sent: unknown[] = [];
    const v = render(<ModelPanel overview={overview} loading={false} problem="" active busy={false}
      columns={columns!} rows={rows!} palette={getPalette('dark')} onRequest={(action, draft) => sent.push({action, draft})} onClose={() => {}}/>);
    const key = async (s: string) => {v.stdin.write(s); await pause(30);};
    await pause(40);
    await key('\t'); await key('\t');
    await key('\r'); await key('secret-value');
    assert.doesNotMatch(v.lastFrame()!, /secret-value/);
    assert.match(v.lastFrame()!, /隐藏/);
    await key('\x1b');
    assert.deepEqual(sent, []);
    assert.equal(v.lastFrame()!.split('\n').length, rows);
    assert.ok(v.lastFrame()!.split('\n').every(line => stringWidth(line) <= columns!));
    await key('\t'); await key('\t');
    await key('\r');
    assert.equal(sent.length, 1);
    assert.match(JSON.stringify(sent), /secret-value/);
  });
}

test('successful save clears discard warning but later editing remains protected', async () => {
  let closed=0;
  const props={overview,loading:false,problem:'',active:true,busy:false,columns:80,rows:24,
    palette:getPalette('dark'),onRequest:()=>{},onClose:()=>{closed++;}};
  const v=render(<ModelPanel {...props}/>);
  const key=async(s:string)=>{v.stdin.write(s);await pause(30);};
  await pause(40);await key('\r');await key('-new');await key('\x1b');
  for(let i=0;i<4;i++)await key('\t');
  await key('\r');
  v.rerender(<ModelPanel {...props} overview={{...overview,revision:'b'.repeat(64),message:'已保存；当前模型未切换。'}}/>);
  await pause(35);await key('\x1b');assert.equal(closed,1);
  for(let i=0;i<5;i++)await key('\t');
  await key('\r');await key('later');await key('\x1b');await key('\x1b');
  assert.equal(closed,1);assert.match(v.lastFrame()!,/未保存/);
});
