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
const overview={path:'/project/.env',revision:'a'.repeat(64),source:'.env',editable:true, servers:[{name:'demo',command:'python',args_count:1,env_keys:['TOKEN'],connected:true}],workspace:'/project',session_id:'s',message:''};
class Client implements BackendClient {
 listeners=new Set<(event:ServerEvent)=>void>(); commands:{name:string;args?:CommandArgs}[]=[]; runs:string[]=[];
 capabilities(){return ['mcp-editor','workbench-settings'];} start(){}
 subscribe(fn:(event:ServerEvent)=>void){this.listeners.add(fn);return()=>{this.listeners.delete(fn);};}
 emit(event:unknown){this.listeners.forEach(fn=>fn(event as ServerEvent));}
 command(name:string,args?:CommandArgs){
   this.commands.push({name,args}); const id='c'+this.commands.length;
   setTimeout(()=>{
     if(args&&'action' in args&&args.action==='list')this.emit({v:1,type:'mcp_config',id,...overview});
     else {this.emit({v:1,type:'error',id,code:'mcp_config_error',message:'保存被拒绝；草稿保留'});
       this.emit({v:1,type:'command_result',id,command:'mcp_config',text:'保存被拒绝；草稿保留'});
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
test('MCP editor failed save retains draft and releases command lock',async()=>{
 const v=await mount();await v.key('/mcp edit');await v.key('\r');
 assert.match(v.lastFrame()!,/MCP 设置/);
 for(let i=0;i<3;i++)await v.key('\t');
 await v.key('\r');await v.key('{"TOKEN":"secret-value"}');await v.key('\x1b');
 await v.key('\t');await v.key('\r');
 assert.match(v.lastFrame()!,/保存被拒绝/);assert.doesNotMatch(v.lastFrame()!,/secret-value/);
 await v.key('\r');assert.equal(v.client.commands.length,3);
 await v.key('\x1b');assert.match(v.lastFrame()!,/未保存/);
 assert.deepEqual(v.client.runs,[]);
});
