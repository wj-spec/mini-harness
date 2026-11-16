import React, {useCallback, useEffect, useRef, useState} from 'react';
import {Box, Text, useInput, type Key} from 'ink';
import {fitText, textLines} from './markdown.js';
import {ReadingPane} from './workbench.js';
import {ActionButtons, type ActionTarget} from './action-buttons.js';
import {useTranscriptMouse} from './use-transcript-scroll.js';
import type {Palette} from './theme.js';
import type {ModelAction, ModelDraft, ModelOverview} from './model-settings.js';

const fields = ['model','base_url','api_key','trust_env'] as const;
const labels = ['模型名','服务地址','API Key（隐藏）','使用环境代理'];
const actions = ['save','test','apply','refresh','back'] as const;
type Action = typeof actions[number];
export function ModelPanel({overview, loading, problem, active, busy, columns, rows, palette, onRequest, onClose}: {
  overview: ModelOverview | null; loading: boolean; problem: string; active: boolean; busy: boolean;
  columns: number; rows: number; palette: Palette;
  onRequest: (action: ModelAction | 'list', draft: ModelDraft) => void; onClose: () => void;
}) {
  const [draft,setDraft] = useState<ModelDraft | null>(null);
  const [saved,setSaved] = useState<string | null>(null);
  const pendingSave = useRef<string | null>(null);
  const signature = (d: ModelDraft) => JSON.stringify([d.model,d.base_url,d.api_key,d.trust_env]);
  useEffect(() => {
    if (overview?.message.startsWith('已保存') && pendingSave.current) {
      setSaved(pendingSave.current); pendingSave.current = null;
    }
  }, [overview]);
  const [index,setIndex] = useState(0);
  const [editing,setEditing] = useState(false);
  const [confirm,setConfirm] = useState<Action | null>(null);
  const [top,setTop] = useState(0);
  const targets = useRef<ActionTarget<Action>[]>([]);
  const press = useRef<Action | null>(null);
  const value = draft ?? {model: overview?.model ?? '', base_url: overview?.base_url ?? '',
    api_key: '', trust_env: overview?.trust_env ?? false, revision: overview?.revision};
  const currentField = fields[Math.min(3,index)]!;
  const shown = currentField === 'api_key' ? (value.api_key ? '已填写（隐藏）' : overview?.key_present ? '留空保留当前 Key' : '尚未填写 Key') :
    currentField === 'trust_env' ? value.trust_env ? '开启' : '关闭' : value[currentField];
  const buttons = actions.map((action,i) => ({action,label: ['保存','检测','应用','刷新','返回'][i]!}));
  const act = (action: Action) => {
    if (!active || loading) return;
    if (action === 'back') {
      if (draft && signature(draft) !== saved && confirm !== 'back') {setConfirm('back'); return;}
      onClose(); return;
    }
    if (action === 'refresh') {onRequest('list',value); return;}
    if (!overview || busy) return;
    if (action === 'test' && confirm !== 'test') {setConfirm('test'); return;}
    if (action === 'save') pendingSave.current = signature(value);
    setConfirm(null); onRequest(action,{...value,revision:overview.revision});
  };
  const change = (text: string) => setDraft({...value,[currentField]:text});
  const handler = useRef<(input:string,key:Key)=>void>(()=>{});
  handler.current = (input,key) => {
    if (!active || key.eventType === 'release') return;
    if (key.escape) {
      if (editing) setEditing(false);
      else if (confirm) setConfirm(null);
      else act('back');
      return;
    }
    if (editing) {
      if (key.ctrl && input === 'u') {change(''); return;}
      if (key.ctrl || key.meta) return;
      if (key.return) {setEditing(false); return;}
      if (key.tab) {setEditing(false); setIndex((index+1)%9); return;}
      const text = String(value[currentField]);
      if (key.backspace || key.delete) {change(Array.from(text).slice(0,-1).join('')); return;}
      if (!key.upArrow && !key.downArrow && !key.leftArrow && !key.rightArrow && input) {
        change((text+input.replace(/[\x00-\x1f\x7f]/g,'')).slice(0,4096));
      }
      return;
    }
    if (key.ctrl || key.meta) return;
    if (key.tab || key.leftArrow || key.rightArrow) {
      setConfirm(null); setIndex((index+(key.shift || key.leftArrow ? -1 : 1)+9)%9); return;
    }
    if (key.upArrow || key.downArrow) {setTop(n=>Math.max(0,n+(key.upArrow?-1:1))); return;}
    if (key.return) {
      if (index < 4) {
        if (index === 3) setDraft({...value,trust_env:!value.trust_env});
        else if (overview) setEditing(true);
      } else act(actions[index-4]!);
    }
  };
  useInput(useCallback((input,key)=>handler.current(input,key),[]));
  useTranscriptMouse({enabled:active,top:5,bottom:rows-4,left:1,right:columns,
    onScroll:delta=>setTop(n=>Math.max(0,n+delta)),onPointer:event=>{
      const hit=targets.current.find(t=>event.row>=t.top&&event.row<=t.bottom&&event.column>=t.left&&event.column<=t.right);
      if(event.type==='press'&&event.button===0) press.current=hit?.action??null;
      else if(event.type==='release'){const prior=press.current;press.current=null;if(hit&&hit.action===prior){setIndex(actions.indexOf(hit.action)+4);act(hit.action);}}
      return Boolean(hit);
    }});
  const details=overview ? [
    '当前生效：'+overview.provider+' / '+overview.model, '服务：'+overview.base_url,
    '来源：模型 '+overview.sources.model+'；地址 '+overview.sources.base_url+'；Key '+overview.sources.api_key+'；代理 '+overview.sources.trust_env,
    '保存位置（用户配置 · 所有项目通用）：'+overview.path, '文件中模型：'+(overview.saved_model||'尚未保存'),
    'Tab 在四个字段与按钮间切换；Enter 编辑，Ctrl+U 清空，Esc 结束编辑。',
    '保存到配置文件，不切换当前模型。应用只影响后续新任务，不写文件；CLI/环境覆盖优先。',
    '更换服务需重新填写 Key。检测只发送固定短消息，可能计费，不发送代码或对话。',
    '旧任务需匹配原配置才可续做；后台进程不热更新。预算/窗口保持原值，未知模型能力不自动推断。',
  ].join('\n') : '正在读取模型信息…';
  const paneRows=Math.max(3,rows-8);
  const lines=textLines(details,Math.max(1,columns-5));
  const message=confirm==='test' ? '检测可能计费；再次确认检测' : confirm==='back' ? '有未保存编辑；再次返回将丢弃' :
    loading ? '操作中；如有审批请先处理' : problem || overview?.message || (busy?'任务运行中：可编辑，空闲后操作':'选择保存、检测或应用；三者相互独立');
  return <Box width={columns} height={rows} flexDirection="column" overflow="hidden" backgroundColor={palette.background}>
    <Text bold color={palette.foreground}>{fitText('模型设置 · Esc 返回',columns)}</Text>
    <Text color={palette.accent}>{fitText((editing?'编辑中 · ':'')+(index<4?labels[index]:'操作 · '+buttons[index-4]!.label),columns)}</Text>
    <Text color={palette.foreground}>{fitText(index<4?shown:'Tab 选择字段或操作',columns)}</Text>
    <ReadingPane title="来源与生效" lines={lines} top={Math.min(top,Math.max(0,lines.length-paneRows+3))} height={paneRows} columns={columns} palette={palette}/>
    <ActionButtons buttons={buttons} selected={index>=4?actions[index-4]!:null} columns={columns} height={2} palette={palette} targets={targets}/>
    <Box height={2}><Text color={problem||confirm?palette.warning:palette.secondary}>{textLines(message,columns).slice(0,2).map(line=>line.spans.map(span=>span.text).join('')).join('\n')}</Text></Box>
    <Text color={palette.secondary}>{fitText('Tab换项 Enter确认 ↑↓说明',columns)}</Text>
  </Box>;
}
