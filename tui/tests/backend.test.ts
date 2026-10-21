import assert from 'node:assert/strict';
import {chmod, mkdir, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import {createBackendClient, type BackendClient} from '../src/backend.js';
import {MAX_FRAME_BYTES, type ServerEvent} from '../src/protocol.js';

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
    client.approve('approval-1','once');await state.wait(event=>event.type==='approval_resolved');
    client.cancel(id);await state.wait(event=>event.type==='ack'&&event.action==='cancel');
    await client.shutdown();
    const requests=(await readFile(path.join(f.root,'requests.jsonl'),'utf8')).trim().split('\n').map(line=>JSON.parse(line));
    assert.deepEqual(requests.map(request=>request.type),['run','approval','cancel','shutdown']);
    assert.equal(new Set(requests.map(request=>request.id)).size,4);
    assert.ok(requests.every(request=>request.v===1));
    assert.equal(requests[2].target,id);
    assert.equal(existsSync(path.join(f.root,'should-not-run')),false);
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
  try{client.start();await state.wait(event=>event.type==='ready');const id=client.run('你好');const done=await state.wait(event=>event.type==='done'&&event.id===id);assert.ok(state.events.some(event=>event.type==='text_delta'&&event.id===id),JSON.stringify(state.events));assert.equal(done.type==='done'&&done.result.status,'done');await client.shutdown();assert.ok(state.events.some(event=>event.type==='closed'));}
  finally{await client.shutdown();await rm(workspace,{recursive:true,force:true});}
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
    assert.equal(state.events.some(event=>event.type==='metrics'||event.type==='text_delta'||event.type==='done'),false);
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
    client.approve(approval.type==='approval_request'?approval.approval_id:'','once');
    await state.wait(event=>event.type==='status'&&event.phase==='idle');
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
