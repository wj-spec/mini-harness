import assert from 'node:assert/strict';
import test from 'node:test';
import {MAX_FRAME_BYTES, parseServerEvent, validateClientArgs} from '../src/protocol.js';
import {contextSnapshot, loadedContextSnapshot} from './context-fixture.js';

test('accepts startup context independently of model requests and only zero startup usage', () => {
  const snapshot = loadedContextSnapshot();
  const frame = {v: 1, type: 'context_snapshot', id: null, snapshot};
  assert.equal(parseServerEvent(JSON.stringify(frame)).type, 'context_snapshot');
  for (const change of [{step: 1}, {attempt: 1}, {reason: 'model_call'}]) {
    assert.throws(() => parseServerEvent(JSON.stringify({...frame, snapshot: {...snapshot, ...change}})));
  }
  assert.throws(() => parseServerEvent(JSON.stringify({...frame, id: 'run-1'})));
  const metrics = {v: 1, type: 'metrics', id: null, used_tokens: 0, limit_tokens: 1000, estimated: true,
    context_tokens: 50, context_window: 1000, done: 0, planned: 0, cost_usd: 0};
  assert.equal(parseServerEvent(JSON.stringify(metrics)).type, 'metrics');
  assert.equal(parseServerEvent(JSON.stringify({...metrics, estimated: false})).type, 'metrics');
  for (const progress of [{done: 1, planned: 2}, {done: 7, planned: 6}, {done: 0, planned: 6}]) {
    assert.equal(parseServerEvent(JSON.stringify({...metrics, ...progress})).type, 'metrics');
  }
  for (const change of [{used_tokens: 1}, {done: -1}, {planned: -1}, {done: 0.5},
    {planned: '6'}, {cost_usd: 0.01}, {context_tokens: null}]) {
    assert.throws(() => parseServerEvent(JSON.stringify({...metrics, ...change})));
  }
});

test('validates estimated context snapshots, partition sums, and block accounting', () => {
  const snapshot = contextSnapshot();
  const frame = (change: Record<string, unknown> = {}) => JSON.stringify({v: 1, type: 'context_snapshot', id: 'run-1', snapshot: {...snapshot, ...change}});
  assert.equal(parseServerEvent(frame()).type, 'context_snapshot');
  assert.equal(parseServerEvent(frame({delta_tokens: -50, section_deltas: {system: 0, memory: 0, tools: 0, history: -50}})).type, 'context_snapshot');
  for (const change of [
    {schema_version: 2}, {snapshot_id: ''}, {request_id: null}, {run_id: 42}, {model: null}, {ts: -1}, {ts: null},
    {step: -1}, {attempt: 0.1}, {phase: 'built'}, {reason: null}, {estimated: false}, {count_method: 'unknown'},
    {context_window: -1}, {input_capacity: Number.MAX_SAFE_INTEGER + 1}, {total_tokens: 99},
    {sections: {system: 20, memory: 0, tools: 30, history: 49}},
    {sections: {...snapshot.sections, extra: 1}}, {sections: {...snapshot.sections, memory: -1}},
    {caps: {...snapshot.caps, history: 0.5}}, {reserves: {output: -1, scratch: 0}}, {remaining_tokens: -1},
    {over_budget: 'no'}, {delta_tokens: 0.5}, {section_deltas: {system: 0, memory: 0, tools: 0}},
    {blocks: []}, {blocks: [{...snapshot.blocks[0], section: 'output'}]},
    {blocks: snapshot.blocks.map((block, index) => index === 0 ? {...block, tokens: 19} : block)},
    {blocks: snapshot.blocks.map((block, index) => index === 0 ? {...block, chars: -1} : block)},
    {blocks: snapshot.blocks.map((block, index) => index === 0 ? {...block, source: null} : block)},
    {blocks: snapshot.blocks.map((block, index) => index === 0 ? {...block, content_hash: 12} : block)},
    {blocks: [...snapshot.blocks, {...snapshot.blocks[0], tokens: 0}]},
  ]) assert.throws(() => parseServerEvent(frame(change)), JSON.stringify(change));
  assert.throws(() => parseServerEvent(JSON.stringify({v: 1, type: 'context_snapshot', id: null, snapshot})));
});

test('accepts complete versioned events and unfinished results', () => {
  assert.equal(parseServerEvent('{"v":1,"type":"ready","id":null,"workspace":"/tmp","model":"fake","session_id":"s","capabilities":[]}').type, 'ready');
  assert.equal(parseServerEvent(JSON.stringify({v:1,type:'done',id:'run-1',result:{text:'',steps:1,status:'truncated',usage:{input_tokens:1},cost_usd:0,run_id:'run-1',stop_reason:'length'}})).type, 'done');
});

test('ready preserves an explicit session purpose while accepting older backends', () => {
  const ready = {v: 1, type: 'ready', id: null, workspace: '/repo', model: 'fake', session_id: 's', capabilities: []};
  for (const purpose of ['user', 'test']) {
    assert.equal(Reflect.get(parseServerEvent(JSON.stringify({...ready, session_purpose: purpose})), 'session_purpose'), purpose);
  }
  assert.equal(parseServerEvent(JSON.stringify(ready)).type, 'ready');
  for (const purpose of ['all', '', null, false, 1]) {
    assert.throws(() => parseServerEvent(JSON.stringify({...ready, session_purpose: purpose})));
  }
});

test('accepts real progress metrics and rejects invalid numeric values', () => {
  const metrics = {v: 1, type: 'metrics', id: 'r', used_tokens: 12, limit_tokens: 1000, estimated: true, context_tokens: null, context_window: 1000, done: 1, planned: 2, cost_usd: 0};
  assert.equal(parseServerEvent(JSON.stringify(metrics)).type, 'metrics');
  for (const change of [{used_tokens: -1}, {estimated: 'true'}, {context_tokens: 'unknown'}, {planned: -1}, {cost_usd: null}]) {
    assert.throws(() => parseServerEvent(JSON.stringify({...metrics, ...change})));
  }
  assert.equal(parseServerEvent(JSON.stringify({v: 1, type: 'ready', id: null, workspace: '/repo', model: 'm', session_id: 's', capabilities: [], provider: 'fake', session_path: '/repo/.vibe/sessions/s'})).type, 'ready');
});

test('rejects invalid JSON, protocol versions and incomplete payloads', () => {
  for (const value of ['broken', '{"v":2,"type":"closed","id":null}', '{"v":1,"type":"text_delta","id":"r"}', '{"v":1,"type":"unknown","id":null}', '{"v":1,"type":"status","id":"r","phase":"approved"}', '{"v":1,"type":"tool_result","id":"r","call":{"id":null,"name":"bash","args":[]},"ok":true,"output":""}']) {
    assert.throws(() => parseServerEvent(value));
  }
  assert.throws(() => parseServerEvent(' '.repeat(MAX_FRAME_BYTES + 1)), /large|大小|上限/i);
});

test('frame limit counts UTF-8 payload bytes without the line delimiter', () => {
  const base = {v: 1, type: 'text_delta', id: 'r', delta: ''};
  const remaining = MAX_FRAME_BYTES - Buffer.byteLength(JSON.stringify(base), 'utf8');
  const exact = JSON.stringify({...base, delta: 'x'.repeat(remaining)});
  assert.equal(Buffer.byteLength(exact, 'utf8'), MAX_FRAME_BYTES);
  assert.equal(parseServerEvent(exact).type, 'text_delta');
  assert.throws(() => parseServerEvent(JSON.stringify({...base, delta: 'x'.repeat(remaining + 1)})), /上限/);
});

test('restores typed history and accepts explicit backend commands and startup state', () => {
  const events = [
    {v:1,type:'ready',id:null,workspace:'/repo',model:'fake',session_id:'s',capabilities:[],resumed:true},
    {v:1,type:'history_message',id:null,message:{role:'assistant',content:'读取文件',tool_calls:[{id:'c1',name:'read_file',args:{path:'a.ts'}}],usage:null,finish_reason:null,name:null,tool_call_id:null}},
    {v:1,type:'history_message',id:null,message:{role:'tool',content:'hello',name:'read_file',tool_call_id:'c1'}},
    {v:1,type:'history_message',id:null,message:{role:'tool',content:'failed',name:'bash',tool_call_id:'c2',execution:{status:'failed',exit_code:1}}},
    {v:1,type:'status',id:'startup',phase:'starting'},
    {v:1,type:'notice',id:null,message:'会话已恢复；输入 /resume 继续'},
    {v:1,type:'command_result',id:'c',command:'permissions',text:'无授权规则'},
    {v:1,type:'command_result',id:'s',command:'skills',text:'技能已激活'},
  ];
  for (const event of events) assert.equal(parseServerEvent(JSON.stringify(event)).type,event.type);
  for (const event of [
    {...events[0],resumed:'yes'}, {...events[1],id:'run'},
    {...events[1],message:{role:'root',content:''}}, {...events[1],message:{role:'user',content:[]}},
    {...events[1],message:{role:'assistant',content:'',tool_calls:[{id:'c1',name:'read_file',args:[]}] }},
    {...events[3],message:{role:'tool',content:'failed',execution:{status:'succeeded',exit_code:2}}},
    {...events[4],id:null}, {...events[5],id:'run'},
    {...events[6],id:null}, {...events[6],command:'run'}, {...events[6],text:undefined},
  ]) assert.throws(() => parseServerEvent(JSON.stringify(event)));
});

test('approval capabilities list only supported decisions and includes deny', () => {
  const event = {v:1,type:'approval_request',id:'startup',approval_id:'a',call:{id:null,name:'mcp_start',args:{}},preview:'启动本地服务',decisions:['once','session','deny']};
  assert.equal(parseServerEvent(JSON.stringify(event)).type,'approval_request');
  assert.equal(parseServerEvent(JSON.stringify({...event,decisions:['once','session','workspace','deny']})).type,'approval_request');
  assert.equal(parseServerEvent(JSON.stringify({...event,decisions:undefined})).type,'approval_request');
  for (const decisions of [[],['always','deny'],['once'],['deny','deny'],'once']) {
    assert.throws(() => parseServerEvent(JSON.stringify({...event,decisions})));
  }
  for (const decision of ['once','session','workspace','deny']) {
    assert.equal(parseServerEvent(JSON.stringify({v:1,type:'approval_resolved',id:'r',approval_id:'a',decision})).type,'approval_resolved');
  }
});

test('model tool IDs retain empty and long strings across live events and restored history', () => {
  for (const id of ['', 'model-call-'.repeat(40)]) {
    const call = {id,name:'read_file',args:{path:'README.md'}};
    for (const event of [
      {v:1,type:'tool_call',id:'r',call},
      {v:1,type:'tool_result',id:'r',call,ok:true,output:'body'},
      {v:1,type:'history_message',id:null,message:{role:'assistant',content:'',tool_calls:[call]}},
      {v:1,type:'history_message',id:null,message:{role:'tool',content:'body',name:call.name,tool_call_id:id}},
    ]) assert.equal(parseServerEvent(JSON.stringify(event)).type,event.type);
  }
  assert.throws(() => parseServerEvent(JSON.stringify({v:1,type:'tool_call',id:'r',call:{id:42,name:'read_file',args:{}}})));
  assert.throws(() => parseServerEvent(JSON.stringify({v:1,type:'tool_call',id:'r'.repeat(129),call:{id:null,name:'read_file',args:{}}})));
});

test('command execution facts validate outcomes without inferring success from output', () => {
  const event = {v:1,type:'tool_result',id:'r',call:{id:'c',name:'bash',args:{command:'false'}},ok:false,output:'tests passed',execution:{status:'failed',exit_code:1}};
  for (const execution of [{status:'succeeded',exit_code:0},{status:'failed',exit_code:2},{status:'timed_out',exit_code:-9},{status:'cancelled',exit_code:null},{status:'unknown',exit_code:null}]) {
    assert.equal(parseServerEvent(JSON.stringify({...event,execution})).type,'tool_result');
  }
  for (const execution of [{status:'succeeded',exit_code:1},{status:'failed',exit_code:0},{status:'failed',exit_code:null},{status:'ok',exit_code:0},{status:'cancelled',exit_code:0.5},{status:'unknown',exit_code:1},{status:'unknown'}]) {
    assert.throws(() => parseServerEvent(JSON.stringify({...event,execution})));
  }
});

test('only forwards supported migration arguments as distinct values', () => {
  assert.deepEqual(validateClientArgs(['--fake','--workspace','/tmp/a b','--model','x; echo bad','--chat-only']), ['--fake','--workspace','/tmp/a b','--model','x; echo bad','--chat-only']);
  assert.deepEqual(validateClientArgs(['--max-steps','0']),['--max-steps','0']);
  assert.deepEqual(validateClientArgs(['--continue','--fake']),['--continue','--fake']);
  assert.deepEqual(validateClientArgs(['--session','20261021-112233-123456','--workspace','/tmp/a b']),['--session','20261021-112233-123456','--workspace','/tmp/a b']);
  assert.deepEqual(validateClientArgs(['--list-sessions','--workspace','/tmp/a b']),['--list-sessions','--workspace','/tmp/a b']);
  for (const args of [['--test-session', '--continue'], ['--test-session', '--list-sessions'], ['--session', 'saved-test', '--test-session']]) {
    assert.deepEqual(validateClientArgs(args), args);
  }
  for (const args of [['--eval'],['--stdio'],['--workspace'],['--unknown'],['--provider','--fake'],['--max-steps','bad'],['--max-steps','-1']]) {
    assert.throws(() => validateClientArgs(args));
  }
});

test('session selection and listing are mutually exclusive before backend startup', () => {
  for (const args of [
    ['--continue','--session','saved'], ['--session','saved','--continue'],
    ['--list-sessions','--continue'], ['--session','saved','--list-sessions'],
    ['--list-sessions','--session','saved'], ['--session'], ['--session',''],
  ]) assert.throws(() => validateClientArgs(args));
});
