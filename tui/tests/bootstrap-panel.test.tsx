import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import React from 'react';
import {render, cleanup} from 'ink-testing-library';
import {setTimeout as pause} from 'node:timers/promises';
import stringWidth from 'string-width';
import {BootstrapPanel} from '../src/bootstrap-panel.js';
import {getPalette} from '../src/theme.js';
afterEach(cleanup);

const defaults = {base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat'};
const overview = {model: 'deepseek-chat', base_url: 'https://api.deepseek.com/v1', provider: 'openai',
  trust_env: false, key_present: false,
  sources: {model: '默认值', base_url: '默认值', api_key: '默认值', trust_env: '默认值', provider: '默认值'},
  path: '/project/.env', revision: 'a'.repeat(64), saved_model: '', applied: false, message: '',
  workspace: '/project', session_id: ''};

function mount(columns: number, rows: number, props: Record<string, unknown> = {}) {
  const calls: {kind: string; action?: string; draft?: Record<string, unknown>}[] = [];
  const panel = (extra: Record<string, unknown> = {}) => <BootstrapPanel
    workspace="/Users/example/mini-harness" defaults={defaults} overview={overview}
    loading={false} problem="" problemField="" activating={false} active
    columns={columns} rows={rows} palette={getPalette('dark')}
    onRequest={(action, draft) => calls.push({kind: 'model', action, draft})}
    onCancel={() => calls.push({kind: 'cancel'})}
    onActivate={() => calls.push({kind: 'activate'})}
    onLater={() => calls.push({kind: 'later'})}
    onDemo={() => calls.push({kind: 'demo'})}
    onQuit={() => calls.push({kind: 'quit'})} {...props} {...extra}/>;
  const v = render(panel());
  const key = async (s: string) => {v.stdin.write(s); await pause(30);};
  return {...v, calls, key, view: (extra: Record<string, unknown>) => v.rerender(panel(extra))};
}

for (const [columns, rows] of [[32, 12], [80, 24], [120, 40]] as const) {
  test(`first-run page fits ${columns}x${rows}, defaults to the service and hides keys`, async () => {
    const v = mount(columns, rows);
    await pause(40);
    assert.match(v.lastFrame()!, /陪你完成第一个编码任务/);
    assert.match(v.lastFrame()!, /连接模型/);
    assert.match(v.lastFrame()!, /模型服务/);
    if (columns < 48) {
      assert.match(v.lastFrame()!, /步骤 1\/2/);
      await v.key('\t');
      assert.match(v.lastFrame()!, /API Key/);
    } else {
      assert.match(v.lastFrame()!, /API Key/);
      assert.doesNotMatch(v.lastFrame()!, /服务地址/);
      assert.doesNotMatch(v.lastFrame()!, /› 模型名/);
    }
    if (columns >= 48) await v.key('\t');
    await v.key('\r'); await v.key('sk-secret-value');
    assert.doesNotMatch(v.lastFrame()!, /sk-secret-value/);
    assert.match(v.lastFrame()!, /已填写（隐藏）/);
    assert.equal(v.lastFrame()!.split('\n').length, rows);
    assert.ok(v.lastFrame()!.split('\n').every(line => stringWidth(line) <= columns));
    assert.equal(v.calls.length, 0);
  });
}

test('custom service reveals address and model fields and can switch back', async () => {
  const v = mount(80, 24);
  await pause(40);
  await v.key('\r');
  assert.match(v.lastFrame()!, /服务地址/);
  assert.match(v.lastFrame()!, /模型名/);
  await v.key('\r');
  assert.match(v.lastFrame()!, /模型服务/);
  assert.match(v.lastFrame()!, /默认 · DeepSeek/);
  assert.doesNotMatch(v.lastFrame()!, /服务地址/);
});

test('save and test chains: save first, then test after the save receipt', async () => {
  const v = mount(80, 24);
  await pause(40);
  await v.key('\t'); await v.key('\r'); await v.key('sk-secret-value'); await v.key('\x1b');
  await v.key('\t'); await v.key('\r');
  assert.equal(v.calls.length, 1);
  assert.equal(v.calls[0]!.action, 'save');
  v.view({overview: {...overview, message: '已保存；进入项目后生效。', key_present: true, revision: 'b'.repeat(64)}});
  await pause(40);
  assert.equal(v.calls.length, 2);
  assert.equal(v.calls[1]!.action, 'test');
  assert.equal((v.calls[1]!.draft as Record<string, unknown>)!.revision, 'b'.repeat(64));
});

test('missing key is caught before sending and focuses the field', async () => {
  const v = mount(80, 24);
  await pause(40);
  await v.key('\t'); await v.key('\t');
  await v.key('\r');
  assert.match(v.lastFrame()!, /请填写 API Key/);
  assert.equal(v.calls.length, 0);
  assert.match(v.lastFrame()!, /› API Key/);
});

test('a failed detection after save is labelled as saved-but-not-detected', async () => {
  const v = mount(80, 24);
  await pause(40);
  await v.key('\t'); await v.key('\r'); await v.key('sk-secret-value'); await v.key('\x1b');
  await v.key('\t'); await v.key('\r');
  v.view({overview: {...overview, message: '已保存；进入项目后生效。', key_present: true}});
  await pause(40);
  v.view({overview: {...overview, message: '检测未通过：HTTP 401；未保存或应用。', key_present: true}});
  await pause(40);
  assert.match(v.lastFrame()!, /已保存；检测未通过：HTTP 401/);
});

test('a passed detection leads to the project confirmation page', async () => {
  const v = mount(80, 24);
  await pause(40);
  v.view({overview: {...overview, message: '连接检测通过；只验证最小请求。', key_present: true}});
  await pause(40);
  for (let i = 0; i < 4; i += 1) await v.key('\t'); // service→api_key→save-test→save→activate
  await v.key('\r');
  assert.match(v.lastFrame()!, /准备进入/);
  assert.match(v.lastFrame()!, /项目确认/);
  assert.match(v.lastFrame()!, /mini-harness/);
  await v.key('\r');
  assert.deepEqual(v.calls, [{kind: 'activate'}]);
});

test('saved but undetected configuration asks before skipping detection', async () => {
  const v = mount(80, 24);
  await pause(40);
  await v.key('\t'); await v.key('\r'); await v.key('sk-secret-value'); await v.key('\x1b');
  await v.key('\t'); await v.key('\t'); await v.key('\r'); // 仅保存
  assert.equal(v.calls[0]!.action, 'save');
  v.view({overview: {...overview, message: '已保存；进入项目后生效。', key_present: true}});
  await pause(40);
  await v.key('\t');
  await v.key('\r');
  assert.match(v.lastFrame()!, /将跳过检测/);
  await v.key('\r');
  assert.match(v.lastFrame()!, /准备进入/);
});

test('cancel is offered while an operation is loading', async () => {
  const v = mount(80, 24, {loading: true});
  await pause(40);
  assert.match(v.lastFrame()!, /取消操作/);
  await v.key('\x1b');
  assert.deepEqual(v.calls, [{kind: 'cancel'}]);
});

test('quit warns about unsaved edits', async () => {
  const v = mount(80, 24);
  await pause(40);
  await v.key('\t'); await v.key('\r'); await v.key('sk-secret-value'); await v.key('\x1b');
  for (let i = 0; i < 4; i += 1) await v.key('\t');
  await v.key('\r');
  assert.match(v.lastFrame()!, /未保存编辑/);
  assert.equal(v.calls.length, 0);
  await v.key('\r');
  assert.deepEqual(v.calls, [{kind: 'quit'}]);
});

test('backend field errors reveal the advanced field and show the message inline', async () => {
  const v = mount(80, 24, {problem: '服务地址需要是 http(s) URL，不能包含凭据、查询或片段。', problemField: 'base_url'});
  await pause(50);
  assert.match(v.lastFrame()!, /› 服务地址/);
  assert.match(v.lastFrame()!, /服务地址需要是 http\(s\) URL/);
});

test('later and demo entries are reachable and clickable', async () => {
  const v = mount(80, 24);
  await pause(40);
  for (let i = 0; i < 6; i += 1) await v.key('\t');
  await v.key('\r');
  assert.deepEqual(v.calls, [{kind: 'later'}]);
  const w = mount(80, 24);
  await pause(40);
  for (let i = 0; i < 7; i += 1) await w.key('\t');
  await w.key('\r');
  assert.deepEqual(w.calls, [{kind: 'demo'}]);
});

test('demo startup status is visible on the first-run page', async () => {
  const v = mount(80, 24, {demoStarting: true});
  await pause(40);
  assert.match(v.lastFrame()!, /正在启动 Demo/);
});

test('clicking the service row toggles the advanced fields', async () => {
  const v = mount(80, 24);
  await pause(50);
  const click = async (label: string) => {
    const lines = v.lastFrame()!.split('\n');
    const row = lines.findIndex(line => line.includes(label));
    assert.ok(row >= 0, label);
    const column = stringWidth(lines[row]!.slice(0, lines[row]!.indexOf(label))) + 2;
    v.stdin.write(`\x1b[<0;${column};${row + 1}M\x1b[<0;${column};${row + 1}m`);
    await pause(40);
  };
  await click('模型服务');
  assert.match(v.lastFrame()!, /服务地址/);
  await click('模型服务');
  assert.doesNotMatch(v.lastFrame()!, /服务地址/);
});


test('user configuration location is shown independently of the workspace', async () => {
  const v = mount(160, 40, {overview: {...overview, path: '/home/me/.config/mini-harness/config.toml'}});
  await pause(40);
  assert.match(v.lastFrame()!, /用户配置 · 所有项目通用/);
  assert.match(v.lastFrame()!, /mini-harness\/config.toml/);
  assert.match(v.lastFrame()!, /独立凭据/);
});
