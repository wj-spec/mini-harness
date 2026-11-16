import {useEffect,useRef,useState} from 'react';
import type {BackendClient} from './backend.js';
import type {ServerEvent} from './protocol.js';
import {validSettingsOverview,type SettingsOverview,type SettingsAction,type SettingsDraft} from './settings.js';
export function useSettings(client:BackendClient, workspace:string, sessionId:string,
  onStart:(id:string)=>void, canMutate:()=>boolean) {
  const [open,setOpen]=useState(false);
  const [overview,setOverview]=useState<SettingsOverview|null>(null);
  const [problem,setProblem]=useState('');
  const [loading,setLoading]=useState(false);
  const pending=useRef<{id:string;timer:ReturnType<typeof setTimeout>}|null>(null);
  const ids=useRef(new Set<string>());
  const finish=(message='')=>{if(pending.current)clearTimeout(pending.current.timer);pending.current=null;setLoading(false);setProblem(message);};
  useEffect(()=>()=>{if(pending.current)clearTimeout(pending.current.timer);},[client]);
  const request=(action:SettingsAction|'list',draft?:SettingsDraft)=>{
    if(pending.current)return;
    if(!client.capabilities?.().includes('workbench-settings')) {setProblem('当前后端不支持设置中心；请更新后端');return;}
    if(action!=='list'&&!canMutate()){setProblem('任务运行中，请等待空闲后操作；草稿保留');return;}
    try {
      const id=client.command('settings',action==='list'?{action}:{...draft!,action});
      ids.current.add(id); while(ids.current.size>128)ids.current.delete(ids.current.values().next().value!);
      pending.current={id,timer:setTimeout(()=>finish('回执超时；请刷新核对，勿重复保存'),120_000)};
      setLoading(true);setProblem('');
      if(action!=='list')onStart(id);
    } catch {finish('设置操作未送达；请核对连接与输入');}
  };
  return {open,overview,problem,loading,request,
    show:()=>{setOpen(true);request('list');},
    close:()=>{if(!pending.current){setOpen(false);setOverview(null);setProblem('');}},
    receive:(event:ServerEvent)=>{
      if(event.type==='closed'){finish('连接已断开，草稿保留；重启后再操作');return false;}
      if(!event.id||!ids.current.has(event.id))return false;
      if(event.type==='command_result'&&event.command==='settings') {
        if(event.id===pending.current?.id)finish(event.text);
        return false;
      }
      if(event.type!=='settings_overview'&&event.type!=='error')return false;
      if(event.id!==pending.current?.id)return true;
      if(event.type==='error'){finish(event.message);return true;}
      if(!validSettingsOverview(event)||event.workspace!==workspace||event.session_id!==sessionId){finish('设置回执身份不匹配，原内容保留');return true;}
      setOverview(event);finish();return true;
    }};
}
