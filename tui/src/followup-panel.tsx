import React, {useCallback, useLayoutEffect, useRef, useState} from 'react';
import {Box, Text, useInput, type Key} from 'ink';
import {ActionButtons, type ActionTarget} from './action-buttons.js';
import {Composer} from './composer.js';
import {fitText, textLines} from './markdown.js';
import {ReadingPane} from './workbench.js';
import {useTranscriptMouse} from './use-transcript-scroll.js';
import type {Palette} from './theme.js';
import type {useFollowups} from './use-followups.js';

type Action = 'previous' | 'next' | 'back' | 'edit' | 'remove' | 'resume' | 'save' | 'cancel' | 'retry' | 'history' | 'forget' | 'confirm';
export function FollowupPanel({queue, columns, rows, palette, blocked, status, onResume, onInspect, onDirtyChange, active}: {
  queue: ReturnType<typeof useFollowups>; columns: number; rows: number; palette: Palette;
  blocked?: string; status: string; onResume: () => void; onInspect: () => void; onDirtyChange: (dirty: boolean) => void; active: boolean;
}) {
  const [index, setIndex] = useState(0);
  const [editing, setEditing] = useState<{id: string; text: string} | null>(null);
  const [top, setTop] = useState(0);
  const [selected, setSelected] = useState<Action | null>(null);
  const [message, setMessage] = useState('');
  const [confirm, setConfirm] = useState(false);
  const dirty = Boolean(editing && queue.items.find(item => item.id === editing.id)?.text !== editing.text);
  useLayoutEffect(() => {onDirtyChange(dirty); return () => onDirtyChange(false);}, [dirty, onDirtyChange]);
  const targets = [useRef<ActionTarget<Action>[]>([]), useRef<ActionTarget<Action>[]>([])];
  const pressed = useRef<string | null>(null);
  const items = queue.inflight ? [queue.inflight, ...queue.items] : queue.items;
  const position = Math.min(index, Math.max(0, items.length - 1));
  const item = items[position];
  const sent = Boolean(item && item.id === queue.inflight?.id);
  const buttons: {action: Action; label: string}[][] = confirm
    ? [[{action: 'confirm', label: '确认移出'}, {action: 'cancel', label: '取消'}]] : editing
    ? [[{action: 'save', label: '保存'}, {action: 'cancel', label: '取消'}]]
    : [[{action: 'previous', label: '前条'}, {action: 'next', label: '后条'}, {action: 'back', label: '返回'}],
      [...(sent ? [{action: 'history' as const, label: '历史'},
        ...(!queue.live && queue.writable && !queue.problem ? [{action: 'forget' as const, label: '移出'}] : [])] :
        item && queue.writable ? [{action: 'edit' as const, label: '编辑'}, {action: 'remove' as const, label: '撤回'}] : []),
        ...(queue.problem ? [{action: 'retry' as const, label: queue.writable ? '重试' : '接入'}] :
          item && !blocked && queue.writable && !queue.inflight ? [{action: 'resume' as const, label: '继续'}] : [])]];
  const bodyHeight = Math.max(4, rows - 5 - buttons.length);
  const lines = textLines(confirm ? '仅移出这条追踪记录，不会停止或重发原任务。请先核对历史结果，再决定是否继续其他待办。' :
    `${sent ? `${queue.live ? '正在执行' : '结果待核对'}\n请求：${queue.inflight!.requestId}\n\n` : ''}${item?.text ?? '没有待办。运行中用 /followup <内容> 添加。'}`, Math.max(1, columns - 5));
  const move = (delta: number) => {setIndex(Math.max(0, Math.min(items.length - 1, position + delta))); setTop(0); setMessage('');};
  const act = (action: Action) => {
    setSelected(null); setMessage('');
    if (action === 'back') queue.close();
    else if (action === 'retry') queue.retry();
    else if (action === 'history') onInspect();
    else if (action === 'forget' && sent && !queue.live) {setConfirm(true); setTop(0);}
    else if (action === 'confirm') {if (queue.acknowledge()) {setConfirm(false); setTop(0);}}
    else if (action === 'previous' || action === 'next') move(action === 'previous' ? -1 : 1);
    else if (action === 'edit' && item && !sent && queue.writable) setEditing({...item});
    else if (action === 'remove' && item && !sent && queue.writable) {queue.remove(item.id); setTop(0);}
    else if (action === 'resume' && !blocked && queue.writable && !queue.problem) onResume();
    else if (action === 'cancel') {setEditing(null); setConfirm(false);}
    else if (action === 'save' && editing) {
      if (!editing.text.trim()) {setMessage('正文不能为空；可取消后撤回'); return;}
      if (queue.edit(editing.id, editing.text)) {setEditing(null); setTop(0); setMessage('已保存 · 尚未发送');}
    }
  };
  const layout = `${columns}:${rows}:${item?.id}:${editing?.id}:${buttons.flat().map(button => button.action).join()}`;
  useTranscriptMouse({enabled: active, top: 3, bottom: rows - buttons.length - 2, left: 1, right: columns,
    onScroll: delta => {if (!editing) setTop(value => Math.max(0, Math.min(Math.max(0, lines.length - bodyHeight + 3), value + delta)));},
    onPointer: event => {
      const hit = targets.flatMap(ref => ref.current).find(target => event.row >= target.top && event.row <= target.bottom && event.column >= target.left && event.column <= target.right);
      const key = hit ? `${layout}:${hit.action}` : null;
      if (event.type === 'press' && event.button === 0) pressed.current = key;
      else if (event.type === 'release') {const previous = pressed.current; pressed.current = null; if (hit && previous === key) act(hit.action);}
      else pressed.current = null;
      return Boolean(hit);
    }});
  const handle = useRef<(input: string, key: Key) => void>(() => {});
  handle.current = (input, key) => {
    if (!active || key.eventType === 'release') return;
    if (key.ctrl || key.meta) return;
    if (key.escape) {confirm ? setConfirm(false) : editing ? setEditing(null) : queue.close(); return;}
    if (key.tab) {
      const actions = buttons.flat().map(button => button.action); const at = selected ? actions.indexOf(selected) : -1;
      setSelected(actions[(at + (key.shift ? actions.length - 1 : 1) + actions.length) % actions.length] ?? null); return;
    }
    if (key.return && selected) {act(selected); return;}
    if (editing || confirm) return;
    if (key.upArrow || key.downArrow) {move(key.upArrow ? -1 : 1); return;}
    if (key.pageUp || key.pageDown) {setTop(value => Math.max(0, Math.min(Math.max(0, lines.length - bodyHeight + 3), value + (key.pageUp ? -1 : 1) * (bodyHeight - 3)))); return;}
    if (key.return && item) act('edit');
  };
  useInput(useCallback((input, key) => handle.current(input, key), []));
  return <Box width={columns} height={rows} flexDirection="column" backgroundColor={palette.background} overflow="hidden">
    <Text color={palette.foreground} bold>{fitText(`后续消息 · ${item ? position + 1 : 0}/${items.length}${editing ? ' · 编辑' : ''}`, columns)}</Text>
    <Text color={palette.warning}>{fitText(queue.problem || message || queue.paused || '成功后按顺序继续', columns)}</Text>
    <Text color={palette.secondary}>{fitText(status, columns)}</Text>
    {editing ? <Box paddingX={2} height={bodyHeight} flexShrink={0}>
      <Composer value={editing.text} onChange={text => setEditing({...editing, text})} onSubmit={() => act('save')} history={[]}
        focused={active && !selected} columns={columns - 4} maxRows={bodyHeight} foregroundColor={palette.foreground} accentColor={palette.accent}/>
    </Box> : <ReadingPane title="待办正文" lines={lines} top={top} height={bodyHeight} columns={columns} palette={palette}/>}
    {buttons.map((group, i) => <ActionButtons key={i} buttons={group} selected={selected} columns={columns} height={1} palette={palette} targets={targets[i]!}/>)}
    <Text color={palette.secondary}>{fitText(blocked || '↑↓ 选条 · 滚轮读正文 · Tab 选动作', columns)}</Text>
    <Text color={palette.secondary}>{fitText(queue.problem ? '待办已暂停 · 原输入保留' : queue.persisted ? '已保存到会话 · 重开先暂停' : '仅本次窗口 · 返回不会自动继续', columns)}</Text>
  </Box>;
}
