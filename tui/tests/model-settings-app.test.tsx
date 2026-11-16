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
const overview={model:'old',base_url:'https://example.com/v1',provider:'openai',trust_env:false,key_present:true,
 sources:{model:'默认值',base_url:'默认值',api_key:'默认值',trust_env:'默认值',provider:'默认值'},
 path:'/project/.env',revision:'a'.repeat(64),saved_model:'old',applied:false,message:'',workspace:'/project',session_id:'s'};
class Client implements BackendClient {
 listeners=new Set<(event:ServerEvent)=>void>(); commands:{name:string;args?:CommandArgs}[]=[]; runs:string[]=[];
 capabilities(){return ['model-settings'];} start(){}
 subscribe(fn:(event:ServerEvent)=>void){this.listeners.add(fn);return()=>{this.listeners.delete(fn);};}
 emit(event:unknown){this.listeners.forEach(fn=>fn(event as ServerEvent));}
 command(name:string,args?:CommandArgs){
   this.commands.push({name,args}); const id='c'+this.commands.length;
   setTimeout(()=>{
     if(args&&'action' in args&&args.action==='list')this.emit({v:1,type:'model_settings',id,...overview});
     else {this.emit({v:1,type:'error',id,code:'model_settings_error',message:'保存被拒绝；草稿保留'});
       this.emit({v:1,type:'command_result',id,command:'model',text:'保存被拒绝；草稿保留'});
       this.emit({v:1,type:'status',id:null,phase:'idle'});}
   },15); return id;
 }
 run(text:string){this.runs.push(text);return 'r';} cancel(){return 'cancel';} approve(){return 'approve';} steer(){return 'steer';} async shutdown(){}
}
async function mount(draft=''){
 const client=new Client();
 const v=render(<App client={client} initialDraft={{value:draft,cursor:2}}
   initialState={{...initialState,phase:'idle',workspace:'/project',sessionId:'s'}}
   windowSize={{columns:80,rows:24}} publishSidebarSignal={()=>{}}/>);
 const key=async(s:string)=>{v.stdin.write(s);await pause(45);};
 await pause(40);return {...v,client,key};
}
test('guide enters model settings and returns to Chinese draft without running',async()=>{
 const v=await mount('中文草稿');await v.key('\x14');
 for(let i=0;i<4;i++)await v.key('\x1b[C');
 await v.key('\r');assert.match(v.lastFrame()!,/模型设置/);
 await v.key('\x1b');await v.key('中');assert.match(v.lastFrame()!,/中文中草稿/);
 assert.deepEqual(v.client.runs,[]);
 assert.deepEqual(v.client.commands.map(c=>c.name),['model']);
});
test('failed model operation returns to idle and retains hidden editing draft',async()=>{
 const v=await mount();await v.key('/model');await v.key('\r');
 await v.key('\t');await v.key('\t');await v.key('\r');await v.key('secret-value');await v.key('\x1b');
 await v.key('\t');await v.key('\t');await v.key('\r');
 assert.match(v.lastFrame()!,/保存被拒绝/);assert.doesNotMatch(v.lastFrame()!,/secret-value/);
 await v.key('\r');assert.equal(v.client.commands.length,3);
 await v.key('\x1b');assert.match(v.lastFrame()!,/丢弃/);
 await v.key('\x1b'); // cancel discard
 assert.match(v.lastFrame()!,/模型设置/);
 assert.deepEqual(v.client.runs,[]);
});
