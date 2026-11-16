import assert from 'node:assert/strict';
import {afterEach,test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {render,cleanup} from 'ink-testing-library';
import stringWidth from 'string-width';
import {McpPanel} from '../src/mcp-panel.js';
import {getPalette} from '../src/theme.js';
import {validMcpOverview,validateMcpArgs} from '../src/mcp-settings.js';
afterEach(cleanup);
export const overview={path:'/project/.env',revision:'a'.repeat(64),source:'.env',editable:true,
 servers:[{name:'demo',command:'python',args_count:1,env_keys:['TOKEN'],connected:true}],
 workspace:'/project',session_id:'s',message:''};
test('MCP protocol rejects secrets in overview and malformed editing requests',()=>{
 assert.ok(validMcpOverview(overview));
 assert.equal(validMcpOverview({...overview,env:{TOKEN:'secret'}}),false);
 assert.equal(validMcpOverview({...overview,servers:[{...overview.servers[0],env:{TOKEN:'secret'}}]}),false);
 assert.throws(()=>validateMcpArgs({action:'save',name:'demo'}));
 assert.doesNotThrow(()=>validateMcpArgs({action:'reload'}));
});
for(const [columns,rows] of [[32,12],[80,24],[120,40]]){
 test('MCP editor hides values, preserves draft on failure and fits '+columns+'x'+rows,async()=>{
  const sent:unknown[]=[];
  const props={overview,loading:false,problem:'',active:true,busy:false,columns:columns!,rows:rows!,
   palette:getPalette('dark'),onRequest:(args:unknown)=>sent.push(args),onClose:()=>{}};
  const v=render(<McpPanel {...props}/>);
  const key=async(s:string)=>{v.stdin.write(s);await pause(30);};
  await pause(40);
  for(let i=0;i<3;i++)await key('\t');
  await key('\r');await key('{"TOKEN":"secret-value"}');await key('\x1b');
  assert.doesNotMatch(v.lastFrame()!,/secret-value/);
  assert.match(v.lastFrame()!,/隐藏/);
  assert.equal(v.lastFrame()!.split('\n').length,rows);
  assert.ok(v.lastFrame()!.split('\n').every(l=>stringWidth(l)<=columns!));
  await key('\t');await key('\r');
  assert.match(JSON.stringify(sent),/secret-value/);
  v.rerender(<McpPanel {...props} problem="保存被拒绝"/>);
  await pause(35);await key('\x1b');assert.match(v.lastFrame()!,/未保存/);
  assert.doesNotMatch(v.lastFrame()!,/secret-value/);
 });
}
test('MCP successful save clears sensitive draft and selects saved server',async()=>{
 let closed=0;
 const props={overview,loading:false,problem:'',active:true,busy:false,columns:80,rows:24,
 palette:getPalette('dark'),onRequest:()=>{},onClose:()=>{closed++;}};
 const v=render(<McpPanel {...props}/>);
 const key=async(s:string)=>{v.stdin.write(s);await pause(30);};
 await pause(40);for(let i=0;i<3;i++)await key('\t');
 await key('\r');await key('{}');await key('\x1b');await key('\t');await key('\r');
 v.rerender(<McpPanel {...props} overview={{...overview,message:'已保存 MCP 配置',revision:'b'.repeat(64)}}/>);
 await pause(35);await key('\x1b');assert.equal(closed,1);
});

test('MCP scoped controls never silently target all services and advanced groups preserve drafts',async()=>{
 const sent:any[]=[];
 const props={overview:{...overview,servers:[{...overview.servers[0]!,configured:true,changed:true}]},
 loading:false,problem:'',active:true,busy:false,columns:80,rows:24,palette:getPalette('dark'),
 onRequest:(args:unknown)=>sent.push(args),onClose:()=>{}};
 const v=render(<McpPanel {...props}/>);
 const key=async(s:string)=>{v.stdin.write(s);await pause(30);};
 await pause(40);
 assert.match(v.lastFrame()!,/待重载/);
 for(let i=0;i<6;i++)await key('\t');
 await key('\r');assert.deepEqual(sent.pop(),{action:'reload',name:'demo'});
 await key('a');await key('\t');await key('\r');await key('\x15');await key('15');await key('\r');
 await key('a');await key('a');
 for(let i=0;i<7;i++)await key('\t');
 await key('\r');
 assert.equal(sent.at(-1).advanced.timeout,15);
 assert.equal(sent.at(-1).name,'demo');
});
for(const [columns,rows] of [[32,12],[80,24],[120,40]]){
 test('advanced MCP fields fit '+columns+'x'+rows,async()=>{
  const v=render(<McpPanel overview={overview} loading={false} problem="" active busy={false}
   columns={columns!} rows={rows!} palette={getPalette('dark')} onRequest={()=>{}} onClose={()=>{}}/>);
  await pause(40);v.stdin.write('a');await pause(40);
  assert.match(v.lastFrame()!,/高级/);
  assert.equal(v.lastFrame()!.split('\n').length,rows);
  assert.ok(v.lastFrame()!.split('\n').every(l=>stringWidth(l)<=columns!));
 });
}
test('MCP protocol validates advanced fields and explicit service names',()=>{
 assert.doesNotThrow(()=>validateMcpArgs({action:'reload',name:'demo'}));
 assert.throws(()=>validateMcpArgs({action:'disconnect',name:''}));
 assert.throws(()=>validateMcpArgs({action:'save',name:'demo',command:'python',args:'',env:'',revision:'a'.repeat(64),
 advanced:{cwd:'',timeout:-1,risk:'read',allowed_tools:[],tool_risks:{},allowed_origins:[],browser_actions:false}}));
});
