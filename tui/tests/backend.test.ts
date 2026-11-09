import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {chmod, mkdir, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import {createBackendClient, type BackendClient} from '../src/backend.js';
import {MAX_FRAME_BYTES, type BackendCommand, type SkillsCommandArgs, type PersistedMessage, type ServerEvent} from '../src/protocol.js';
import {initialState, reduceView} from '../src/view-state.js';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const ready = {v:1,type:'ready',id:null,workspace:'/tmp',model:'fake',session_id:'test',capabilities:[]};

async function fixture(source:string) {
  const base = path.join(repoRoot,'.vibe','node-transport-tests');
  await mkdir(base,{recursive:true});
  const root = await mkdtemp(path.join(base,'fixture-'));
  await writeFile(path.join(root,'fixture.mjs'),source);
  const launcher = path.join(root,'run_vibe.sh');
  await writeFile(launcher,`#!/bin/sh\nexec '${process.execPath.replaceAll("'", "'\\''")}' "$(dirname "$0")/fixture.mjs"\n`);
  await chmod(launcher,0o755);
  return {root, async close(){await rm(root,{recursive:true,force:true});}};
}

function observer(client:BackendClient) {
  const events:ServerEvent[]=[];
  const pending = new Set<{predicate:(event:ServerEvent)=>boolean;resolve:(event:ServerEvent)=>void;reject:(error:Error)=>void;timer:ReturnType<typeof setTimeout>}>();
  client.subscribe(event=>{events.push(event); for(const waiter of pending){if(waiter.predicate(event)){clearTimeout(waiter.timer);pending.delete(waiter);waiter.resolve(event);}}});
  return {events, wait(predicate:(event:ServerEvent)=>boolean, timeout=10000):Promise<ServerEvent>{
    const previous=events.find(predicate); if(previous)return Promise.resolve(previous);
    return new Promise((resolve,reject)=>{const waiter={predicate,resolve,reject,timer:setTimeout(()=>{pending.delete(waiter);reject(new Error(`event timeout: ${JSON.stringify(events)}`));},timeout)};pending.add(waiter);});
  }};
}

test('selected task connection waits for exact scope and replays its snapshot without closing the original backend', async () => {
  const snapshot = {v: 1, type: 'task_snapshot', id: null, task: {task_id: 't-selected', kind: 'run', status: 'running', segment: 1,
    run_id: 'selected-run', objective: '原任务', goal_id: null, mode: 'act', plan_id: null, plan_version: null, worktree: null}, approvals: []};
  const f = await fixture(`import readline from 'node:readline';
const attached = process.argv.includes('--attach');
console.log(JSON.stringify(${JSON.stringify({...ready, capabilities: ['attach', 'cancel']})}));
if (attached) setTimeout(() => console.log(JSON.stringify(${JSON.stringify(snapshot)})), 60);
readline.createInterface({input:process.stdin}).on('line', line => {
 const r=JSON.parse(line);
 if(r.type==='shutdown'){console.log(JSON.stringify({v:1,type:'closed',id:r.id}));process.exit(0);}
 console.log(JSON.stringify({v:1,type:'notice',id:null,message:JSON.stringify({request:r,args:process.argv.slice(2)})}));
});`);
  await writeFile(path.join(f.root, 'run_vibe.sh'), `#!/bin/sh\nexec '${process.execPath.replaceAll("'", "'\\''")}' "$(dirname "$0")/fixture.mjs" "$@"\n`);
  const client = createBackendClient({repoRoot: f.root, args: ['--fake', '--continue', '--workspace', '/old'], shutdownTimeoutMs: 500});
  const state = observer(client);
  try {
    client.start(); await state.wait(event => event.type === 'ready');
    const connection = client as BackendClient & {connectTask(target: {workspace: string; sessionId: string; taskId: string}, signal?: AbortSignal): Promise<BackendClient>};
    const attached = await connection.connectTask({workspace: '/tmp', sessionId: 'test', taskId: 't-selected'});
    const selected = observer(attached);
    assert.deepEqual(await selected.wait(event => event.type === 'task_snapshot'), snapshot);
    attached.cancel('selected-run');
    const receipt = await selected.wait(event => event.type === 'notice');
    assert.equal(receipt.type, 'notice');
    if (receipt.type !== 'notice') return;
    const received = JSON.parse(receipt.message);
    assert.deepEqual(received.args, ['--attach', '--task-id', 't-selected', '--session', 'test', '--mode', 'control', '--fake', '--workspace', '/tmp']);
    assert.equal(received.request.target, 'selected-run');
    assert.equal(state.events.some(event => event.type === 'closed'), false);
    await client.shutdown();
    await selected.wait(event => event.type === 'closed');
  } finally {await client.shutdown(); await f.close();}
});

test('failed or cancelled task handshakes close only the candidate process and leave the original usable', async () => {
  const f = await fixture(`import readline from 'node:readline';import {appendFileSync} from 'node:fs';
const attached=process.argv.includes('--attach');
if(attached)appendFileSync('attached-pids',String(process.pid)+String.fromCharCode(10));
if(process.argv.includes('t-denied')){console.error('attach denied: another controller is attached');process.exit(2);}
console.log(JSON.stringify(${JSON.stringify({...ready, capabilities: ['attach']})}));
if(attached && !process.argv.includes('t-slow'))console.log(JSON.stringify({v:1,type:'task_snapshot',id:null,task:{task_id:'t-actual',kind:'run',status:'paused',segment:0,run_id:null,objective:'原任务',goal_id:null,mode:'act',plan_id:null,plan_version:null,worktree:null},approvals:[]}));
readline.createInterface({input:process.stdin}).on('line',line=>{const r=JSON.parse(line);
if(r.type==='shutdown'){console.log(JSON.stringify({v:1,type:'closed',id:r.id}));process.exit(0);}
console.log(JSON.stringify({v:1,type:'notice',id:null,message:r.text||'ok'}));});`);
  await writeFile(path.join(f.root, 'run_vibe.sh'), `#!/bin/sh\nexec '${process.execPath.replaceAll("'", "'\\''")}' "$(dirname "$0")/fixture.mjs" "$@"\n`);
  const client = createBackendClient({repoRoot: f.root, shutdownTimeoutMs: 500}); const state = observer(client);
  try {
    client.start(); await state.wait(e => e.type === 'ready');
    await assert.rejects(client.connectTask!({workspace: '/foreign', sessionId: 'test', taskId: 't-actual'}), /工作区或会话不匹配/);
    await assert.rejects(client.connectTask!({workspace: '/tmp', sessionId: 'test', taskId: 't-wrong'}), /任务不匹配/);
    await assert.rejects(client.connectTask!({workspace: '/tmp', sessionId: 'test', taskId: 't-denied'}), /another controller is attached/);
    const abort = new AbortController();
    const waiting = assert.rejects(client.connectTask!({workspace: '/tmp', sessionId: 'test', taskId: 't-slow'}, abort.signal), /取消/);
    setTimeout(() => abort.abort(), 80); await waiting;
    client.run('原会话仍可用'); await state.wait(e => e.type === 'notice' && e.message === '原会话仍可用');
    assert.equal(state.events.some(e => e.type === 'closed'), false);
    for (const pid of (await readFile(path.join(f.root, 'attached-pids'), 'utf8')).trim().split('\n').map(Number)) {
      assert.throws(() => process.kill(pid, 0), {code: 'ESRCH'});
    }
  } finally {await client.shutdown(); await f.close();}
});

test('run transports a preallocated request identity unchanged', async () => {
  const f = await fixture(`import readline from 'node:readline';
console.log(JSON.stringify(${JSON.stringify(ready)}));
readline.createInterface({input:process.stdin}).on('line', line => {
  const r = JSON.parse(line);
  if (r.type === 'shutdown') {console.log(JSON.stringify({v:1,type:'closed',id:r.id}));process.exit(0);}
  console.log(JSON.stringify({v:1,type:'notice',id:null,message:JSON.stringify(r)}));
});`);
  const client = createBackendClient({repoRoot: f.root}); const state = observer(client);
  try {
    client.start(); await state.wait(event => event.type === 'ready');
    const prepared = '34929261-f6e4-42f6-8d5e-378a2509d109';
    const run = client.run as (text: string, task: undefined, requestId: string) => string;
    assert.equal(run('已保存的后续消息', undefined, prepared), prepared);
    const received = await state.wait(event => event.type === 'notice');
    assert.deepEqual(received.type === 'notice' && JSON.parse(received.message), {v: 1, type: 'run', id: prepared, text: '已保存的后续消息'});
  } finally {await client.shutdown(); await f.close();}
});

test('task navigation transports a structured page independently of run commands', async () => {
  const row = {task_id: 't-selected', objective: '检查旧任务', kind: 'run', mode: 'act', status: 'paused', priority: 0,
    created_at: '2026-11-09T00:00:00Z', updated_at: '2026-11-09T00:01:00Z', worker: null, worker_error: null, pending_approvals: 0};
  const f = await fixture(`import readline from 'node:readline';
console.log(JSON.stringify(${JSON.stringify({...ready, capabilities: ['task-navigation']})}));
readline.createInterface({input:process.stdin}).on('line', line => {
  const r = JSON.parse(line);
  if (r.type === 'shutdown') {console.log(JSON.stringify({v:1,type:'closed',id:r.id}));process.exit(0);}
  const row = ${JSON.stringify(row)};
  if (!r.args || Object.keys(r.args).length) row.objective = '参数缺失';
  if (r.args?.cursor === 'unicode') {row.objective = '🙂'.repeat(4000); row.worker_error = '🙂'.repeat(500);}
  console.log(JSON.stringify({v:1,type:'task_catalog',id:r.id,workspace:'/tmp',session_id:'test',records:[row],next_cursor:null,skipped:0,problems:[]}));
});`);
  const client = createBackendClient({repoRoot: f.root}); const state = observer(client);
  try {
    client.start(); await state.wait(event => event.type === 'ready');
    const id = client.command('tasks', {});
    const result = await state.wait(event => event.type === 'task_catalog' && event.id === id);
    assert.equal(result.type === 'task_catalog' && result.records[0]!.objective, '检查旧任务');
    const unicodeId = client.command('tasks', {cursor: 'unicode'});
    const unicode = await state.wait(event => event.type === 'error' || event.type === 'task_catalog' && event.id === unicodeId);
    assert.ok(unicode.type === 'task_catalog'); assert.equal(unicode.records[0]!.objective, '🙂'.repeat(4000));
  } finally {await client.shutdown(); await f.close();}
});

test('offline attach transport sends only history or exact plan reads and shutdown', async () => {
  const f = await fixture(`import readline from 'node:readline';
console.log(JSON.stringify(${JSON.stringify({...ready, capabilities: ['attach', 'attach-offline', 'inspection-history', 'plan-review']})}));
readline.createInterface({input:process.stdin}).on('line', line => {
  const r = JSON.parse(line);
  if (r.type === 'shutdown') {console.log(JSON.stringify({v:1,type:'closed',id:r.id}));process.exit(0);}
  console.log(JSON.stringify({v:1,type:'notice',id:null,message:JSON.stringify(r)}));
});`);
  const client = createBackendClient({repoRoot: f.root}); const state = observer(client);
  try {
    client.start(); await state.wait(event => event.type === 'ready');
    for (const send of [() => client.run('不能运行'), () => client.cancel('run'), () => client.steer('run', '纠偏'),
      () => client.command('resume'), () => client.command('plan', {action: 'mode-on'})]) assert.throws(send, /离线/);
    const id = client.command('inspections', {task_id: 'bound-task'});
    await state.wait(event => event.type === 'notice' && event.message.includes(id));
    assert.equal(state.events.filter(event => event.type === 'notice').length, 1);
  } finally {await client.shutdown(); await f.close();}
});

test('transport accepts fragmented UTF-8 and completes graceful shutdown', async()=>{
  const f=await fixture(`import readline from 'node:readline';
const bytes=Buffer.from(JSON.stringify(${JSON.stringify(ready)})+'\\n'+JSON.stringify({v:1,type:'text_delta',id:'r',delta:'中文'})+'\\n');
for(const byte of bytes) process.stdout.write(Buffer.from([byte]));
readline.createInterface({input:process.stdin}).on('line',line=>{const request=JSON.parse(line); if(request.type==='shutdown'){console.log(JSON.stringify({v:1,type:'closed',id:request.id}));process.exit(0);}});`);
  const client=createBackendClient({repoRoot:f.root,shutdownTimeoutMs:500}); const state=observer(client);
  try{client.start();const event=await state.wait(event=>event.type==='text_delta');assert.equal(event.type==='text_delta'&&event.delta,'中文');await client.shutdown();assert.ok(state.events.some(event=>event.type==='closed'));await client.shutdown();}
  finally{await client.shutdown();await f.close();}
});

test('startup stderr becomes readable diagnostic events across split UTF-8 bytes',async()=>{
  const message='缺少 API key：请设置 VIBE_API_KEY，或使用 --fake。\n';
  const f=await fixture(`import {setTimeout} from 'node:timers/promises';
for(const byte of Buffer.from(${JSON.stringify(message)})){process.stderr.write(Buffer.from([byte]));await setTimeout(1);}process.exit(2);`);
  const callback:string[]=[];
  const client=createBackendClient({repoRoot:f.root,onDiagnostic:message=>callback.push(message),shutdownTimeoutMs:50});const state=observer(client);
  try{
    client.start();await state.wait(event=>event.type==='closed');
    const diagnostics=state.events.filter(event=>event.type==='diagnostic');
    const combined=diagnostics.map(event=>event.message).join('');
    assert.equal(combined,message);
    assert.doesNotMatch(combined,/\uFFFD/);
    assert.equal(callback.join(''),message);
    const late=observer(client);await late.wait(event=>event.type==='closed');
    assert.equal(late.events.filter(event=>event.type==='diagnostic').map(event=>event.message).join(''),message);
  }finally{await client.shutdown();await f.close();}
});

test('stderr diagnostics have an explicit cumulative 8 KiB limit',async()=>{
  const f=await fixture(`process.stderr.write('中文'.repeat(10000));process.stderr.end(()=>process.exit(2));`);
  const client=createBackendClient({repoRoot:f.root,shutdownTimeoutMs:50});const state=observer(client);
  try{
    client.start();await state.wait(event=>event.type==='closed');
    const messages=state.events.filter(event=>event.type==='diagnostic').map(event=>event.message);
    assert.ok(messages.length>0);
    assert.ok(messages.every(message=>Buffer.byteLength(message,'utf8')<=8192));
    assert.ok(Buffer.byteLength(messages.join(''),'utf8')<=8192);
    assert.match(messages.join(''),/省略/);
    assert.doesNotMatch(messages.join(''),/\uFFFD/);
  }finally{await client.shutdown();await f.close();}
});

test('run, approval and cancel preserve identifiers without interpreting user text', async()=>{
  const f=await fixture(`import readline from 'node:readline';import {appendFileSync} from 'node:fs';
let runId='';console.log(JSON.stringify(${JSON.stringify(ready)}));
readline.createInterface({input:process.stdin}).on('line',line=>{
  appendFileSync('requests.jsonl',line+'\\n');const request=JSON.parse(line);
  if(request.type==='run'){runId=request.id;console.log(JSON.stringify({v:1,type:'approval_request',id:runId,approval_id:'approval-1',call:{id:null,name:'write_file',args:{path:'hello.txt'}},preview:request.text}));}
  if(request.type==='approval')console.log(JSON.stringify({v:1,type:'approval_resolved',id:runId,approval_id:request.approval_id,decision:request.decision}));
  if(request.type==='cancel')console.log(JSON.stringify({v:1,type:'ack',id:request.id,action:'cancel'}));
  if(request.type==='shutdown'){console.log(JSON.stringify({v:1,type:'closed',id:request.id}));process.exit(0);}
});`);
  const client=createBackendClient({repoRoot:f.root,shutdownTimeoutMs:500});const state=observer(client);
  try{
    client.start();await state.wait(event=>event.type==='ready');
    const text='中文 $(touch should-not-run) ; `hello`';const id=client.run(text);
    const approval=await state.wait(event=>event.type==='approval_request');
    assert.equal(approval.type==='approval_request'&&approval.preview,text);
    const responseId=client.approve('approval-1','once');await state.wait(event=>event.type==='approval_resolved');
    client.cancel(id);await state.wait(event=>event.type==='ack'&&event.action==='cancel');
    await client.shutdown();
    const requests=(await readFile(path.join(f.root,'requests.jsonl'),'utf8')).trim().split('\n').map(line=>JSON.parse(line));
    assert.deepEqual(requests.map(request=>request.type),['run','approval','cancel','shutdown']);
    assert.equal(requests[1].id,responseId);
    assert.equal(new Set(requests.map(request=>request.id)).size,4);
    assert.ok(requests.every(request=>request.v===1));
    assert.equal(requests[2].target,id);
    assert.equal(existsSync(path.join(f.root,'should-not-run')),false);
  }finally{await client.shutdown();await f.close();}
});

test('steer frames carry the active run target and reject invalid payloads before sending', async()=>{
  const f=await fixture(`import readline from 'node:readline';import {appendFileSync} from 'node:fs';
console.log(JSON.stringify(${JSON.stringify(ready)}));
readline.createInterface({input:process.stdin}).on('line',line=>{
  appendFileSync('requests.jsonl',line+'\\n');const request=JSON.parse(line);
  if(request.type==='steer')console.log(JSON.stringify({v:1,type:'ack',id:request.id,action:'steer'}));
  if(request.type==='shutdown'){console.log(JSON.stringify({v:1,type:'closed',id:request.id}));process.exit(0);}
});`);
  const client=createBackendClient({repoRoot:f.root,shutdownTimeoutMs:500});const state=observer(client);
  try{
    client.start();await state.wait(event=>event.type==='ready');
    const steerId=client.steer('run-1','优先修复失败的测试');
    const ack=await state.wait(event=>event.type==='ack'&&event.action==='steer');
    assert.ok(ack.type==='ack'&&ack.id);
    assert.equal(ack.id,steerId);
    assert.throws(()=>client.steer('','x'),/目标/);
    assert.throws(()=>client.steer('x'.repeat(129),'x'),/目标/);
    assert.throws(()=>client.steer('run-1','   '),/内容/);
    await client.shutdown();
    const requests=(await readFile(path.join(f.root,'requests.jsonl'),'utf8')).trim().split('\n').map(line=>JSON.parse(line));
    assert.deepEqual(requests.map(request=>request.type),['steer','shutdown']);
    const steer=requests[0];
    assert.deepEqual(Object.keys(steer).sort(),['id','target','text','type','v']);
    assert.equal(steer.v,1);
    assert.equal(steer.id,ack.type==='ack'&&ack.id);
    assert.equal(steer.target,'run-1');
    assert.equal(steer.text,'优先修复失败的测试');
  }finally{await client.shutdown();await f.close();}
});

test('request frame limit excludes the newline and rejects one extra payload byte', async()=>{
  const f=await fixture(`import readline from 'node:readline';import {writeFileSync} from 'node:fs';
console.log(JSON.stringify(${JSON.stringify(ready)}));
readline.createInterface({input:process.stdin}).on('line',line=>{
  const request=JSON.parse(line);
  if(request.type==='run'){writeFileSync('frame-bytes.txt',String(Buffer.byteLength(line,'utf8')));console.log(JSON.stringify({v:1,type:'done',id:request.id,result:{text:'accepted',steps:0,status:'done',usage:{},cost_usd:0,run_id:request.id,stop_reason:''}}));}
  if(request.type==='shutdown'){console.log(JSON.stringify({v:1,type:'closed',id:request.id}));process.exit(0);}
});`);
  const client=createBackendClient({repoRoot:f.root,shutdownTimeoutMs:500});const state=observer(client);
  try{
    client.start();await state.wait(event=>event.type==='ready');
    const overhead=Buffer.byteLength(JSON.stringify({v:1,type:'run',id:'0'.repeat(36),text:''}),'utf8');
    const text='x'.repeat(MAX_FRAME_BYTES-overhead);
    const id=client.run(text);await state.wait(event=>event.type==='done'&&event.id===id);
    assert.equal(Number(await readFile(path.join(f.root,'frame-bytes.txt'),'utf8')),MAX_FRAME_BYTES);
    assert.throws(()=>client.run(text+'x'),/上限/);
  }finally{await client.shutdown();await f.close();}
});

test('startup remains cancellable and scoped approval cannot exceed advertised capabilities', async()=>{
  const f=await fixture(`import readline from 'node:readline';import {appendFileSync} from 'node:fs';
console.log(JSON.stringify(${JSON.stringify(ready)}));
console.log(JSON.stringify({v:1,type:'status',id:'startup',phase:'starting'}));
console.log(JSON.stringify({v:1,type:'approval_request',id:'startup',approval_id:'mcp-start',call:{id:null,name:'mcp_start',args:{}},preview:'start service',decisions:['once','session','deny']}));
readline.createInterface({input:process.stdin}).on('line',line=>{
appendFileSync('requests.jsonl',line+'\\n');const request=JSON.parse(line);
if(request.type==='approval')console.log(JSON.stringify({v:1,type:'approval_resolved',id:'startup',approval_id:request.approval_id,decision:request.decision}));
if(request.type==='cancel')console.log(JSON.stringify({v:1,type:'ack',id:request.id,action:'cancel'}));
if(request.type==='shutdown'){console.log(JSON.stringify({v:1,type:'closed',id:request.id}));process.exit(0);}
});`);
  const client=createBackendClient({repoRoot:f.root,shutdownTimeoutMs:500});const state=observer(client);
  try{
    client.start();await state.wait(event=>event.type==='approval_request');
    assert.throws(()=>client.approve('mcp-start','workspace'),/范围|审批/);
    client.approve('mcp-start','session');
    assert.throws(()=>client.approve('mcp-start','session'),/审批/);
    await state.wait(event=>event.type==='approval_resolved');
    assert.throws(()=>client.approve('mcp-start','session'),/审批/);
    client.cancel('startup');await state.wait(event=>event.type==='ack');
    await client.shutdown();
    const requests=(await readFile(path.join(f.root,'requests.jsonl'),'utf8')).trim().split('\n').map(line=>JSON.parse(line));
    assert.deepEqual(requests.map(request=>request.type),['approval','cancel','shutdown']);
    assert.equal(requests[0].decision,'session');
    assert.equal(requests[1].target,'startup');
  }finally{await client.shutdown();await f.close();}
});

test('backend commands use unique identifiers and --continue is forwarded unchanged', async()=>{
  const f=await fixture(`import readline from 'node:readline';import {appendFileSync,writeFileSync} from 'node:fs';
writeFileSync('argv.json',JSON.stringify(process.argv.slice(2)));
console.log(JSON.stringify(${JSON.stringify(ready)}));
readline.createInterface({input:process.stdin}).on('line',line=>{
appendFileSync('requests.jsonl',line+'\\n');const request=JSON.parse(line);
if(request.type==='command')console.log(JSON.stringify({v:1,type:'command_result',id:request.id,command:request.command,text:'command output'}));
if(request.type==='shutdown'){console.log(JSON.stringify({v:1,type:'closed',id:request.id}));process.exit(0);}
});`);
  // fixture launcher保留传给run_vibe的参数，不经shell求值。
  await writeFile(path.join(f.root,'run_vibe.sh'),`#!/bin/sh\nexec '${process.execPath.replaceAll("'", "'\\''")}' "$(dirname "$0")/fixture.mjs" "$@"\n`);
  const client=createBackendClient({repoRoot:f.root,args:['--continue','--workspace','/tmp/a b'],shutdownTimeoutMs:500});const state=observer(client);
  const commands=client as BackendClient & {command(command:string):string};
  try{
    assert.throws(()=>commands.command('cost'),/尚未就绪/);
    client.start();await state.wait(event=>event.type==='ready');
    const ids=[];
    for(const command of ['resume','permissions','permissions_reset','cost','tools','diff','mcp']){
      const id=commands.command(command);ids.push(id);
      const result=await state.wait(event=>event.type==='command_result'&&event.id===id);
      assert.equal(result.type==='command_result'&&result.command,command);
    }
    assert.equal(new Set(ids).size,7);
    assert.throws(()=>commands.command('bad'),/命令/);
    await client.shutdown();
    assert.deepEqual(JSON.parse(await readFile(path.join(f.root,'argv.json'),'utf8')),['--stdio','--continue','--workspace','/tmp/a b']);
    const requests=(await readFile(path.join(f.root,'requests.jsonl'),'utf8')).trim().split('\n').map(line=>JSON.parse(line));
    assert.deepEqual(requests.map(request=>request.type),[...Array(7).fill('command'),'shutdown']);
    assert.deepEqual(requests.slice(0,7).map(request=>Object.keys(request).sort()),Array(7).fill(['command','id','type','v']));
  }finally{await client.shutdown();await f.close();}
});

test('skills command forwards typed arguments and rejects invalid payloads before sending', async()=>{
  const f = await fixture(`import readline from 'node:readline';import {appendFileSync} from 'node:fs';
console.log(JSON.stringify(${JSON.stringify(ready)}));
readline.createInterface({input:process.stdin}).on('line',line=>{
appendFileSync('requests.jsonl',line+'\\n');const request=JSON.parse(line);
if(request.type==='command')console.log(JSON.stringify({v:1,type:'command_result',id:request.id,command:request.command,text:'skills updated'}));
if(request.type==='shutdown'){console.log(JSON.stringify({v:1,type:'closed',id:request.id}));process.exit(0);}
});`);
  const client = createBackendClient({repoRoot: f.root, shutdownTimeoutMs: 500});
  const state = observer(client);
  try {
    client.start(); await state.wait(event=>event.type==='ready');
    for (const args of [undefined, {}, {action: 'refresh'}, {action: 'activate', name: 'release'}, {action: 'unload', name: 'release'}] as const) {
      const id = client.command('skills', args);
      await state.wait(event=>event.type==='command_result'&&event.id===id);
    }
    for (const [command, args] of [
      ['cost', {}], ['skills', null], ['skills', []], ['skills', {unknown: true}],
      ['skills', {action: 'delete'}], ['skills', {action: 1}], ['skills', {action: 'activate'}],
      ['skills', {action: 'unload', name: ' '}], ['skills', {action: 'activate', name: 7}],
      ['skills', {action: 'list', name: 'release'}], ['skills', {action: 'refresh', name: 'release'}],
    ] as const) {
      assert.throws(()=>client.command(command as BackendCommand, args as SkillsCommandArgs), /参数|skills|技能/);
    }
    await client.shutdown();
    const requests = (await readFile(path.join(f.root, 'requests.jsonl'), 'utf8')).trim().split('\n').map(line=>JSON.parse(line));
    assert.equal(requests.length, 6);
    assert.ok(!('args' in requests[0]));
    assert.deepEqual(requests.slice(1,5).map(request=>request.args), [{}, {action: 'refresh'},
      {action: 'activate', name: 'release'}, {action: 'unload', name: 'release'}]);
  } finally {await client.shutdown(); await f.close();}
});

test('late subscribers receive restored history and unresolved startup approval in order', async()=>{
  const f=await fixture(`import readline from 'node:readline';
console.log(JSON.stringify({...${JSON.stringify(ready)},resumed:true}));
console.log(JSON.stringify({v:1,type:'history_message',id:null,message:{role:'user',content:'旧问题'}}));
console.log(JSON.stringify({v:1,type:'notice',id:null,message:'不会自动续跑'}));
console.log(JSON.stringify({v:1,type:'status',id:'startup',phase:'starting'}));
console.log(JSON.stringify({v:1,type:'status',id:'startup',phase:'waiting_approval'}));
console.log(JSON.stringify({v:1,type:'approval_request',id:'startup',approval_id:'a',call:{id:null,name:'mcp_start',args:{}},preview:'start',decisions:['once','deny']}));
readline.createInterface({input:process.stdin}).on('line',line=>{const request=JSON.parse(line);if(request.type==='shutdown'){console.log(JSON.stringify({v:1,type:'closed',id:request.id}));process.exit(0);}});`);
  const client=createBackendClient({repoRoot:f.root,shutdownTimeoutMs:500});const state=observer(client);
  try{
    client.start();await state.wait(event=>event.type==='approval_request');
    const late=observer(client);await late.wait(event=>event.type==='approval_request');
    assert.deepEqual(late.events.map(event=>event.type),['ready','history_message','notice','status','status','approval_request']);
    assert.equal(late.events[3].type==='status'&&late.events[3].phase,'starting');
    assert.equal(late.events[4].type==='status'&&late.events[4].phase,'waiting_approval');
  }finally{await client.shutdown();await f.close();}
});

for(const [name,source,code] of [
  ['invalid JSON', `console.log('not JSON');setInterval(()=>{},1000);`, 'protocol_error'],
  ['unsupported version', `console.log(JSON.stringify({v:2,type:'closed',id:null}));setInterval(()=>{},1000);`, 'protocol_error'],
  ['oversized frame', `process.stdout.write('x'.repeat(1024*1024+1));setInterval(()=>{},1000);`, 'protocol_error'],
  ['unexpected exit', `process.exit(7);`, 'backend_exit'],
  ['EOF without closed', `process.stdout.end();setInterval(()=>{},1000);`, 'backend_eof'],
] as const) {
  test(`reports ${name} without hanging`, async()=>{
    const f=await fixture(source);const client=createBackendClient({repoRoot:f.root,shutdownTimeoutMs:50});const state=observer(client);
    try{client.start();const event=await state.wait(event=>event.type==='error');assert.equal(event.type==='error'&&event.code,code);await client.shutdown();}
    finally{await client.shutdown();await f.close();}
  });
}

test('spawn failure is reported as a transport error',async()=>{
  const client=createBackendClient({repoRoot:path.join(repoRoot,'.vibe','missing-node-backend'),shutdownTimeoutMs:50});const state=observer(client);
  client.start();const event=await state.wait(event=>event.type==='error');assert.equal(event.type==='error'&&event.code,'backend_spawn');await client.shutdown();
});

test('late subscribers receive terminal errors and closed state',async()=>{
  const f=await fixture('process.exit(8);');const client=createBackendClient({repoRoot:f.root,shutdownTimeoutMs:50});const state=observer(client);
  try{client.start();await state.wait(event=>event.type==='closed');const late=observer(client);await late.wait(event=>event.type==='closed');assert.ok(late.events.some(event=>event.type==='error'&&event.code==='backend_exit'));}
  finally{await client.shutdown();await f.close();}
});

test('parent exit with inherited stderr triggers automatic failure and descendant cleanup', {skip:process.platform==='win32'}, async()=>{
  const f=await fixture(`import {spawn} from 'node:child_process';import {writeFileSync} from 'node:fs';
const worker=spawn(process.execPath,['-e',"setInterval(()=>{},1000);"],{stdio:['ignore','ignore','inherit']});
writeFileSync(new URL('./worker.pid',import.meta.url),String(worker.pid));
console.log(JSON.stringify(${JSON.stringify(ready)}));process.exit(7);`);
  const client=createBackendClient({repoRoot:f.root,shutdownTimeoutMs:50,killTimeoutMs:50});const state=observer(client);
  let workerPid:number|undefined;
  const alive=()=>{try{if(workerPid)process.kill(workerPid,0);return !!workerPid;}catch{return false;}};
  try{
    client.start();await state.wait(event=>event.type==='ready');
    workerPid=Number(await readFile(path.join(f.root,'worker.pid'),'utf8'));
    const error=await state.wait(event=>event.type==='error',2000);
    assert.equal(error.type==='error'&&error.code,'backend_exit');
    await state.wait(event=>event.type==='closed',2000);
    const until=Date.now()+2000;while(alive()&&Date.now()<until)await new Promise(resolve=>setTimeout(resolve,25));
    assert.equal(alive(),false);
  }finally{await client.shutdown();if(alive()&&workerPid)process.kill(workerPid,'SIGKILL');await f.close();}
});

test('shutdown escalates and reaps a backend that ignores shutdown and SIGTERM',async()=>{
  const f=await fixture(`process.on('SIGTERM',()=>{});console.log(JSON.stringify(${JSON.stringify(ready)}));setInterval(()=>{},1000);`);
  const client=createBackendClient({repoRoot:f.root,shutdownTimeoutMs:50,killTimeoutMs:50});const state=observer(client);
  try{client.start();await state.wait(event=>event.type==='ready');const start=Date.now();await client.shutdown();assert.ok(Date.now()-start<3000);}
  finally{await client.shutdown();await f.close();}
});

test('default shutdown gives Python its complete three-second cleanup window',async()=>{
  const f=await fixture(`import readline from 'node:readline';console.log(JSON.stringify(${JSON.stringify(ready)}));
readline.createInterface({input:process.stdin}).on('line',line=>{const request=JSON.parse(line);if(request.type==='shutdown')setTimeout(()=>{console.log(JSON.stringify({v:1,type:'closed',id:request.id}));process.exit(0);},1600);});`);
  const client=createBackendClient({repoRoot:f.root});const state=observer(client);
  try{client.start();await state.wait(event=>event.type==='ready');await client.shutdown();assert.ok(state.events.some(event=>event.type==='closed'&&event.id!==null));assert.equal(state.events.some(event=>event.type==='error'),false);}
  finally{await client.shutdown();await f.close();}
});

test('shutdown removes descendants even when the parent exits after closed', {skip: process.platform === 'win32'}, async()=>{
  const f=await fixture(`import {spawn} from 'node:child_process';
import {writeFileSync} from 'node:fs';import readline from 'node:readline';
const worker=spawn(process.execPath,['-e',"process.on('SIGTERM',()=>{});console.log('ready');setInterval(()=>{},1000);"],{stdio:['ignore','pipe','ignore']});
writeFileSync(new URL('./worker.pid',import.meta.url),String(worker.pid));
worker.stdout.once('data',()=>console.log(JSON.stringify(${JSON.stringify(ready)})));
readline.createInterface({input:process.stdin}).on('line',line=>{const request=JSON.parse(line);if(request.type==='shutdown'){console.log(JSON.stringify({v:1,type:'closed',id:request.id}));process.exit(0);}});`);
  const client=createBackendClient({repoRoot:f.root,shutdownTimeoutMs:50,killTimeoutMs:50});const state=observer(client);
  let workerPid:number|undefined;
  const alive=()=>{try{if(workerPid)process.kill(workerPid,0);return !!workerPid;}catch{return false;}};
  try{client.start();await state.wait(event=>event.type==='ready');workerPid=Number(await readFile(path.join(f.root,'worker.pid'),'utf8'));await client.shutdown();const until=Date.now()+2000;while(alive()&&Date.now()<until)await new Promise(resolve=>setTimeout(resolve,25));assert.equal(alive(),false);}
  finally{await client.shutdown();if(alive()&&workerPid)process.kill(workerPid,'SIGKILL');await f.close();}
});

test('runs the actual Python fake backend and shuts it down',{skip:!existsSync(path.join(repoRoot,'.venv','bin','python3'))},async()=>{
  const base=path.join(repoRoot,'.vibe','node-transport-tests');await mkdir(base,{recursive:true});const workspace=await mkdtemp(path.join(base,'workspace-'));
  const client=createBackendClient({repoRoot,args:['--fake','--workspace',workspace,'--chat-only'],shutdownTimeoutMs:1000});const state=observer(client);
  try{client.start();await state.wait(event=>event.type==='status'&&event.phase==='idle');
    const loaded=state.events.find(event=>event.type==='context_snapshot'&&event.snapshot.phase==='loaded');
    assert.ok(loaded?.type==='context_snapshot'&&loaded.id===null&&loaded.snapshot.total_tokens>0);
    const startupUsage=state.events.find(event=>event.type==='metrics'&&event.id===null);
    assert.ok(startupUsage?.type==='metrics'&&startupUsage.used_tokens===0);
    assert.equal(state.events.some(event=>['text_delta','done'].includes(event.type)),false);
    const id=client.run('你好');const done=await state.wait(event=>event.type==='done'&&event.id===id);assert.ok(state.events.some(event=>event.type==='text_delta'&&event.id===id),JSON.stringify(state.events));assert.equal(done.type==='done'&&done.result.status,'done');
    const snapshots=state.events.filter(event=>event.type==='context_snapshot').filter(event=>event.snapshot.phase!=='loaded');
    assert.ok(snapshots.length>0,JSON.stringify(state.events));
    assert.ok(snapshots.every(event=>event.id===id&&event.snapshot.phase==='request'&&event.snapshot.estimated));
    assert.ok(snapshots.every(event=>done.type==='done'&&event.snapshot.run_id===done.result.run_id));
    assert.ok(snapshots.every(event=>event.snapshot.request_id.startsWith(event.snapshot.run_id+':')));
    const late=observer(client);await late.wait(event=>event.type==='ready');
    assert.equal(late.events.some(event=>event.type==='context_snapshot'||event.type==='metrics'),false);
    await client.shutdown();assert.ok(state.events.some(event=>event.type==='closed'));}
  finally{await client.shutdown();await rm(workspace,{recursive:true,force:true});}
});

test('restored task progress does not disconnect actual Python startup',{skip:!existsSync(path.join(repoRoot,'.venv','bin','python3'))},async()=>{
  const base=path.join(repoRoot,'.vibe','node-transport-tests');await mkdir(base,{recursive:true});
  const workspace=await mkdtemp(path.join(base,'resume-progress-'));
  execFileSync(path.join(repoRoot,'.venv','bin','python3'),['-c',
    `import sys
from app.session.store import SessionStore
from core.contracts import Message
from core.task import TaskState
session = SessionStore.new(sys.argv[1], 'fake', purpose='test')
session.append_message(Message('user', '继续已有计划'))
session.append_message(Message('assistant', '已完成第一步'))
session.save_state(TaskState(goal='已有任务', plan=['第一步', '第二步'], done=['第一步']))
`,workspace],{cwd:repoRoot});
  const client=createBackendClient({repoRoot,args:['--fake','--workspace',workspace,'--continue'],shutdownTimeoutMs:1000});
  const state=observer(client);
  try{
    client.start();
    const startup=await state.wait(event=>event.type==='error'||event.type==='closed'||(event.type==='status'&&event.phase==='idle'));
    assert.equal(startup.type,'status',JSON.stringify(startup));
    const metrics=state.events.find(event=>event.type==='metrics');
    assert.ok(metrics?.type==='metrics');
    assert.equal(metrics.id,null);
    assert.equal(metrics.used_tokens,0);
    assert.equal(metrics.cost_usd,0);
    assert.equal(metrics.done,1);
    assert.equal(metrics.planned,2);
    const view=state.events.reduce((current,event)=>reduceView(current,{type:'event',event}),initialState);
    assert.equal(view.phase,'idle');
    assert.equal(view.activeId,null);
    assert.equal(view.hasStartedRun,false);
    assert.equal(view.metrics?.done,1);
    assert.equal(view.metrics?.planned,2);
    const late=observer(client);
    await late.wait(event=>event.type==='metrics');
    assert.ok(late.events.some(event=>event.type==='metrics'&&event.done===1&&event.planned===2));
    const id=client.command('cost');
    const cost=await state.wait(event=>event.type==='command_result'&&event.id===id);
    assert.match(cost.type==='command_result'?cost.text:'',/runs 0/);
    assert.equal(state.events.some(event=>event.type==='error'||event.type==='done'||event.type==='text_delta'),false);
  }finally{await client.shutdown();await rm(workspace,{recursive:true,force:true});}
});

test('actual Python restores a session without running and serves explicit commands',{skip:!existsSync(path.join(repoRoot,'.venv','bin','python3'))},async()=>{
  const base=path.join(repoRoot,'.vibe','node-transport-tests');await mkdir(base,{recursive:true});
  const workspace=await mkdtemp(path.join(base,'resume-workspace-'));
  const first=createBackendClient({repoRoot,args:['--fake','--workspace',workspace,'--chat-only'],shutdownTimeoutMs:1000});
  const firstState=observer(first);
  let restored:BackendClient|undefined;
  try{
    first.start();const initial=await firstState.wait(event=>event.type==='ready');
    const id=first.run('保留这条旧对话');await firstState.wait(event=>event.type==='done'&&event.id===id);await first.shutdown();
    restored=createBackendClient({repoRoot,args:['--fake','--workspace',workspace,'--chat-only','--continue'],shutdownTimeoutMs:1000});
    const state=observer(restored);restored.start();
    const ready=await state.wait(event=>event.type==='ready');
    assert.equal(ready.type==='ready'&&ready.resumed,true);
    assert.equal(ready.type==='ready'&&ready.session_id,initial.type==='ready'&&initial.session_id);
    await state.wait(event=>event.type==='history_message'&&event.message.role==='user');
    assert.ok(state.events.some(event=>event.type==='history_message'&&event.message.content==='保留这条旧对话'));
    for(const command of ['permissions','permissions_reset','cost','tools','mcp'] as const){
      const commandId=restored.command(command);
      const result=await state.wait(event=>event.type==='command_result'&&event.id===commandId);
      assert.ok(result.type==='command_result'&&result.text.length>0);
    }
    assert.equal(state.events.some(event=>(event.type==='metrics'&&event.id!==null)||event.type==='text_delta'||event.type==='done'),false);
    const diffId=restored.command('diff');
    const approval=await state.wait(event=>event.type==='approval_request'&&event.id===diffId);
    assert.ok(approval.type==='approval_request'&&approval.decisions?.includes('deny'));
    restored.approve(approval.type==='approval_request'?approval.approval_id:'','deny');
    const diff=await state.wait(event=>event.type==='command_result'&&event.id===diffId);
    assert.match(diff.type==='command_result'?diff.text:'',/拒绝|未执行/);
    const resumeId=restored.command('resume');
    const done=await state.wait(event=>event.type==='done'&&event.id===resumeId);
    assert.equal(done.type==='done'&&done.result.status,'done');
  }finally{await first.shutdown();await restored?.shutdown();await rm(workspace,{recursive:true,force:true});}
});

test('actual Python restores original conversation after context compaction for live and late subscribers',{skip:!existsSync(path.join(repoRoot,'.venv','bin','python3'))},async()=>{
  const base=path.join(repoRoot,'.vibe','node-transport-tests');await mkdir(base,{recursive:true});
  const workspace=await mkdtemp(path.join(base,'compacted-resume-'));
  const client=createBackendClient({repoRoot,args:['--fake','--workspace',workspace,'--chat-only','--continue'],shutdownTimeoutMs:1000});
  try{
    const seeded:{id:string;messages:PersistedMessage[]}=JSON.parse(execFileSync(path.join(repoRoot,'.venv','bin','python3'),['-c',`
import json
import sys
from dataclasses import asdict
from app.session.store import SessionStore
from core.contracts import Message, ToolCall

session = SessionStore.new(sys.argv[1], "fake", purpose="test")
goal = Message("user", "第一轮：保留完整旧对话")
tail = Message("user", "第二轮：继续检查")
messages = [
    goal,
    Message("assistant", "旧回复正文，压缩之后仍应该可以回看。\\n第二行内容。"),
    Message("assistant", "已请求保存文件", tool_calls=[ToolCall("write_file", {"path": "should-not-run.txt", "content": "saved"}, "saved-write")]),
    Message("tool", "旧工具结果完整内容", name="write_file", tool_call_id="saved-write"),
    Message("assistant", "第一轮已经结束"),
    tail,
    Message("assistant", "第二轮旧回复"),
]
for message in messages:
    session.append_message(message)
session.save_context([goal, Message("assistant", "模型专用分层摘要", name="history_summary_l2"), tail])
print(json.dumps({"id": session.id, "messages": [asdict(message) for message in messages]}, ensure_ascii=False))
`,workspace],{cwd:repoRoot,encoding:'utf8'}));
    const live=observer(client);client.start();
    await live.wait(event=>event.type==='status'&&event.phase==='idle');
    const ready=live.events.find(event=>event.type==='ready');
    assert.equal(ready?.type==='ready'&&ready.resumed,true);
    assert.equal(ready?.type==='ready'&&ready.session_id,seeded.id);
    const late=observer(client);
    await late.wait(event=>event.type==='status'&&event.phase==='idle');
    for(const received of [live.events,late.events]){
      assert.deepEqual(received.filter(event=>event.type==='history_message').map(event=>event.message),seeded.messages);
      const view=received.reduce((state,event)=>reduceView(state,{type:'event',event}),initialState);
      assert.equal(view.phase,'idle');
      assert.equal(view.activeId,null);
      assert.deepEqual(view.entries.filter(entry=>entry.kind==='task').map(entry=>entry.text),['第一轮：保留完整旧对话','第二轮：继续检查']);
      assert.ok(view.entries.some(entry=>entry.kind==='assistant'&&entry.text==='旧回复正文，压缩之后仍应该可以回看。\n第二行内容。'));
      assert.equal(view.entries.find(entry=>entry.call?.id==='saved-write')?.output,'旧工具结果完整内容');
      assert.equal(view.entries.some(entry=>entry.text==='模型专用分层摘要'),false);
      assert.equal(received.some(event=>['text_delta','tool_call','tool_result','done'].includes(event.type)||(event.type==='metrics'&&event.id!==null)),false);
      assert.equal(view.contextSnapshots.length,1);
      assert.equal(view.contextSnapshots[0]?.phase,'loaded');
      assert.ok(view.contextSnapshots[0]!.sections.history>0);
      assert.equal(view.metrics?.used_tokens,0);
      assert.equal(received.some(event=>event.type==='error'),false);
    }
    assert.equal(existsSync(path.join(workspace,'should-not-run.txt')),false);
  }finally{await client.shutdown();await rm(workspace,{recursive:true,force:true});}
});

test('actual FastMCP startup is approved through Node and its process exits on shutdown',{skip:process.platform==='win32'||!existsSync(path.join(repoRoot,'.venv','bin','python3'))},async()=>{
  const f=await fixture('');
  const server=path.join(f.root,'server.py');
  const marker=path.join(f.root,'server.pid');
  await writeFile(server,`from fastmcp import FastMCP\nfrom pathlib import Path\nimport os\nPath(${JSON.stringify(marker)}).write_text(str(os.getpid()))\nmcp = FastMCP("node-transport-test")\n@mcp.tool\ndef echo(text: str) -> str:\n    return text\nmcp.run(transport="stdio", show_banner=False)\n`);
  const servers=JSON.stringify({local:{command:path.join(repoRoot,'.venv','bin','python3'),args:[server]}});
  const quote=(value:string)=>`'${value.replaceAll("'", "'\\''")}'`;
  await writeFile(path.join(f.root,'run_vibe.sh'),`#!/bin/sh\nexport VIBE_MCP_CONFIG=''\nexport VIBE_MCP_SERVERS=${quote(servers)}\nexec ${quote(path.join(repoRoot,'run_vibe.sh'))} "$@"\n`);
  const client=createBackendClient({repoRoot:f.root,args:['--fake','--workspace',f.root]});
  const state=observer(client);
  let pid:number|undefined;
  const alive=()=>{try{if(pid)process.kill(pid,0);return Boolean(pid);}catch{return false;}};
  try{
    client.start();const approval=await state.wait(event=>event.type==='approval_request'&&event.id==='startup');
    assert.ok(approval.type==='approval_request'&&approval.call.name==='mcp_start');
    assert.deepEqual(approval.type==='approval_request'&&approval.decisions,['once','session','deny']);
    assert.equal(existsSync(marker),false);
    const base=state.events.find(event=>event.type==='context_snapshot');
    assert.ok(base?.type==='context_snapshot'&&base.snapshot.phase==='loaded');
    client.approve(approval.type==='approval_request'?approval.approval_id:'','once');
    await state.wait(event=>event.type==='status'&&event.phase==='idle');
    const loaded=state.events.filter(event=>event.type==='context_snapshot');
    assert.equal(loaded.length,2);
    assert.ok(loaded.every(event=>event.id===null&&event.snapshot.phase==='loaded'));
    assert.ok(loaded.at(-1)!.snapshot.sections.tools>loaded[0]!.snapshot.sections.tools);
    const view=state.events.reduce((view,event)=>reduceView(view,{type:'event',event}),initialState);
    assert.equal(view.contextSnapshots.length,1);
    assert.equal(view.contextSnapshots[0]?.snapshot_id,loaded.at(-1)!.snapshot.snapshot_id);
    assert.equal(view.metrics?.used_tokens,0);
    const id=client.command('tools');
    const tools=await state.wait(event=>event.type==='command_result'&&event.id===id);
    assert.match(tools.type==='command_result'?tools.text:'',/mcp_local_echo/);
    pid=Number(await readFile(marker,'utf8'));
    assert.equal(alive(),true);
    await client.shutdown();
    const deadline=Date.now()+2000;while(alive()&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,25));
    assert.equal(alive(),false);
    assert.equal(state.events.some(event=>event.type==='error'),false);
  }finally{await client.shutdown();if(alive()&&pid)process.kill(pid,'SIGKILL');await f.close();}
});

test('revision commands require the separate backend capability', async () => {
  const f = await fixture(`import readline from 'node:readline';
console.log(JSON.stringify(${JSON.stringify({...ready, capabilities: ['plan-review', 'tasks']})}));
readline.createInterface({input:process.stdin}).on('line',line=>{const request=JSON.parse(line);
if(request.type==='shutdown'){console.log(JSON.stringify({v:1,type:'closed',id:request.id}));process.exit(0);}});`);
  const client = createBackendClient({repoRoot: f.root, shutdownTimeoutMs: 500});
  const state = observer(client);
  try {
    client.start(); await state.wait(event => event.type === 'ready');
    assert.throws(() => client.command('plan', {action: 'request-changes', feedback: '检查空文件', target: {
      session_id: 'test', plan_task_id: 't-20261108-000000-abcdef', plan_id: 'plan-1',
      plan_version: 1, plan_hash: 'a'.repeat(64),
    }}), /不支持.*修订/);
    assert.throws(() => client.command('plan', {action: 'list-revisions', target: {
      session_id: 'test', plan_task_id: 't-20261108-000000-abcdef', plan_id: 'plan-1',
      plan_version: 1, plan_hash: 'a'.repeat(64),
    }}), /不支持.*修订记录/);
  } finally {await client.shutdown(); await f.close();}
});

test('explore notification and plan commands forward their args', async()=>{
  const f = await fixture(`import readline from 'node:readline';import {appendFileSync} from 'node:fs';
console.log(JSON.stringify(${JSON.stringify(ready)}));
readline.createInterface({input:process.stdin}).on('line',line=>{
appendFileSync('requests.jsonl',line+'\\n');const request=JSON.parse(line);
if(request.type==='command')console.log(JSON.stringify({v:1,type:'command_result',id:request.id,command:request.command,text:'ok'}));
if(request.type==='shutdown'){console.log(JSON.stringify({v:1,type:'closed',id:request.id}));process.exit(0);}
});`);
  const client = createBackendClient({repoRoot: f.root, shutdownTimeoutMs: 500});
  const state = observer(client);
  try {
    client.start(); await state.wait(event=>event.type==='ready');
    assert.throws(() => client.command('plan', {action: 'review'}), /不支持/);
    assert.throws(() => client.command('plan', {action: 'approve-execute', target: {
      session_id: 'test', plan_task_id: 't-20261108-000000-abcdef', plan_id: 'plan-1',
      plan_version: 1, plan_hash: 'a'.repeat(64),
    }}), /不支持/);
    const ids = [
      client.command('explore', {prompt: 'read'}),
      client.command('notifications', {kind: 'plan_acceptance'}),
      client.command('plan', {action: 'list'}),
      client.command('plan', {action: 'mode-on'}),
      client.command('plan', {action: 'request-changes', feedback: '补充失败路径测试'}),
      client.command('plan', {action: 'markdown'}),
    ];
    for (const id of ids) await state.wait(event=>event.type==='command_result'&&event.id===id);
    await client.shutdown();
    const requests = (await readFile(path.join(f.root,'requests.jsonl'),'utf8')).trim().split('\n').map(line=>JSON.parse(line));
    assert.deepEqual(requests[0].args, {prompt: 'read'});
    assert.deepEqual(requests[1].args, {kind: 'plan_acceptance'});
    assert.deepEqual(requests[2].args, {action: 'list'});
    assert.deepEqual(requests[3].args, {action: 'mode-on'});
    assert.deepEqual(requests[4].args, {action: 'request-changes', feedback: '补充失败路径测试'});
    assert.deepEqual(requests[5].args, {action: 'markdown'});
  } finally {await client.shutdown(); await f.close();}
});

test('plan_mode frames are forwarded and replayed to late subscribers', async()=>{
  const f=await fixture(`import readline from 'node:readline';
console.log(JSON.stringify(${JSON.stringify(ready)}));
console.log(JSON.stringify({v:1,type:'plan_mode',id:null,mode:{active:true,pending:false,wanted:true}}));
readline.createInterface({input:process.stdin}).on('line',line=>{const request=JSON.parse(line); if(request.type==='shutdown'){console.log(JSON.stringify({v:1,type:'closed',id:request.id}));process.exit(0);}});`);
  const client=createBackendClient({repoRoot:f.root,shutdownTimeoutMs:500});
  try{
    client.start();
    const live=observer(client);
    const liveMode=await live.wait(event=>event.type==='plan_mode');
    assert.ok(liveMode.type==='plan_mode');
    assert.deepEqual(liveMode.mode,{active:true,pending:false,wanted:true});
    const late=observer(client);
    const replayed=await late.wait(event=>event.type==='plan_mode');
    assert.ok(replayed.type==='plan_mode');
    assert.deepEqual(replayed.mode,{active:true,pending:false,wanted:true});
    assert.deepEqual(late.events.filter(event=>event.type==='ready').length,1);
    const view=late.events.reduce((state,event)=>reduceView(state,{type:'event',event}),initialState);
    assert.deepEqual(view.planMode,{active:true,pending:false,wanted:true});
  }finally{await client.shutdown();await f.close();}
});
