import assert from 'node:assert/strict';
import test from 'node:test';
import {MAX_FRAME_BYTES, parseServerEvent, validateClientArgs, validatePlanArgs, validateTraceArgs, validatePriorityArgs} from '../src/protocol.js';
import {contextSnapshot, loadedContextSnapshot} from './context-fixture.js';

const reviewTarget = {session_id: 'session-1', plan_task_id: 't-20261108-000000-abcdef',
  plan_id: 'plan-1', plan_version: 1, plan_hash: 'a'.repeat(64)};
const reviewFrame = {v: 1, type: 'plan_review', id: 'review-1', target: reviewTarget,
  outcome: 'ready', code: null, message: '计划已读取', replacement_target: null, launch: null,
  summary: {objective: '修复输入', status: 'proposed', executable: false, step_count: 2, criterion_count: 1, question_count: 0},
  body_page: '计划正文', next_cursor: null, available_actions: ['approve-execute', 'approve-only', 'dismiss']};

test('revision protocol binds feedback, observed segment and returned plan version', () => {
  for (const action of ['request-changes', 'query-revision', 'resume-revision']) {
    validatePlanArgs({action, target: reviewTarget, feedback: '检查空文件',
      ...(action === 'resume-revision' ? {expected_segment: 1} : {})});
  }
  const task = {task_id: 't-20261108-010000-abcdef', run_id: 'revise', segment: 1, status: 'running', run_started: true};
  const frame = {v: 1, type: 'plan_revision', id: 'revise', target: reviewTarget, feedback: '检查空文件',
    revision_id: 'b'.repeat(64), outcome: 'running', code: null, message: '修订中', task, result_target: null};
  assert.equal(parseServerEvent(JSON.stringify(frame)).type, 'plan_revision');
  const output = {...reviewTarget, plan_version: 2, plan_hash: 'c'.repeat(64)};
  const finished = {...frame, outcome: 'submitted', task: {...task, status: 'completed', run_started: false}, result_target: output};
  assert.equal(parseServerEvent(JSON.stringify(finished)).type, 'plan_revision');
  assert.equal(parseServerEvent(JSON.stringify({...frame, outcome: 'incomplete', code: 'revision_not_submitted',
    task: {...task, status: 'paused', run_started: false}})).type, 'plan_revision');
  for (const args of [
    {action: 'query-revision', feedback: '检查'},
    {action: 'request-changes', target: reviewTarget, feedback: '字'.repeat(4001)},
    {action: 'resume-revision', target: reviewTarget, feedback: '检查', expected_segment: true},
    {action: 'resume-revision', target: reviewTarget, feedback: '检查'},
  ]) assert.throws(() => validatePlanArgs(args));
  for (const invalid of [
    {...frame, id: 'wrong-run'}, {...frame, task: {...task, segment: 0}},
    {...finished, result_target: {...output, plan_version: 3}},
    {...finished, result_target: {...output, plan_task_id: 't-20261108-020000-abcdef'}},
    {...finished, result_target: null}, {...frame, outcome: 'absent'},
  ]) assert.throws(() => parseServerEvent(JSON.stringify(invalid)));
});

test('revision history protocol accepts only bounded read-only records from the displayed plan family', () => {
  validatePlanArgs({action: 'list-revisions', target: reviewTarget});
  validatePlanArgs({action: 'list-revisions', target: reviewTarget, cursor: 'b'.repeat(64)});
  const record = {target: reviewTarget, feedback: '找回反馈', revision_id: 'b'.repeat(64), outcome: 'paused',
    code: null, message: '已暂停', result_target: null,
    task: {task_id: 't-20261108-010000-abcdef', run_id: 'original-run', segment: 1, status: 'paused', run_started: false}};
  const frame = {v: 1, type: 'plan_revision_history', id: 'history', target: {...reviewTarget, plan_version: 2},
    records: [record], next_cursor: null, code: null, message: '已读取修订记录'};
  assert.equal(parseServerEvent(JSON.stringify(frame)).type, 'plan_revision_history');
  for (const invalid of [
    {...frame, records: [{...record, target: {...reviewTarget, plan_id: 'other'}}]},
    {...frame, records: [{...record, task: {...record.task, status: 'running', run_id: 'history', run_started: true}, outcome: 'running'}]},
    {...frame, records: Array(11).fill(record)}, {...frame, records: [record, record]},
    {...frame, next_cursor: '../escape'}, {...frame, code: 'error'},
  ]) assert.throws(() => parseServerEvent(JSON.stringify(invalid)));
  for (const args of [{action: 'list-revisions'}, {action: 'list-revisions', target: reviewTarget, feedback: 'x'},
    {action: 'list-revisions', target: reviewTarget, cursor: '../escape'}]) assert.throws(() => validatePlanArgs(args));
});

test('plan review protocol binds actions and distinguishes pending from launched', () => {
  for (const args of [{action: 'review'}, {action: 'review', target: reviewTarget, cursor: 0},
    {action: 'approve-execute', target: reviewTarget}, {action: 'approve', target: reviewTarget},
    {action: 'dismiss', target: reviewTarget}]) {
    assert.doesNotThrow(() => validatePlanArgs(args));
  }
  assert.equal(parseServerEvent(JSON.stringify(reviewFrame)).type, 'plan_review');
  const pending = {...reviewFrame, outcome: 'approved_not_started', launch: {
    task_id: 't-20261108-000001-fedcba', run_id: null, segment: 0, status: 'reserved', run_started: false}};
  assert.equal(parseServerEvent(JSON.stringify(pending)).type, 'plan_review');
  const started = {...reviewFrame, outcome: 'launched', available_actions: [], launch: {
    task_id: 't-20261108-000001-fedcba', run_id: 'review-1', segment: 1, status: 'running', run_started: true}};
  assert.equal(parseServerEvent(JSON.stringify(started)).type, 'plan_review');
  assert.equal(parseServerEvent(JSON.stringify({...started, id: 'reopened',
    launch: {...started.launch, run_started: false}})).type, 'plan_review');
  for (const args of [{action: 'approve-execute'}, {action: 'review', target: null},
    {action: 'review', cursor: -1}, {action: 'review', cursor: 1},
    {action: 'approve', target: reviewTarget, cursor: 0}, {action: 'reject', target: reviewTarget},
    {action: 'review', target: {...reviewTarget, plan_version: true}},
    {action: 'review', target: {...reviewTarget, plan_hash: 'bad'}}]) {
    assert.throws(() => validatePlanArgs(args), JSON.stringify(args));
  }
  for (const frame of [
    {...reviewFrame, target: null}, {...reviewFrame, launch: started.launch},
    {...reviewFrame, next_cursor: -1}, {...reviewFrame, available_actions: ['execute']},
    {...pending, launch: {...pending.launch, run_started: true}},
    {...started, launch: {...started.launch, run_id: null}},
    {...started, launch: {...started.launch, segment: 0}},
    {...started, id: 'another-request'},
    {...started, outcome: 'unavailable', code: 'busy'},
  ]) assert.throws(() => parseServerEvent(JSON.stringify(frame)), JSON.stringify(frame));
});

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


test('client args accept sandbox flags, with --sandbox-network implying sandbox', () => {
  assert.deepEqual(validateClientArgs(['--sandbox']), ['--sandbox']);
  assert.deepEqual(validateClientArgs(['--sandbox-network']), ['--sandbox-network']);
  assert.deepEqual(validateClientArgs(['--sandbox', '--sandbox-network', '--workspace', '/tmp/ws']),
    ['--sandbox', '--sandbox-network', '--workspace', '/tmp/ws']);
});


test('client args accept a sandbox network allowlist with a value', () => {
  assert.deepEqual(validateClientArgs(['--sandbox-network-allow', 'registry.npmjs.org,*.pypi.org']),
    ['--sandbox-network-allow', 'registry.npmjs.org,*.pypi.org']);
});



test('ready and skills command results carry an optional structured catalog', () => {
  const ready = {v: 1, type: 'ready', id: null, workspace: '/repo', model: 'fake', session_id: 's', capabilities: []};
  const skills = [{name: 'ponytail', summary: '精简实现', active: false, source: '/repo/.vibe/skills/ponytail/SKILL.md'}];
  assert.equal(parseServerEvent(JSON.stringify({...ready, skills})).type, 'ready');
  assert.throws(() => parseServerEvent(JSON.stringify({...ready, skills: [{name: '', summary: 'x', active: false}]})));
  assert.throws(() => parseServerEvent(JSON.stringify({...ready, skills: [{name: 'x', summary: 'x', active: 'no'}]})));

  const result = {v: 1, type: 'command_result', id: 'c1', command: 'skills', text: 'skills: 1 registered'};
  assert.equal(parseServerEvent(JSON.stringify({...result, skills})).type, 'command_result');
  assert.equal(parseServerEvent(JSON.stringify({...result, skills: undefined})).type, 'command_result');
});

test('accepts autonomy ready/command payloads and verified completion summaries', () => {
  const ready = {v: 1, type: 'ready', id: null, workspace: '/repo', model: 'm', session_id: 's', capabilities: [], autonomy: 'edits', autonomy_label: '自动改文件'};
  assert.equal(Reflect.get(parseServerEvent(JSON.stringify(ready)), 'autonomy'), 'edits');
  assert.equal(parseServerEvent(JSON.stringify({...ready, autonomy: undefined, autonomy_label: undefined})).type, 'ready');
  for (const autonomy of ['ask', 'edits', 'workspace', 'full']) {
    assert.equal(parseServerEvent(JSON.stringify({v: 1, type: 'command_result', id: 'c', command: 'autonomy', text: 'ok', autonomy})).type, 'command_result');
  }
  for (const autonomy of ['auto', '', 1, null]) {
    assert.throws(() => parseServerEvent(JSON.stringify({...ready, autonomy})));
  }

  const done = {v: 1, type: 'done', id: 'r', result: {
    text: '', steps: 1, status: 'done', usage: {}, cost_usd: 0, run_id: 'r', stop_reason: '', verified: false,
    completion: {allowed: false, codes: ['no_verification'], messages: ['有文件改动，但没有记录到验证命令']},
    audit: {status: 'incomplete', integrity: 'clean', verified: false, changed_files: 1, coverage: 'full', unattributed: [], findings: []},
  }};
  assert.equal(parseServerEvent(JSON.stringify(done)).type, 'done');
  assert.equal(parseServerEvent(JSON.stringify({...done, result: {...done.result, verified: true, completion: null, audit: null}})).type, 'done');
  for (const change of [
    {verified: 'no'},
    {completion: {allowed: false, codes: [1], messages: []}},
    {audit: {status: 'incomplete', integrity: 'clean', verified: 'yes'}},
    {audit: {status: 'incomplete', integrity: 'clean', verified: false, changed_files: -1}},
  ]) {
    assert.throws(() => parseServerEvent(JSON.stringify({...done, result: {...done.result, ...change}})), JSON.stringify(change));
  }
});

test('CLI accepts --autonomy and rejects missing or invalid levels', () => {
  assert.deepEqual(validateClientArgs(['--workspace', '/repo', '--autonomy', 'edits']), ['--workspace', '/repo', '--autonomy', 'edits']);
  for (const level of ['ask', 'edits', 'workspace', 'full']) {
    assert.deepEqual(validateClientArgs(['--autonomy', level]), ['--autonomy', level]);
  }
  for (const bad of ['auto', 'edits x', '']) {
    assert.throws(() => validateClientArgs(['--autonomy', bad]));
  }
  assert.throws(() => validateClientArgs(['--autonomy']));
  assert.throws(() => validateClientArgs(['--autonomy', '--fake']));
});

test('approval requests may carry structured tier and matched rule context', () => {
  const base = {v: 1, type: 'approval_request', id: 'r', approval_id: 'a',
    call: {id: null, name: 'write_file', args: {path: 'a.txt'}}, preview: 'diff', decisions: ['once', 'deny']};
  const enriched = {...base, tier: 'write', tier_label: 'T1 工作区写入',
    rule: {source: 'project', tool: 'write_file', decision: 'once', note: '项目要求逐次', scopes: ['once']}};
  assert.equal(parseServerEvent(JSON.stringify(enriched)).type, 'approval_request');
  assert.equal(parseServerEvent(JSON.stringify(base)).type, 'approval_request');
  for (const change of [
    {tier: 1}, {tier_label: 2}, {rule: 'write_file'},
    {rule: {source: 'project'}}, {rule: {source: 'project', tool: 'x', scopes: ['once', 1]}},
  ]) {
    assert.throws(() => parseServerEvent(JSON.stringify({...enriched, ...change})), JSON.stringify(change));
  }
});
test('accepts task_state snapshots only when well-formed', () => {
  const task = {task_id: 't-20261103-120000-abcdef', kind: 'goal', status: 'running', segment: 1,
    objective: '目标', goal_id: null, updated_at: '2026-11-03T12:00:00+00:00'};
  const frame = (change: Record<string, unknown> = {}) => JSON.stringify({v: 1, type: 'task_state', id: 'run-1', task: {...task, ...change}});
  assert.equal(parseServerEvent(frame()).type, 'task_state');
  const withMode = parseServerEvent(frame({mode: 'chat'}));
  assert.ok(withMode.type === 'task_state' && withMode.task.mode === 'chat');
  for (const change of [
    {task_id: ''}, {kind: 1}, {status: null}, {segment: -1}, {segment: 1.5},
    {objective: null}, {goal_id: 1}, {updated_at: null}, {mode: 1},
  ]) {
    assert.throws(() => parseServerEvent(frame(change)));
  }
  assert.throws(() => parseServerEvent(JSON.stringify({v: 1, type: 'task_state', id: null, task})));
});
test('accepts optional task hints on ready', () => {
  const base = {v: 1, type: 'ready', id: null, workspace: '/repo', model: 'fake', session_id: 's',
    capabilities: ['tasks']};
  assert.equal(parseServerEvent(JSON.stringify({...base, tasks: [
    {task_id: 't-20261103-120000-abcdef', kind: 'goal', status: 'paused', worker_status: null, stale: false},
  ]})).type, 'ready');
  const hint = {task_id: 't-20261103-120000-abcdef', kind: 'goal', status: 'paused', worker_status: null, stale: false};
  assert.equal(parseServerEvent(JSON.stringify({...base, tasks: [{...hint, pending_approvals: 2}]})).type, 'ready');
  for (const change of [{pending_approvals: -1}, {pending_approvals: 1.5}, {pending_approvals: '2'}]) {
    assert.throws(() => parseServerEvent(JSON.stringify({...base, tasks: [{...hint, ...change}]})));
  }
  assert.equal(parseServerEvent(JSON.stringify({...base, tasks: [{...hint, priority: 5}]})).type, 'ready');
  for (const change of [{priority: -1}, {priority: 101}, {priority: 1.5}, {priority: '5'}]) {
    assert.throws(() => parseServerEvent(JSON.stringify({...base, tasks: [{...hint, ...change}]})));
  }
  assert.throws(() => parseServerEvent(JSON.stringify({...base, tasks: [{task_id: 'x'}]})));
});
test('accepts attach task_snapshot with approvals', () => {
  const frame = {v: 1, type: 'task_snapshot', id: null,
    task: {task_id: 't-20261103-120000-abcdef', kind: 'goal', status: 'running', segment: 1, run_id: 'worker-1',
      objective: '目标', goal_id: null,
      worktree: {path: '/tmp/wt', branch: 'vibe/s-t', base_commit: 'abc'}},
    approvals: [{approval_id: 'a-1', tool: 'bash', tier: 'exec', requester: 'parent', expires_at: null}]};
  assert.equal(parseServerEvent(JSON.stringify(frame)).type, 'task_snapshot');
  assert.throws(() => parseServerEvent(JSON.stringify({...frame, approvals: null})));
});

test('accepts subagent_event with child task id', () => {
  const event = parseServerEvent(JSON.stringify({
    v: 1, type: 'subagent_event', id: null,
    task_id: 't-20261103-120000-abcdef', status: 'ok', summary: 'done',
  }));
  assert.equal(event.type, 'subagent_event');
  assert.throws(() => parseServerEvent(JSON.stringify({
    v: 1, type: 'subagent_event', id: null, task_id: '', status: 'ok',
  })));
});


test('validateTraceArgs accepts bounded hours and rejects others', () => {
  assert.doesNotThrow(() => validateTraceArgs({}));
  assert.doesNotThrow(() => validateTraceArgs({hours: 1}));
  assert.doesNotThrow(() => validateTraceArgs({hours: 8760}));
  for (const bad of [{hours: 0}, {hours: -1}, {hours: 8761}, {hours: '24'}, {kind: 'x'}]) {
    assert.throws(() => validateTraceArgs(bad), /trace 参数/);
  }
});


test('validatePriorityArgs accepts valid payload and rejects others', () => {
  assert.doesNotThrow(() => validatePriorityArgs({task_id: 't-1', priority: 9}));
  for (const bad of [{task_id: '', priority: 9}, {task_id: 't-1', priority: 101},
    {task_id: 't-1', priority: -1}, {task_id: 't-1', priority: 1.5},
    {task_id: 't-1', priority: '9'}, {priority: 9}, {task_id: 't-1', priority: 9, extra: 1}]) {
    assert.throws(() => validatePriorityArgs(bad), /priority 参数/);
  }
});


test('accepts plan_mode frames and rejects malformed mode state', () => {
  const frame = (mode: unknown = {active: true, pending: false, wanted: true}) => JSON.stringify({v: 1, type: 'plan_mode', id: null, mode});
  assert.equal(parseServerEvent(frame()).type, 'plan_mode');
  const event = parseServerEvent(frame({active: false, pending: true, wanted: null}));
  assert.deepEqual(event.type === 'plan_mode' && event.mode, {active: false, pending: true, wanted: null});
  for (const [mode, id] of [
    [{active: true, pending: false}, null],
    [{active: true, pending: false, wanted: 'yes'}, null],
    [{active: 'true', pending: false, wanted: true}, null],
    [{active: true, pending: false, wanted: true, extra: 1}, null],
    [{active: true, pending: false, wanted: true}, 'run-1'],
    [null, null],
  ] as const) {
    assert.throws(() => parseServerEvent(JSON.stringify({v: 1, type: 'plan_mode', id, mode})));
  }
});

test('validatePlanArgs accepts mode and review actions with non-empty feedback only', () => {
  for (const args of [
    {}, {action: 'list'}, {action: 'mode-on'}, {action: 'mode-off'}, {action: 'mode-status'},
    {action: 'dismiss'}, {action: 'markdown'}, {action: 'request-changes', feedback: '补充失败路径测试'},
  ]) assert.doesNotThrow(() => validatePlanArgs(args));
  for (const bad of [
    {action: 'mode-toggle'}, {action: 'request-changes'},
    {action: 'request-changes', feedback: ''}, {action: 'request-changes', feedback: '   '},
    {action: 'request-changes', feedback: 1}, {action: 'markdown', feedback: '多余'},
    {action: 'list', feedback: 'x'}, {action: 'markdown', extra: true},
  ]) assert.throws(() => validatePlanArgs(bad), /plan 参数/);
});
