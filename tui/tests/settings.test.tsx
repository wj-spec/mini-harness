import assert from 'node:assert/strict';
import {afterEach,test} from 'node:test';
import {setTimeout as pause} from 'node:timers/promises';
import React from 'react';
import {cleanup,render} from 'ink-testing-library';
import stringWidth from 'string-width';
import {SettingsPanel} from '../src/settings-panel.js';
import {getPalette} from '../src/theme.js';
import {validateSettingsArgs,validSettingsOverview} from '../src/settings.js';
afterEach(cleanup);
export const overview={limits:{max_steps:6,max_run_seconds:100,max_run_tokens:8000,max_run_cost_usd:0},
 sources:{max_steps:'默认值',max_run_seconds:'默认值',max_run_tokens:'默认值',max_run_cost_usd:'默认值'},
 path:'/project/.env',revision:'a'.repeat(64),advanced:{mcp_servers:0,mcp_connected:0,sandbox:false,sandbox_network:false,notifications:false,priced:false},
 message:'',workspace:'/project',session_id:'s'};
test('settings validate bounded numbers and sanitized overview',()=>{
 assert.ok(validSettingsOverview(overview));
 assert.equal(validSettingsOverview({...overview,advanced:{...overview.advanced,webhook:'secret'}}),false);
 for(const limits of [{...overview.limits,max_steps:1.5},{...overview.limits,max_run_seconds:0},{...overview.limits,max_run_tokens:Infinity}]){
   assert.throws(()=>validateSettingsArgs({action:'apply',limits}));
 }
});
for(const [columns,rows] of [[32,12],[80,24],[120,40]]){
 test('limit editor validates and fits '+columns+'x'+rows,async()=>{
  const sent:unknown[]=[];
  const v=render(<SettingsPanel overview={overview} loading={false} problem="" active busy={false}
    columns={columns!} rows={rows!} palette={getPalette('dark')} onRequest={(a,d)=>sent.push({a,d})} onClose={()=>{}} onNavigate={()=>{}}/>);
  const key=async(s:string)=>{v.stdin.write(s);await pause(30);};
  await pause(40);await key('\x1b[B');await key('\r');
  await key('\r');await key('\x15');await key('1.5');await key('\r');
  assert.match(v.lastFrame()!,/整数/);
  for(let i=0;i<4;i++)await key('\t');
  await key('\r');assert.equal(sent.length,0);
  assert.equal(v.lastFrame()!.split('\n').length,rows);
  assert.ok(v.lastFrame()!.split('\n').every(l=>stringWidth(l)<=columns!));
 });
}
test('editor submits parsed limits and navigates without running',async()=>{
 const sent:unknown[]=[];const nav:string[]=[];
 const v=render(<SettingsPanel overview={overview} loading={false} problem="" active busy={false} columns={80} rows={24}
 palette={getPalette('dark')} onRequest={(a,d)=>sent.push({a,d})} onClose={()=>{}} onNavigate={n=>nav.push(n)}/>);
 const key=async(s:string)=>{v.stdin.write(s);await pause(30);};
 await pause(40);await key('\x1b[B');await key('\r');
 await key('\r');await key('\x15');await key('12');await key('\r');
 for(let i=0;i<4;i++)await key('\t');await key('\r');
 assert.match(JSON.stringify(sent),/"max_steps":12/);
 for(let i=0;i<3;i++)await key('\t');await key('\r');
 assert.deepEqual(nav,['model']);
});

test('goal group retains both drafts and scopes save',async()=>{
 const sent:unknown[]=[];
 const props={overview:{...overview,goal_limits:{max_segments:8,max_seconds:14400,max_tokens:0,max_cost_usd:0},
 goal_sources:{max_segments:'默认值',max_seconds:'默认值',max_tokens:'默认值',max_cost_usd:'默认值'}},
 loading:false,problem:'',active:true,busy:false,columns:32,rows:12,palette:getPalette('dark'),onRequest:(a:unknown,d:unknown)=>sent.push({a,d}),onClose:()=>{},onNavigate:()=>{}};
 const v=render(<SettingsPanel {...props}/>);
 const key=async(s:string)=>{v.stdin.write(s);await pause(30);};
 await pause(40);await key('\x1b[B');await key('\r');
 await key('\r');await key('\x15');await key('12');await key('\r');
 await key('g');assert.match(v.lastFrame()!,/目标/);
 await key('\r');await key('\x15');await key('3');await key('\r');
 await key('g');assert.match(v.lastFrame()!,/12/);
 await key('g');assert.match(v.lastFrame()!,/3/);
 for(let i=0;i<4;i++)await key('\t');await key('\r');
 assert.match(JSON.stringify(sent),/"scope":"goal"/);assert.match(JSON.stringify(sent),/"max_segments":3/);
 assert.equal(v.lastFrame()!.split('\n').length,12);
 assert.ok(v.lastFrame()!.split('\n').every(l=>stringWidth(l)<=32));
 await key('\x1b');await key('\x1b');assert.match(v.lastFrame()!,/未保存/);
});

test('returning to the category home keeps the edited budget draft', async () => {
 const sent:unknown[]=[];
 const v=render(<SettingsPanel overview={overview} loading={false} problem="" active busy={false} columns={80} rows={24}
  palette={getPalette('dark')} onRequest={(a,d)=>sent.push({a,d})} onClose={()=>{}} onNavigate={()=>{}}/>);
 const key=async(s:string)=>{v.stdin.write(s);await pause(30);};
 await pause(40);await key('\x1b[B');await key('\r');
 await key('\r');await key('\x15');await key('12');await key('\r');
 await key('\x1b');assert.match(v.lastFrame()!,/设置中心/);
 await key('\r');assert.match(v.lastFrame()!,/12/);
 assert.equal(v.lastFrame()!.split('\n').length,24);
});

test('clicking a settings category row opens that section', async () => {
 const v=render(<SettingsPanel overview={overview} loading={false} problem="" active busy={false} columns={80} rows={24}
  palette={getPalette('dark')} onRequest={()=>{}} onClose={()=>{}} onNavigate={()=>{}}/>);
 const key=async(s:string)=>{v.stdin.write(s);await pause(35);};
 await pause(50);
 const lines=v.lastFrame()!.split('\n');
 const row=lines.findIndex(line=>line.includes('任务预算'));assert.ok(row>=0);
 const column=stringWidth(lines[row]!.slice(0,lines[row]!.indexOf('任务预算')))+2;
 await key(`\x1b[<0;${column};${row+1}M\x1b[<0;${column};${row+1}m`);
 assert.match(v.lastFrame()!,/单段限制/);
});
