import assert from 'node:assert/strict';
import test from 'node:test';
import {MAX_FRAME_BYTES, parseServerEvent, validateClientArgs} from '../src/protocol.js';

test('accepts complete versioned events and unfinished results', () => {
  assert.equal(parseServerEvent('{"v":1,"type":"ready","id":null,"workspace":"/tmp","model":"fake","session_id":"s","capabilities":[]}').type, 'ready');
  assert.equal(parseServerEvent(JSON.stringify({v:1,type:'done',id:'run-1',result:{text:'',steps:1,status:'truncated',usage:{input_tokens:1},cost_usd:0,run_id:'run-1',stop_reason:'length'}})).type, 'done');
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
  for (const args of [['--eval'],['--stdio'],['--workspace'],['--unknown'],['--provider','--fake'],['--max-steps','bad'],['--max-steps','-1']]) {
    assert.throws(() => validateClientArgs(args));
  }
});
