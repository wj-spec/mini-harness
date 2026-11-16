export type McpAdvanced={cwd:string;timeout:number;risk:'read'|'write'|'exec';allowed_tools:string[];
 tool_risks:Record<string,'read'|'write'|'exec'>;allowed_origins:string[];browser_actions:boolean};
export const advancedFields=['cwd','timeout','risk','allowed_tools','tool_risks','allowed_origins','browser_actions'] as const;
export const defaultAdvanced=():McpAdvanced=>({cwd:'',timeout:60,risk:'exec',allowed_tools:[],tool_risks:{},allowed_origins:[],browser_actions:false});
export type McpDraft={name:string;command:string;args:string;env:string;revision:string;advanced?:McpAdvanced};
export type McpArgs={action:'list'}|{action:'reload'|'disconnect';name?:string}|({action:'save'}&McpDraft)|{action:'delete';name:string;revision:string};
export type McpServerView={name:string;command:string;args_count:number;env_keys:string[];connected:boolean;
 configured?:boolean;changed?:boolean;advanced?:McpAdvanced};
export type McpOverview={path:string;revision:string;source:string;editable:boolean;servers:McpServerView[];
 message:string;workspace:string;session_id:string};
const text=(v:unknown,max=4096):v is string=>typeof v==='string'&&v.length<=max&&!/[\x00-\x1f\x7f]/.test(v);
function validAdvanced(v:unknown):v is McpAdvanced{
 if(!v||typeof v!=='object'||Array.isArray(v))return false;
 const a=v as Record<string,unknown>;
 return Object.keys(a).length===advancedFields.length&&advancedFields.every(k=>k in a)&&
 text(a.cwd)&&typeof a.timeout==='number'&&Number.isFinite(a.timeout)&&a.timeout>0&&a.timeout<=86400&&
 ['read','write','exec'].includes(String(a.risk))&&typeof a.browser_actions==='boolean'&&
 Array.isArray(a.allowed_tools)&&a.allowed_tools.every(t=>text(t)&&t.trim())&&
 Array.isArray(a.allowed_origins)&&a.allowed_origins.every(t=>text(t)&&t.trim())&&
 Boolean(a.tool_risks&&typeof a.tool_risks==='object'&&!Array.isArray(a.tool_risks)&&
 Object.entries(a.tool_risks).every(([k,v])=>text(k)&&k.trim()&&['read','write','exec'].includes(String(v))));
}
export function validMcpOverview(v:unknown):v is McpOverview{
 if(!v||typeof v!=='object'||Array.isArray(v))return false;
 const x=v as Record<string,unknown>;
 return Object.keys(x).every(k=>['v','type','id','path','revision','source','message','workspace','session_id','editable','servers'].includes(k))&&
 ['path','revision','source','message','workspace','session_id'].every(k=>text(x[k],8192))&&
 /^[a-f0-9]{64}$/.test(String(x.revision))&&typeof x.editable==='boolean'&&
 Array.isArray(x.servers)&&x.servers.every((s:unknown)=>{
  if(!s||typeof s!=='object'||Array.isArray(s))return false;
  const y=s as Record<string,unknown>;
  return Object.keys(y).every(k=>['name','command','args_count','env_keys','connected','configured','changed','advanced'].includes(k))&&
   text(y.name)&&text(y.command)&&Number.isSafeInteger(y.args_count)&&Number(y.args_count)>=0&&
   Array.isArray(y.env_keys)&&y.env_keys.every(k=>text(k))&&typeof y.connected==='boolean'&&
   (y.configured===undefined||typeof y.configured==='boolean')&&(y.changed===undefined||typeof y.changed==='boolean')&&
   (y.advanced===undefined||validAdvanced(y.advanced));
 });
}
export function validateMcpArgs(v:unknown):asserts v is McpArgs{
 if(!v||typeof v!=='object'||Array.isArray(v))throw new Error('MCP 参数无效');
 const x=v as Record<string,unknown>;
 if(x.action==='list'&&Object.keys(x).length===1)return;
 if(['reload','disconnect'].includes(String(x.action))&&Object.keys(x).every(k=>['action','name'].includes(k))&&
  (!('name' in x)||(text(x.name)&&Boolean(x.name.trim()))))return;
 const keys=x.action==='save'?['action','name','command','args','env','revision',...('advanced' in x?['advanced']:[])]:['action','name','revision'];
 if(!['save','delete'].includes(String(x.action))||Object.keys(x).length!==keys.length||Object.keys(x).some(k=>!keys.includes(k))||
 !text(x.name)||!x.name.trim()||!text(x.revision)||!/^[a-f0-9]{64}$/.test(x.revision)||
 (x.action==='save'&&(!text(x.command)||!x.command.trim()||!text(x.args,32768)||!text(x.env,32768))))throw new Error('请核对服务名称、命令和配置格式');
 if('advanced' in x&&!validAdvanced(x.advanced))throw new Error('高级配置无效；超时需大于 0，风险为 read/write/exec，列表/映射需为 JSON');
 if(x.action==='save'){
  try{
   const args=x.args?JSON.parse(String(x.args)):[],env=x.env?JSON.parse(String(x.env)):{};
   if(!Array.isArray(args)||!args.every(a=>typeof a==='string')||!env||typeof env!=='object'||Array.isArray(env)||
    !Object.values(env).every(e=>typeof e==='string'))throw new Error();
  }catch{throw new Error('参数需为 JSON 字符串数组，环境需为 JSON 字符串对象');}
 }
}
