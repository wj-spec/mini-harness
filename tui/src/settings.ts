import {validGoalLimits,goalFields,type GoalLimits} from './goal-budget.js';
export const limitFields = ['max_steps','max_run_seconds','max_run_tokens','max_run_cost_usd'] as const;
export type Limits = Record<typeof limitFields[number],number>;
export type SettingsDraft = {limits:Limits|GoalLimits; scope?:'goal'; revision?:string};
export type SettingsAction = 'save'|'apply';
export type SettingsArgs = {action:'list'} | (SettingsDraft & {action:SettingsAction});
export type SettingsOverview = {goal_limits?:GoalLimits; goal_sources?:Record<string,string>; limits:Limits; sources:Record<string,string>; path:string; revision:string;
 advanced:{mcp_servers:number;mcp_connected:number;sandbox:boolean;sandbox_network:boolean;notifications:boolean;priced:boolean};
 message:string; workspace:string; session_id:string};
export function validateSettingsArgs(v:unknown): asserts v is SettingsArgs {
 if(!v||typeof v!=='object'||Array.isArray(v))throw new Error('设置参数无效');
 const x=v as Record<string,unknown>;
 if(x.action==='list'&&Object.keys(x).length===1)return;
 if(!['save','apply'].includes(String(x.action))||Object.keys(x).some(k=>!['action','limits','revision','scope'].includes(k)))throw new Error('设置参数无效');
 const limits=x.limits as Record<string,unknown>|undefined;
 if(!limits||typeof limits!=='object'||Array.isArray(limits)||Object.keys(limits).length!==4)throw new Error('请填写四项限制');
 if(x.scope!==undefined&&x.scope!=='goal')throw new Error('设置分组无效');
 if(x.scope==='goal'){
  if(!validGoalLimits(limits))throw new Error('总段数须为正整数，时长至少60秒，token须为非负整数');
 }else {
 for(const k of limitFields) {
  const n=limits[k];
  if(typeof n!=='number'||!Number.isFinite(n)||n<0||n>1e12)throw new Error('限制须为 0 至 10¹² 的数字');
  if((k==='max_steps'||k==='max_run_tokens')&&!Number.isInteger(n))throw new Error('步数和 token 必须为整数');
 }
 if(Number(limits.max_run_seconds)<=0)throw new Error('时长必须大于 0');
 }
 if((x.action==='save'||x.revision!==undefined)&&!(typeof x.revision==='string'&&/^[a-f0-9]{64}$/.test(x.revision)))throw new Error('请先刷新配置版本');
}
export function validSettingsOverview(v:unknown):v is SettingsOverview {
 if(!v||typeof v!=='object'||Array.isArray(v))return false;
 const x=v as SettingsOverview;
 try{validateSettingsArgs({action:'apply',limits:x.limits,revision:x.revision});}catch{return false;}
 if(x.goal_limits!==undefined&&(!validGoalLimits(x.goal_limits)||!x.goal_sources||!goalFields.every(k=>typeof x.goal_sources![k]==='string')))return false;
 const a=x.advanced;
 return ['path','revision','message','workspace','session_id'].every(k=>typeof (v as Record<string,unknown>)[k]==='string')&&
  /^[a-f0-9]{64}$/.test(x.revision)&&Boolean(x.sources&&limitFields.every(k=>typeof x.sources[k]==='string'))&&
  Boolean(a&&typeof a==='object'&&Object.keys(a).length===6&&
   ['mcp_servers','mcp_connected'].every(k=>Number.isSafeInteger(a[k as keyof typeof a])&&Number(a[k as keyof typeof a])>=0)&&
   ['sandbox','sandbox_network','notifications','priced'].every(k=>typeof a[k as keyof typeof a]==='boolean'));
}
