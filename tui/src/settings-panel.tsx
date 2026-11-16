import {goalFields} from './goal-budget.js';
import React,{useCallback,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {Box,Text,measureElement,useInput,type DOMElement,type Key} from 'ink';
import {fitText,textLines} from './markdown.js';
import {ReadingPane} from './workbench.js';
import {ActionButtons,type ActionTarget} from './action-buttons.js';
import {useTranscriptMouse} from './use-transcript-scroll.js';
import type {Palette} from './theme.js';
import {limitFields,validateSettingsArgs,type SettingsOverview,type SettingsDraft,type SettingsAction} from './settings.js';

const runLabels=['步数（0 不限）','时长（秒，必须大于 0）','token（0 不限）','估算费用（美元，0 不限）'];
const actions=['save','apply','refresh','model','permissions','back'] as const;
type Action=typeof actions[number];
type Draft=Record<string,string>;
type View='home'|'budget'|'more';
type HomeCategory='model'|'budget'|'mcp'|'permissions'|'more';

export function SettingsPanel({overview,loading,problem,active,busy,columns,rows,palette,onRequest,onClose,onNavigate,modelLabel}:{
 overview:SettingsOverview|null;loading:boolean;problem:string;active:boolean;busy:boolean;columns:number;rows:number;palette:Palette;
 onRequest:(action:SettingsAction|'list',draft:SettingsDraft)=>void;onClose:()=>void;onNavigate:(target:'model'|'permissions'|'mcp')=>void;modelLabel?:string;
}){
 const [view,setView]=useState<View>('home');
 const wasActive=useRef(active);
 const [category,setCategory]=useState(0);
 const [homeConfirm,setHomeConfirm]=useState(false);
 const [group,setGroup]=useState<'run'|'goal'>('run');
 const [drafts,setDrafts]=useState<Partial<Record<'run'|'goal',Draft>>>({});
 const draft=drafts[group];
 const fields=group==='goal'?goalFields:limitFields;
 const labels=group==='goal'?['总段数（至少1段）','总时长（秒，至少60）','总token（0不限）','估算总费用（美元，0不限）']:runLabels;
 const currentLimits=(group==='goal'?overview?.goal_limits:overview?.limits) as Record<string,number>|undefined;
 const sources=group==='goal'?overview?.goal_sources:overview?.sources;
 const [index,setIndex]=useState(0),[editing,setEditing]=useState(false),[top,setTop]=useState(0);
 const [validation,setValidation]=useState(''),[confirm,setConfirm]=useState(false),[saved,setSaved]=useState<Partial<Record<'run'|'goal',string>>>({});
 const pendingSave=useRef<{group:'run'|'goal';signature:string}|null>(null);
 const value=draft??Object.fromEntries(fields.map(k=>[k,String(currentLimits?.[k]??'')])) as Draft;
 const signature=JSON.stringify(value);
 const dirty=Object.entries(drafts).some(([key,d])=>JSON.stringify(d)!==saved[key as 'run'|'goal']);
 useEffect(()=>{
  if(overview?.message.startsWith('已保存')&&pendingSave.current){const done=pendingSave.current;setSaved(prior=>({...prior,[done.group]:done.signature}));pendingSave.current=null;}
 },[overview]);
 useEffect(()=>{
  const returning=active&&!wasActive.current;
  wasActive.current=active;
  if(returning&&view==='home'&&overview)onRequest('list',{limits:overview.limits});
 },[active,view,overview,onRequest]);
 const currentField=fields[Math.min(3,index)]!;
 const parse=():SettingsDraft|null=>{
  if(!overview)return null;
  try{
   if(fields.some(k=>!value[k].trim()||!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(value[k].trim())))throw new Error('请输入非负数字；不可留空');
   const parsed={limits:Object.fromEntries(fields.map(k=>[k,Number(value[k])])) as SettingsDraft['limits'],revision:overview.revision,...(group==='goal'?{scope:'goal' as const}:{})};
   validateSettingsArgs({action:'apply',...parsed});setValidation('');return parsed;
  }catch(error){setValidation(error instanceof Error?error.message:'限制无效');return null;}
 };
 const close=()=>{
  if(dirty&&!homeConfirm){setHomeConfirm(true);return;}
  setHomeConfirm(false);onClose();
 };
 const open=(target:HomeCategory)=>{
  if(target==='model'||target==='mcp'||target==='permissions'){onNavigate(target);return;}
  setView(target);setTop(0);setValidation('');setConfirm(false);
 };
 const act=(action:Action)=>{
  if(!active||loading)return;
  if(action==='back'){
   if(dirty&&!confirm&&view==='home'){setHomeConfirm(true);return;}
   setView('home');setConfirm(false);setHomeConfirm(false);return;
  }
  if(action==='model'||action==='permissions'){onNavigate(action);return;}
  if(action==='refresh'){if(overview)onRequest('list',{limits:overview.limits});return;}
  if(busy)return;
  const parsed=parse();if(!parsed)return;
  if(action==='save')pendingSave.current={group,signature};
  setConfirm(false);onRequest(action,parsed);
 };
 const change=(text:string)=>{setDrafts(prior=>({...prior,[group]:{...value,[currentField]:text}}));setConfirm(false);};
 const categories:{key:HomeCategory;title:string;summary:(o:SettingsOverview)=>string;warning?:(o:SettingsOverview)=>boolean}[]=[
  {key:'model',title:'模型连接',summary:()=>`${modelLabel??'当前模型、来源与检测'}；打开模型设置`},
  {key:'budget',title:'任务预算',summary:o=>`单段 ${o.limits.max_steps} 步 · ${o.limits.max_run_seconds}s；目标 ${o.goal_limits?.max_segments ?? '—'} 段`},
  {key:'mcp',title:'外部工具',summary:o=>`已配置 ${o.advanced.mcp_servers} 个 · 已连接 ${o.advanced.mcp_connected} 个`,
    warning:o=>o.advanced.mcp_servers>0&&o.advanced.mcp_connected===0},
  {key:'permissions',title:'权限与安全',summary:()=>'查看审批方式、授权来源与撤销'},
  {key:'more',title:'更多设置',summary:o=>`沙箱${o.advanced.sandbox?'开':'关'} · 通知${o.advanced.notifications?'开':'关'} · 计价${o.advanced.priced?'开':'关'}`,
    warning:o=>!o.advanced.priced},
 ];
 const handler=useRef<(input:string,key:Key)=>void>(()=>{});
 handler.current=(input,key)=>{
  if(!active||key.eventType==='release')return;
  if(view==='home'){
   if(key.escape){close();return;}
   if(key.upArrow){setCategory(index=>(index+categories.length-1)%categories.length);setHomeConfirm(false);return;}
   if(key.downArrow){setCategory(index=>(index+1)%categories.length);setHomeConfirm(false);return;}
   if(key.return&&overview){open(categories[category]!.key);return;}
   if(!key.ctrl&&!key.meta&&input==='r'&&overview){onRequest('list',{limits:overview.limits});return;}
   return;
  }
  if(view==='more'){
   if(key.escape){setView('home');return;}
   if(key.upArrow||key.downArrow){setTop(n=>Math.max(0,n+(key.upArrow?-1:1)));return;}
   return;
  }
  if(key.escape){if(editing){setEditing(false);parse();}else if(confirm)setConfirm(false);else act('back');return;}
  if(editing){
   if(key.ctrl&&input==='u'){change('');return;}
   if(key.ctrl||key.meta)return;
   if(key.return||key.tab){setEditing(false);parse();if(key.tab)setIndex((index+(key.shift?9:1))%10);return;}
   if(key.backspace||key.delete){change(value[currentField].slice(0,-1));return;}
   if(!key.upArrow&&!key.downArrow&&!key.leftArrow&&!key.rightArrow&&input)change((value[currentField]+input.replace(/[\x00-\x1f\x7f]/g,'')).slice(0,32));
   return;
  }
  if(key.ctrl||key.meta)return;
  if(input==='m'&&!loading){onNavigate('mcp');return;}
  if(input==='g'&&overview?.goal_limits&&!loading){setGroup(group==='run'?'goal':'run');setIndex(0);setTop(0);setValidation('');setConfirm(false);return;}
  if(key.tab||key.leftArrow||key.rightArrow){setConfirm(false);setIndex((index+(key.shift||key.leftArrow?9:1))%10);return;}
  if(key.upArrow||key.downArrow){setTop(n=>Math.max(0,n+(key.upArrow?-1:1)));return;}
  if(key.return){if(index<4){if(overview)setEditing(true);}else act(actions[index-4]!);}
 };
 useInput(useCallback((input,key)=>handler.current(input,key),[]));
 const targets=useRef<ActionTarget<Action>[]>([]), navTargets=useRef<ActionTarget<Action>[]>([]),press=useRef<Action|null>(null);
 const categoryElements=useRef(new Map<HomeCategory,DOMElement>());
 const categoryTargets=useRef<ActionTarget<HomeCategory>[]>([]);
 const categoryPress=useRef<HomeCategory|null>(null);
 useLayoutEffect(()=>{
  categoryTargets.current=[...categoryElements.current.entries()].flatMap(([action,element])=>{
   const {x,y,width,height}=measureElement(element);
   return [{action,top:y+1,bottom:y+height,left:x+1,right:x+width}];
  });
 });
 useTranscriptMouse({enabled:active&&(view==='budget'||view==='home'),top:5,bottom:rows-4,left:1,right:columns,onScroll:delta=>setTop(n=>Math.max(0,n+delta)),onPointer:event=>{
  if(view==='home'){
   const hit=categoryTargets.current.find(t=>event.row>=t.top&&event.row<=t.bottom&&event.column>=t.left&&event.column<=t.right);
   if(event.type==='press'&&event.button===0)categoryPress.current=hit?.action??null;
   else if(event.type==='release'){
    const prior=categoryPress.current;categoryPress.current=null;
    if(hit&&prior===hit.action){const position=categories.findIndex(item=>item.key===hit.action);setCategory(position);open(hit.action);}
   } else categoryPress.current=null;
   return Boolean(hit);
  }
  const hit=[...targets.current,...navTargets.current].find(t=>event.row>=t.top&&event.row<=t.bottom&&event.column>=t.left&&event.column<=t.right);
  if(event.type==='press'&&event.button===0)press.current=hit?.action??null;
  else if(event.type==='release'){const prior=press.current;press.current=null;if(hit&&prior===hit.action){setIndex(actions.indexOf(hit.action)+4);act(hit.action);}}
  return Boolean(hit);
 }});
 const buttons=actions.map((action,i)=>({action,label:['保存','应用','刷新','模型','权限','返回中心'][i]!}));
 const a=overview?.advanced;
 const budgetDetails=overview?[
  (group==='goal'?'新目标总预算：':'单段默认限制：')+fields.map((k,i)=>labels[i]+' '+currentLimits?.[k]+'（'+sources?.[k]+'）').join('；'),
  'Tab 选择字段，Enter 编辑，Ctrl+U 清空；↑↓滚动说明。保存写入配置；应用仅改变后续新任务，不写文件。',
  group==='goal'?'目标预算在每段开始前检查，已开始的一段仍受单段限制；总时长包含暂停和审批等待。达到上限后手动恢复也不会重置。':
    '旧任务续做沿用原限制；计划策略可能进一步收紧。独立探索任务不随本页变化。',
  'g 切换单段 / 目标预算，两组草稿分别保留。目标转入后台仍使用任务快照与累计用量；应用仅影响新目标。',
  '保存位置：'+overview.path+'；CLI/进程环境仍优先。刷新核对来源，保留当前编辑。',
  'Esc 返回设置中心；在中心再按 Esc 关闭设置。',
 ].join('\n'):'正在读取设置…';
 const moreDetails=overview?[
  '这些项来自 .env、CLI 或启动参数；修改后重启相关进程，本轮不提供编辑器。',
  '沙箱执行：'+(a!.sandbox?'启用':'未启用')+'；沙箱网络：'+(a!.sandbox_network?'允许':'默认拒绝')+'。',
  '通知 Webhook：'+(a!.notifications?'已配置':'未配置')+'；凭据只在环境配置中管理。',
  a!.priced?'模型计价：已配置输入/输出单价；费用限制按估算值提示。':'模型计价：未配置单价；费用限制不能提供硬保护，可先设置 token 上限。',
 ].join('\n'):'正在读取设置…';
 const homeDetails=overview?[
  categories[category]!.title+'：'+categories[category]!.summary(overview),
  '设置分类按用户目标组织；数字参数只在“任务预算”里编辑。',
  '模型与权限在各自面板中处理；返回后仍回到设置中心。',
  'Esc 逐层返回；有未保存编辑时会先确认。',
 ].join('\n'):'正在读取设置…';
 const selected=overview&&categories[category]?.warning?.(overview);
 const paneRows=Math.max(0,rows-(view==='budget'?8:view==='home'?11:6));
 const lines=textLines(view==='budget'?budgetDetails:view==='more'?moreDetails:homeDetails,Math.max(1,columns-5));
 const message=view==='home'
  ? (homeConfirm?'有未保存编辑；再次按 Esc 将丢弃并关闭':problem||overview?.message||'↑↓选择分类，Enter 打开，Esc 关闭')
  : view==='more'
    ? (problem||'只读状态；Esc 返回设置中心')
    : (confirm?'有未保存编辑；再次返回将丢弃':loading?'操作中；如有审批请先处理':validation||problem||overview?.message||(busy?'任务运行中：可查看，空闲后保存或应用':'保存与应用分别生效；旧任务限制保持原样'));
 const title=view==='home'?'设置中心':view==='more'?'设置中心 · 更多设置':'设置中心 · '+(group==='goal'?'目标总预算':'单段限制');
 const hint=view==='home'?(columns<48?'↑↓ Enter Esc':'↑↓ 选择 · Enter 打开 · Esc 关闭 · r 刷新')
  :view==='more'?'Esc 返回设置中心 · ↑↓ 滚动说明'
   :(overview?.goal_limits?'g切组 m:MCP Esc返回中心':'Tab换项 Enter确认 Esc返回中心');
 return <Box width={columns} height={rows} flexDirection="column" overflow="hidden" backgroundColor={palette.background}>
  <Text bold color={palette.foreground}>{fitText(title,columns)}</Text>
  {view==='home'?<>
   <Text color={palette.secondary}>{fitText('按目的选择分类；模型与权限保留原面板。',columns)}</Text>
   {overview?categories.map((item,position)=><Box key={item.key} height={1} ref={element=>{if(element)categoryElements.current.set(item.key,element);else categoryElements.current.delete(item.key);}}>
    <Text color={position===category?(selected?palette.warning:palette.accent):palette.foreground}>
     {fitText((position===category?'› ':'  ')+item.title+'  '+item.summary(overview),columns)}</Text></Box>)
    :<Text color={palette.secondary}>{fitText('正在读取设置…',columns)}</Text>}
   <Box height={1}/>
  </>:view==='more'?<>
   <Text color={palette.secondary}>{fitText('通知、沙箱与计价等需要重启的项',columns)}</Text>
   <Box height={1}/>
  </>:<>
   <Text color={palette.accent}>{fitText((editing?'编辑中 · ':'')+(index<4?labels[index]:'操作 · '+buttons[index-4]!.label),columns)}</Text>
   <Text color={palette.foreground}>{fitText(index<4?value[currentField]:'Tab 选择字段或操作',columns)}</Text>
  </>}
  {paneRows>=3?<ReadingPane title={view==='more'?'运行范围':view==='home'?'分类说明':'限制与高级设置'} lines={lines}
   top={Math.min(top,Math.max(0,lines.length-paneRows+3))} height={paneRows} columns={columns} palette={palette}/>:null}
  {view==='budget'?<>
   <ActionButtons buttons={buttons.slice(0,3)} selected={index>=4?actions[index-4]!:null} columns={columns} height={1} palette={palette} targets={targets}/>
   <ActionButtons buttons={buttons.slice(3)} selected={index>=4?actions[index-4]!:null} columns={columns} height={1} palette={palette} targets={navTargets}/>
  </>:null}
  <Box height={2}><Text color={validation||problem||confirm||homeConfirm||selected?palette.warning:palette.secondary}>{textLines(message,columns).slice(0,2).map(l=>l.spans.map(s=>s.text).join('')).join('\n')}</Text></Box>
  <Text color={palette.secondary}>{fitText(hint,columns)}</Text>
 </Box>;
}
