import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import React from 'react';
import {render, cleanup} from 'ink-testing-library';
import {setTimeout as pause} from 'node:timers/promises';
import stringWidth from 'string-width';
import {ConfigProblemPanel} from '../src/config-problem-panel.js';
import {App} from '../src/app.js';
import {initialState} from '../src/view-state.js';
import {getPalette} from '../src/theme.js';
import type {BackendClient} from '../src/backend.js';
import type {ServerEvent, CommandArgs} from '../src/protocol.js';
afterEach(cleanup);

const problem = '配置文件无法解析：.env 第 1 行引号不完整。 原文件未修改；请修正后重新启动。';

for (const [columns, rows] of [[32, 12], [80, 24]] as const) {
  test(`config problem page fits ${columns}x${rows} without offering to overwrite`, async () => {
    const calls: string[] = [];
    const v = render(<ConfigProblemPanel workspace="/repo/project" problem={problem}
      configPath="/repo/.env" columns={columns} rows={rows} palette={getPalette('dark')}
      onQuit={() => calls.push('quit')}/>);
    const key = async (s: string) => {v.stdin.write(s); await pause(30);};
    await pause(40);
    const frame = v.lastFrame()!;
    assert.match(frame, /配置需要修复/);
    assert.match(frame, /第 1/);
    assert.match(frame, /行引号不完整/);
    assert.match(frame, /\/repo\/\.env/);
    assert.match(frame, /原文件未修改/);
    assert.doesNotMatch(frame, /保存并检测/);
    assert.doesNotMatch(frame, /进入项目/);
    assert.equal(frame.split('\n').length, rows);
    assert.ok(frame.split('\n').every(line => stringWidth(line) <= columns));
    await key('\x1b');
    assert.deepEqual(calls, ['quit']);
  });
}

class Client implements BackendClient {
  listeners = new Set<(event: ServerEvent) => void>();
  commands: {name: string; args?: CommandArgs}[] = [];
  capabilities() {return ['config-bootstrap', 'model-settings'];}
  start() {}
  subscribe(fn: (event: ServerEvent) => void) {this.listeners.add(fn); return () => {this.listeners.delete(fn);};}
  emit(event: unknown) {this.listeners.forEach(fn => fn(event as ServerEvent));}
  command(name: string, args?: CommandArgs) {this.commands.push({name, args}); return 'c1';}
  run() {return 'r';}
  cancel() {return 'cancel';}
  approve() {return 'approve';}
  steer() {return 'steer';}
  async shutdown() {}
}

test('a broken configuration enters the repair page instead of the first-run wizard', async () => {
  const client = new Client();
  const v = render(<App client={client} initialState={{...initialState, phase: 'idle' as const, workspace: '/repo/project'}}
    windowSize={{columns: 80, rows: 24}} publishSidebarSignal={() => {}}/>);
  await pause(40);
  client.emit({v: 1, type: 'ready', id: null, workspace: '/repo/project', model: 'deepseek-chat',
    session_id: '', capabilities: ['config-bootstrap', 'model-settings'], session_purpose: 'user',
    resumed: false, provider: 'openai',
    bootstrap: {configured: false, reason: 'missing_key', config_path: '/repo/.env', problem,
      defaults: {base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat'}}});
  client.emit({v: 1, type: 'status', id: null, phase: 'idle'});
  await pause(60);
  const frame = v.lastFrame()!;
  assert.match(frame, /配置需要修复/);
  assert.doesNotMatch(frame, /陪你完成第一个编码任务/);
  assert.deepEqual(client.commands, []);
});
