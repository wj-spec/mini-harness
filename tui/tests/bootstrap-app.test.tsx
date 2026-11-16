import assert from 'node:assert/strict';
import {afterEach, test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup, render} from 'ink-testing-library';
import {App} from '../src/app.js';
import {initialState} from '../src/view-state.js';
import type {BackendClient} from '../src/backend.js';
import type {ServerEvent, CommandArgs} from '../src/protocol.js';
afterEach(cleanup);

const overview = {model: 'deepseek-chat', base_url: 'https://api.deepseek.com/v1', provider: 'openai',
  trust_env: false, key_present: false,
  sources: {model: '默认值', base_url: '默认值', api_key: '默认值', trust_env: '默认值', provider: '默认值'},
  path: '/project/.env', revision: 'a'.repeat(64), saved_model: '', applied: false, message: '',
  workspace: '/project', session_id: ''};

class BootstrapClient implements BackendClient {
  listeners = new Set<(event: ServerEvent) => void>();
  commands: {name: string; args?: CommandArgs}[] = [];
  approvals: {id: string; decision: string}[] = [];
  runs: string[] = [];
  override: Record<string, (id: string) => unknown> = {};
  capabilities() {return ['config-bootstrap', 'model-settings', 'commands', 'session_recording'];}
  start() {}
  subscribe(fn: (event: ServerEvent) => void) {this.listeners.add(fn); return () => {this.listeners.delete(fn);};}
  emit(event: unknown) {this.listeners.forEach(fn => fn(event as ServerEvent));}
  command(name: string, args?: CommandArgs) {
    this.commands.push({name, args});
    const id = 'c' + this.commands.length;
    setTimeout(() => {
      const custom = this.override[name];
      if (custom) custom(id);
      else if (name === 'model' && args && 'action' in args && args.action === 'list') {
        this.emit({v: 1, type: 'model_settings', id, ...overview});
      }
    }, 10);
    return id;
  }
  run(text: string) {this.runs.push(text); return 'r';}
  cancel() {return 'cancel';}
  approve(id: string, decision: string) {this.approvals.push({id, decision}); return 'approve';}
  steer() {return 'steer';}
  async shutdown() {}
}

async function mount() {
  const client = new BootstrapClient();
  const v = render(<App client={client} initialState={{...initialState, phase: 'idle', workspace: '/project'}}
    windowSize={{columns: 80, rows: 24}} publishSidebarSignal={() => {}}/>);
  const key = async (s: string) => {v.stdin.write(s); await pause(45);};
  await pause(40);
  client.emit({v: 1, type: 'ready', id: null, workspace: '/project', model: 'deepseek-chat',
    session_id: '', capabilities: ['config-bootstrap', 'model-settings', 'commands'],
    session_purpose: 'user', resumed: false, provider: 'openai',
    bootstrap: {configured: false, reason: 'missing_key', config_path: '/project/.env', problem: '',
      defaults: {base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat'}}});
  client.emit({v: 1, type: 'status', id: null, phase: 'idle'});
  await pause(80);
  return {v, client, key};
}

test('bootstrap ready renders the first-run page and asks for the model overview', async () => {
  const {v, client} = await mount();
  assert.match(v.lastFrame()!, /陪你完成第一个编码任务/);
  assert.match(v.lastFrame()!, /1 连接模型/);
  assert.deepEqual(client.commands.map(item => item.name), ['model']);
  assert.deepEqual(client.runs, []);
});

test('typing on the first-run page never submits a task', async () => {
  const {v, client, key} = await mount();
  await key('帮我改一下'); await key('\r');
  assert.deepEqual(client.runs, []);
  assert.match(v.lastFrame()!, /陪你完成第一个编码任务/);
});

test('bootstrap approvals use the normal approval surface', async () => {
  const {v, client, key} = await mount();
  client.emit({v: 1, type: 'approval_request', id: 'bootstrap', approval_id: 'a1',
    call: {name: 'write_file', args: {path: '/project/.env', keys: ['VIBE_API_KEY']}, id: null},
    preview: '保存模型配置到 /project/.env；Key 隐藏。', decisions: ['once', 'deny']});
  await pause(60);
  assert.match(v.lastFrame()!, /允许本次/);
  assert.doesNotMatch(v.lastFrame()!, /sk-secret/);
  await key('y');
  assert.deepEqual(client.approvals, [{id: 'a1', decision: 'once'}]);
});

test('entering the project is gated on detection and waits for a new ready', async () => {
  const {v, client, key} = await mount();
  client.emit({v: 1, type: 'model_settings', id: 'c1', ...overview,
    message: '连接检测通过；只验证最小请求，不代表工具调用或模型能力完整支持。', key_present: true});
  await pause(60);
  for (let i = 0; i < 4; i += 1) await key('\t');
  await key('\r'); await key('\r');
  assert.deepEqual(client.commands.map(item => item.name), ['model', 'bootstrap']);
  assert.deepEqual(client.commands[1]!.args, {action: 'activate'});
  client.emit({v: 1, type: 'bootstrap_activation', id: 'c2', text: '配置有效，正在初始化真实运行环境…'});
  await pause(40);
  assert.match(v.lastFrame()!, /正在初始化真实运行环境/);
  client.emit({v: 1, type: 'ready', id: null, workspace: '/project', model: 'deepseek-chat',
    session_id: 's-1', capabilities: ['run', 'model-settings', 'tasks'], session_purpose: 'user',
    resumed: false, provider: 'openai'});
  client.emit({v: 1, type: 'status', id: null, phase: 'idle'});
  await pause(60);
  assert.doesNotMatch(v.lastFrame()!, /陪你完成第一个编码任务/);
  assert.match(v.lastFrame()!, /描述任务|输入任务/);
});

test('explicit demo sessions are labelled and never presented as real models', async () => {
  const client = new BootstrapClient();
  const v = render(<App client={client} initialState={{...initialState, phase: 'idle', workspace: '/project'}} windowSize={{columns: 80, rows: 24}} publishSidebarSignal={() => {}}/>);
  await pause(40);
  client.emit({v: 1, type: 'ready', id: null, workspace: '/project', model: 'fake', session_id: 's-1',
    capabilities: ['run', 'session_recording'], session_purpose: 'test', resumed: false, provider: 'fake', demo: true});
  client.emit({v: 1, type: 'status', id: null, phase: 'idle'});
  await pause(60);
  assert.match(v.lastFrame()!, /Demo · 不调用真实模型/);
});

test('choosing demo hands off to the demo backend starter', async () => {
  const client = new BootstrapClient();
  let demos = 0;
  const v = render(<App client={client} initialState={{...initialState, phase: 'idle', workspace: '/project'}}
    onDemo={() => {demos += 1;}}
    windowSize={{columns: 80, rows: 24}} publishSidebarSignal={() => {}}/>);
  const key = async (s: string) => {v.stdin.write(s); await pause(45);};
  await pause(40);
  client.emit({v: 1, type: 'ready', id: null, workspace: '/project', model: 'deepseek-chat', session_id: '',
    capabilities: ['config-bootstrap', 'model-settings'], session_purpose: 'user', resumed: false, provider: 'openai',
    bootstrap: {configured: false, reason: 'missing_key', config_path: '/project/.env', problem: '',
      defaults: {base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat'}}});
  client.emit({v: 1, type: 'status', id: null, phase: 'idle'});
  await pause(80);
  for (let i = 0; i < 7; i += 1) await key('\t');
  await key('\r');
  assert.equal(demos, 1);
});

test('later enters a draft-preserving page that cannot submit tasks', async () => {
  const {v, client, key} = await mount();
  for (let i = 0; i < 6; i += 1) await key('\t');
  await key('\r');
  assert.match(v.lastFrame()!, /尚未连接模型/);
  await key('中文草稿'); await key('\r');
  assert.deepEqual(client.runs, []);
  assert.match(v.lastFrame()!, /草稿已保留/);
  await key('\x1b');
  assert.match(v.lastFrame()!, /陪你完成第一个编码任务/);
  for (let i = 0; i < 6; i += 1) await key('\t');
  await key('\r');
  assert.match(v.lastFrame()!, /中文草稿/);
});
