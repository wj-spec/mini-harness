import assert from 'node:assert/strict';
import {afterEach,test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup,render} from 'ink-testing-library';
import {App} from '../src/app.js';
import {initialState} from '../src/view-state.js';
import type {BackendClient} from '../src/backend.js';
import type {ServerEvent,CommandArgs} from '../src/protocol.js';
afterEach(cleanup);
const overview={limits:{max_steps:6,max_run_seconds:100,max_run_tokens:8000,max_run_cost_usd:0},
 sources:{max_steps:'默认值',max_run_seconds:'默认值',max_run_tokens:'默认值',max_run_cost_usd:'默认值'},
 path:'/project/.env',revision:'a'.repeat(64),advanced:{mcp_servers:0,mcp_connected:0,sandbox:false,sandbox_network:false,notifications:false,priced:false},
 message:'',workspace:'/project',session_id:'s'};
class Client implements BackendClient {
 listeners=new Set<(event:ServerEvent)=>void>(); commands:{name:string;args?:CommandArgs}[]=[]; runs:string[]=[];
 capabilities(){return ['workbench-settings','model-settings'];} start(){}
 subscribe(fn:(event:ServerEvent)=>void){this.listeners.add(fn);return()=>{this.listeners.delete(fn);};}
 emit(event:unknown){this.listeners.forEach(fn=>fn(event as ServerEvent));}
 command(name:string,args?:CommandArgs){
  this.commands.push({name,args});const id='c'+this.commands.length;
  setTimeout(()=>{
   if(name==='settings'&&args&&'action' in args&&args.action==='list')this.emit({v:1,type:'settings_overview',id,...overview});
   else if(name==='settings')this.emit({v:1,type:'error',id,code:'busy',message:'操作被拒绝；草稿保留'});
   else this.emit({v:1,type:'model_settings',id,model:'fake',provider:'fake',base_url:'https://example.com/v1',trust_env:false,key_present:false,
    sources:{model:'默认值',provider:'默认值',base_url:'默认值',api_key:'默认值',trust_env:'默认值'},
    path:'/project/.env',revision:'a'.repeat(64),saved_model:'',applied:false,message:'',workspace:'/project',session_id:'s'});
  },15);return id;
 }
 run(text:string){this.runs.push(text);return 'r';}cancel(){return 'cancel';}approve(){return 'approve';}steer(){return 'steer';}async shutdown(){}
}
async function mount(draft=''){
 const client=new Client();const v=render(<App client={client} initialDraft={{value:draft,cursor:2}}
 initialState={{...initialState,phase:'idle',workspace:'/project',sessionId:'s',model:'deepseek-chat'}} windowSize={{columns:80,rows:24}} publishSidebarSignal={()=>{}}/>);
 const key=async(s:string)=>{v.stdin.write(s);await pause(45);};await pause(40);return {...v,client,key};
}
test('settings categories open model settings and return to the original Chinese draft',async()=>{
 const v=await mount('中文草稿');await v.key('\x14');
 for(let i=0;i<4;i++)await v.key('\x1b[C');
 await v.key('\r');assert.match(v.lastFrame()!,/设置中心/);
 assert.match(v.lastFrame()!,/任务预算/);assert.match(v.lastFrame()!,/外部工具/);
 await v.key('\r');
 assert.match(v.lastFrame()!,/模型设置/);assert.doesNotMatch(v.lastFrame()!,/设置中心/);
 await v.key('\x1b');assert.match(v.lastFrame()!,/设置中心/);
 await v.key('\x1b');await v.key('中');assert.match(v.lastFrame()!,/中文中草稿/);
 assert.deepEqual(v.client.commands.map(c=>c.name),['settings','model','settings']);assert.deepEqual(v.client.runs,[]);
});
test('rejected settings mutation releases active command and preserves numeric draft',async()=>{
 const v=await mount();await v.key('/settings');await v.key('\r');
 await v.key('\x1b[B');await v.key('\r');
 assert.match(v.lastFrame()!,/单段限制/);
 await v.key('\r');await v.key('\x15');await v.key('12');await v.key('\x1b');
 for(let i=0;i<4;i++)await v.key('\t');await v.key('\r');
 assert.match(v.lastFrame()!,/操作被拒绝/);
 assert.match(JSON.stringify(v.client.commands.at(-1)),/"max_steps":12/);
 await v.key('\x1b');await v.key('\x1b');assert.match(v.lastFrame()!,/未保存/);
 await v.key('\x1b');assert.doesNotMatch(v.lastFrame()!,/设置中心/);
 assert.deepEqual(v.client.runs,[]);
});

test('settings home groups by purpose and keeps restart-only items read-only',async()=>{
 const v=await mount();await v.key('/settings');await v.key('\r');
 await pause(40);
 const frame=v.lastFrame()!;
 assert.match(frame,/模型连接/);assert.match(frame,/任务预算/);
 assert.match(frame,/外部工具/);assert.match(frame,/权限与安全/);assert.match(frame,/更多设置/);
 assert.match(frame,/单段 6 步/);
 for(let i=0;i<4;i++)await v.key('\x1b[B');
 await v.key('\r');await pause(30);
 assert.match(v.lastFrame()!,/沙箱执行：未启用/);
 assert.match(v.lastFrame()!,/未配置单价/);
 await v.key('\x1b');assert.match(v.lastFrame()!,/设置中心/);
});

test('settings model category summarizes the current model and detection state',async()=>{
 const v=await mount();await v.key('/settings');await v.key('\r');
 await pause(40);
 assert.match(v.lastFrame()!,/模型连接  deepseek-chat · 尚未检测/);
});
