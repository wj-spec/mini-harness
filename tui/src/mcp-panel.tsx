import React,{useCallback,useEffect,useRef,useState} from 'react';
import {Box,Text,useInput,type Key} from 'ink';
import {fitText,textLines} from './markdown.js';
import {ReadingPane} from './workbench.js';
import {ActionButtons,type ActionTarget} from './action-buttons.js';
import {useTranscriptMouse} from './use-transcript-scroll.js';
import type {Palette} from './theme.js';
import {validateMcpArgs,advancedFields,defaultAdvanced,type McpAdvanced,type McpArgs,type McpDraft,type McpOverview} from './mcp-settings.js';
const basicFields=['name','command','args','env'] as const;
const basicLabels=['服务名称（已有服务不可改名）','启动命令（例如 npx 或 python）','参数 JSON（隐藏；空白保留）','环境 JSON（隐藏；空白保留）'];
const actions=['save','delete','reload','disconnect','next','back'] as const;
type Action=typeof actions[number];
export function McpPanel({overview,loading,problem,active,busy,columns,rows,palette,onRequest,onClose}:{
 overview:McpOverview|null;loading:boolean;problem:string;active:boolean;busy:boolean;columns:number;rows:number;palette:Palette;
 onRequest:(args:McpArgs)=>void;onClose:()=>void;
}){
 const [advanced,setAdvanced]=useState(false),[scope,setScope]=useState<'selected'|'all'>('selected');
 const [advancedDraft,setAdvancedDraft]=useState<Record<string,string>|null>(null);
 const fields=advanced?advancedFields:basicFields;
 const labels=advanced?['工作目录（空白为工作区）','工具超时（秒）','默认风险 read / write / exec','工具白名单 JSON（[] 为全部）','逐工具风险 JSON（{} 沿用默认）','允许来源 JSON（[] 不限制）','受控浏览器 true / false']:basicLabels;
 const count=fields.length,total=count+actions.length;
 const [selected,setSelected]=useState<string|null>(null),[draft,setDraft]=useState<McpDraft|null>(null);
 const [index,setIndex]=useState(0),[editing,setEditing]=useState(false),[top,setTop]=useState(0);
 const [confirm,setConfirm]=useState<Action|null>(null),[validation,setValidation]=useState('');
 const pending=useRef<{action:'save'|'delete';name:string}|null>(null);
 const name=selected??overview?.servers[0]?.name??'';
 const server=overview?.servers.find(s=>s.name===name);
 const value=draft??{name:server?.name??'',command:server?.command??'',args:'',env:'',revision:overview?.revision??''};
 useEffect(()=>{
  if(overview?.message.startsWith('已保存')&&pending.current){
   setSelected(pending.current.action==='save'?pending.current.name:'');setDraft(null);setAdvancedDraft(null);setConfirm(null);setValidation('');
   pending.current=null;
  }
 },[overview]);
 const field=fields[Math.min(index,count-1)]!;
 const defaults=server?.advanced??defaultAdvanced();
 const advancedValue=advancedDraft??Object.fromEntries(advancedFields.map(k=>[k,typeof defaults[k]==='string'?defaults[k]:JSON.stringify(defaults[k])]));
 const displayed:Record<string,string>=advanced?advancedValue:{name:value.name,command:value.command,args:value.args,env:value.env};
 const dirty=Boolean(draft||advancedDraft);
 const editable=overview?.editable&&server?.configured!==false;
 const change=(text:string)=>{
  if(advanced)setAdvancedDraft({...advancedValue,[field]:text});
  else setDraft({...value,[field]:text});
  setConfirm(null);setValidation('');
 };
 const parsedAdvanced=():McpAdvanced=>Object.fromEntries(advancedFields.map(k=>[k,k==='cwd'||k==='risk'?advancedValue[k]:JSON.parse(advancedValue[k]!)])) as McpAdvanced;
 const act=(action:Action)=>{
  if(!active||loading)return;
  if(action==='back'||action==='next'){
   if(dirty&&confirm!==action){setConfirm(action);return;}
   if(action==='back'){onClose();return;}
   const names=[...(overview?.servers.map(s=>s.name)??[]),''];
   setSelected(names[(names.indexOf(name)+1)%names.length]!);setDraft(null);setAdvancedDraft(null);setConfirm(null);setIndex(0);setTop(0);setValidation('');return;
  }
  if(!overview||busy)return;
  if((action==='save'||action==='delete')&&!editable){setValidation('来源不可编辑或服务已从配置删除；请刷新核对');return;}
  if(action==='delete'&&(!server||server.configured===false)){setValidation('请先选择已保存的服务');return;}
  if((action==='delete'||action==='disconnect')&&confirm!==action){setConfirm(action);return;}
  if(action==='reload'&&dirty){setValidation('存在未保存编辑，请先保存；重载只读取已保存配置');return;}
  if((action==='reload'||action==='disconnect')&&scope==='selected'&&!server){setValidation('请先选择已保存的服务；s 可切换全部范围');return;}
  let request:McpArgs;
  try{
   request=action==='save'?{...value,action,revision:overview.revision,...(advancedDraft?{advanced:parsedAdvanced()}:{} )}:
    action==='delete'?{action,name:server!.name,revision:overview.revision}:
    {...(scope==='selected'?{name:server!.name}:{}),action};
   validateMcpArgs(request);
  }catch{setValidation('配置格式无效；请核对 JSON、正数超时、风险及浏览器开关');return;}
  if(action==='save'||action==='delete')pending.current={action,name:value.name};
  setConfirm(null);setValidation('');onRequest(request);
 };
 const handler=useRef<(input:string,key:Key)=>void>(()=>{});
 handler.current=(input,key)=>{
  if(!active||key.eventType==='release')return;
  if(key.escape){if(editing)setEditing(false);else if(confirm)setConfirm(null);else act('back');return;}
  if(loading)return;
  if(editing){
   if(key.ctrl&&input==='u'){change('');return;}if(key.ctrl||key.meta)return;
   if(key.return||key.tab){setEditing(false);if(key.tab)setIndex((index+(key.shift?total-1:1))%total);return;}
   if(key.backspace||key.delete){change(Array.from(displayed[field]!).slice(0,-1).join(''));return;}
   if(!key.upArrow&&!key.downArrow&&!key.leftArrow&&!key.rightArrow&&input)change((displayed[field]+input.replace(/[\x00-\x1f\x7f]/g,'')).slice(0,field==='args'||field==='env'?32768:4096));
   return;
  }
  if(key.ctrl||key.meta)return;
  if(input==='a'){setAdvanced(!advanced);setIndex(0);setTop(0);setConfirm(null);return;}
  if(input==='s'){setScope(scope==='selected'?'all':'selected');setConfirm(null);return;}
  if(input==='r'){onRequest({action:'list'});return;}
  if(key.tab||key.leftArrow||key.rightArrow){setConfirm(null);setIndex((index+(key.shift||key.leftArrow?total-1:1))%total);return;}
  if(key.upArrow||key.downArrow){setTop(n=>Math.max(0,n+(key.upArrow?-1:1)));return;}
  if(key.return){
   if(index<count){if(editable&&!(!advanced&&index===0&&server))setEditing(true);}
   else act(actions[index-count]!);
  }
 };
 useInput(useCallback((input,key)=>handler.current(input,key),[]));
 const targets=useRef<ActionTarget<Action>[]>([]),navTargets=useRef<ActionTarget<Action>[]>([]),press=useRef<Action|null>(null);
 useTranscriptMouse({enabled:active,top:5,bottom:rows-4,left:1,right:columns,onScroll:delta=>setTop(n=>Math.max(0,n+delta)),onPointer:event=>{
  const hit=[...targets.current,...navTargets.current].find(t=>event.row>=t.top&&event.row<=t.bottom&&event.column>=t.left&&event.column<=t.right);
  if(event.type==='press'&&event.button===0)press.current=hit?.action??null;
  else if(event.type==='release'){const prior=press.current;press.current=null;if(hit&&hit.action===prior){setIndex(actions.indexOf(hit.action)+count);act(hit.action);}}
  return Boolean(hit);
 }});
 const buttons=actions.map((action,i)=>({action,label:['保存','删除','重载','断开','切服务','返回'][i]!}));
 const details=overview?[
  '选择：'+(server?server.name+' · '+(server.configured===false?'已删除，仍有连接':server.changed?'待重载':server.connected?'已连接':'未连接'):'新增服务')+'；切服务依次选择已有服务和新增。',
  '操作范围：'+(scope==='selected'?'当前服务 '+(server?.name||'尚未选择'):'全部前台 MCP 服务')+'；s 切换范围，a 切换基础 / 高级。',
  '来源：'+overview.source+'；'+(overview.editable?'可编辑':'只读')+'；保存位置：'+overview.path,
  '服务列表：'+(overview.servers.map(s=>s.name+'（'+(s.connected?'已连接':'未连接')+'）').join('、')||'尚无服务'),
  'Tab 选择字段 / 按钮，Enter 编辑，Ctrl+U 清空；r 刷新保留编辑，↑↓滚动说明。',
  '参数示例：["-y","包名"]；环境示例：{"API_KEY":"你的密钥"}。两项均隐藏；已有值不回传，留空保留；[] / {} 清空。',
  '当前参数 '+(server?.args_count??0)+' 个；环境键：'+(server?.env_keys.join('、')||'无'),
  '高级项：cwd 工作目录，timeout 超时秒数，risk 为 read/write/exec；白名单/来源为 JSON 数组，逐工具风险为 JSON 对象，浏览器开关为 true/false。更换工作目录也需重填已有环境。',
  '保存或删除只改配置；重载/断开按显示的范围执行，失败保留旧工具。其他服务及后台进程不变。浏览器控制需要本地来源和受限工具白名单。',
  '首次配置保存到 .env。进程环境覆盖需移除并重启；启动来源路径变化需重启。',
 ].join('\n'):'正在读取 MCP 配置…';
 const paneRows=Math.max(3,rows-8),lines=textLines(details,Math.max(1,columns-5));
 const shown=field==='args'||field==='env'?(displayed[field]?'已填写（隐藏）':'留空保留已有值；新服务默认为空'):displayed[field];
 const message=confirm==='delete'?'再次删除确认；仅删配置，重载才断开该服务':confirm==='disconnect'?'再次断开确认：'+(scope==='selected'?server?.name:'全部前台服务'):
  confirm?'有未保存编辑；再次操作将丢弃':loading?'操作中；如有审批请先处理':validation||problem||overview?.message||
  (busy?'任务运行中：可编辑，空闲后操作':'保存后重载才连接；r 刷新');
 return <Box width={columns} height={rows} flexDirection="column" overflow="hidden" backgroundColor={palette.background}>
  <Text bold color={palette.foreground}>{fitText('MCP 设置 · '+(advanced?'高级':'基础')+' · '+(scope==='selected'?'当前':'全部'),columns)}</Text>
  <Text color={palette.accent}>{fitText((editing?'编辑中 · ':'')+(index<count?labels[index]:'操作 · '+buttons[index-count]!.label),columns)}</Text>
  <Text color={palette.foreground}>{fitText(index<count?shown:'Tab 选择字段或操作',columns)}</Text>
  <ReadingPane title="配置与连接" lines={lines} top={Math.min(top,Math.max(0,lines.length-paneRows+3))} height={paneRows} columns={columns} palette={palette}/>
  <ActionButtons buttons={buttons.slice(0,3)} selected={index>=count?actions[index-count]!:null} columns={columns} height={1} palette={palette} targets={targets}/>
  <ActionButtons buttons={buttons.slice(3)} selected={index>=count?actions[index-count]!:null} columns={columns} height={1} palette={palette} targets={navTargets}/>
  <Box height={2}><Text color={validation||problem||confirm?palette.warning:palette.secondary}>{textLines(message,columns).slice(0,2).map(l=>l.spans.map(s=>s.text).join('')).join('\n')}</Text></Box>
  <Text color={palette.secondary}>{fitText('a高级 s范围 r刷新 Esc返回',columns)}</Text>
 </Box>;
}
