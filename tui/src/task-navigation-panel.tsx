import React, {useCallback, useLayoutEffect, useRef, useState} from 'react';
import {Box, Text, useInput, type Key} from 'ink';
import {ActionButtons, type ActionTarget} from './action-buttons.js';
import {fitText, textLines} from './markdown.js';
import {ReadingPane} from './workbench.js';
import {useTranscriptMouse} from './use-transcript-scroll.js';
import type {Palette} from './theme.js';
import type {useTaskNavigation} from './use-task-navigation.js';

type Action = 'previous' | 'next' | 'back' | 'records' | 'refresh' | 'older' | 'newer' | 'retry' | 'connect' | 'stop' | 'cancel' | 'wait-current' | 'stop-current';
const taskLabels: Record<string, string> = {created: '待开始', running: '运行中', paused: '已暂停', waiting_approval: '等待审批',
  completed: '已完成', finished: '已结束', cancelled: '已取消', failed: '失败', interrupted: '已中断', stopped: '已停止'};
export function TaskNavigationPanel({navigation, columns, rows, palette, active, status, message, onInspect, onConnect, connecting, onCancel, handoff, onHandoff, onReturn}: {
  navigation: ReturnType<typeof useTaskNavigation>; columns: number; rows: number; palette: Palette;
  active: boolean; status: string; onInspect: (taskId: string) => void;
  onConnect?: (taskId: string, stop: boolean) => void; connecting?: boolean; onCancel?: () => void;
  handoff?: boolean; onHandoff?: (stopCurrent: boolean) => void;
  onReturn?: () => void;
  message?: string;
}) {
  const [chosen, setChosen] = useState<{id: string | null; lost: boolean}>({id: navigation.records[0]?.task_id ?? null, lost: false});
  const [top, setTop] = useState(0); const [selected, setSelected] = useState<Action | null>(null);
  const targets = [useRef<ActionTarget<Action>[]>([]), useRef<ActionTarget<Action>[]>([]), useRef<ActionTarget<Action>[]>([]), useRef<ActionTarget<Action>[]>([])];
  const pressed = useRef<string | null>(null);
  const lastPage = useRef(navigation.cursor);
  useLayoutEffect(() => {
    if (lastPage.current !== navigation.cursor || !chosen.id && !chosen.lost && navigation.records.length) {
      setChosen({id: navigation.records[0]?.task_id ?? null, lost: false}); setTop(0);
    } else if (chosen.id && !navigation.records.some(item => item.task_id === chosen.id)) {
      setChosen({id: null, lost: true}); setTop(0);
    }
    lastPage.current = navigation.cursor;
  }, [navigation.records, navigation.cursor, chosen]);
  const position = navigation.records.findIndex(item => item.task_id === chosen.id); const item = navigation.records[position];
  const buttons: {action: Action; label: string}[][] = handoff ? [
    [{action: 'wait-current', label: '等待结束后接入'}], [{action: 'stop-current', label: '停止当前并接入'}], [{action: 'cancel', label: '取消接入'}],
  ] : [
    [{action: 'previous', label: '前条'}, {action: 'next', label: '后条'}, {action: 'back', label: '返回'}],
    [...(item ? [{action: 'records' as const, label: '查看记录'}] : []),
      ...(!navigation.loading ? [{action: navigation.problem ? 'retry' as const : 'refresh' as const, label: navigation.problem ? '重试' : '刷新'}] : [])],
    ...(connecting ? [[{action: 'cancel' as const, label: '取消接入'}]] : onConnect && item?.controls?.attach ? [[
      {action: 'connect' as const, label: item.controls.attach === 'live' ? '接入任务' : '离线查看'},
      ...(item.controls.stop ? [{action: 'stop' as const, label: '停止此任务'}] : [])]] : []),
    ...(!navigation.loading && (navigation.next || navigation.previous.length) ? [[
      ...(navigation.previous.length ? [{action: 'newer' as const, label: '较新'}] : []),
      ...(navigation.next ? [{action: 'older' as const, label: '更早'}] : [])]] : [])];
  const bodyHeight = Math.max(4, rows - 4 - buttons.length);
  const worker = item?.worker;
  const body = item ? `${item.objective}\n\n任务：${item.task_id}\n状态：${taskLabels[item.status] ?? item.status} · ${item.mode}\n` +
    `worker：${item.worker_error ? '记录不可读 · ' + item.worker_error : !worker ? '无 worker 记录' : worker.foreign_host ? '其他主机 · 本机无法确认存活' : `${taskLabels[worker.status] ?? worker.status} · ${worker.pid_alive ? '进程存在' : '进程不存在'}${worker.stale ? ' · 心跳或进程异常' : ''}`}\n` +
    `待审批：${item.pending_approvals ?? '未知'} · 优先级：${item.priority}\n更新：${item.updated_at}` +
    (item.controls?.attach_reason ? `\n接入：${item.controls.attach_reason}` : '') + (item.controls?.stop_reason ? `\n停止：${item.controls.stop_reason}` : '') :
    chosen.lost ? '原任务已不在本页，请重新选择。' : '当前会话没有可读取的任务记录。';
  const lines = textLines([message, body].filter(Boolean).join('\n\n'), Math.max(1, columns - 5));
  const move = (delta: number) => {
    const next = position < 0 ? 0 : Math.max(0, Math.min(navigation.records.length - 1, position + delta));
    setChosen({id: navigation.records[next]?.task_id ?? null, lost: false}); setTop(0);
  };
  const act = (action: Action) => {
    setSelected(null);
    if (action === 'back') (onReturn ?? navigation.close)();
    else if (action === 'previous' || action === 'next') move(action === 'previous' ? -1 : 1);
    else if (action === 'records' && item) onInspect(item.task_id);
    else if ((action === 'connect' || action === 'stop') && item && !connecting) onConnect?.(item.task_id, action === 'stop');
    else if (action === 'cancel') onCancel?.();
    else if (action === 'wait-current' || action === 'stop-current') onHandoff?.(action === 'stop-current');
    else if (!navigation.loading && ['refresh', 'retry', 'older', 'newer'].includes(action)) navigation[action as 'refresh' | 'retry' | 'older' | 'newer']();
  };
  const scroll = (delta: number) => setTop(value => Math.max(0, Math.min(Math.max(0, lines.length - bodyHeight + 3), value + delta)));
  const layout = `${columns}:${rows}:${item?.task_id}:${navigation.cursor}:${buttons.flat().map(button => button.action).join()}`;
  useTranscriptMouse({enabled: active, top: 3, bottom: rows - buttons.length - 1, left: 1, right: columns,
    onScroll: scroll, onPointer: event => {
      const hit = targets.flatMap(ref => ref.current).find(target => event.row >= target.top && event.row <= target.bottom && event.column >= target.left && event.column <= target.right);
      const key = hit ? `${layout}:${hit.action}` : null;
      if (event.type === 'press' && event.button === 0) pressed.current = key;
      else if (event.type === 'release') {const prior = pressed.current; pressed.current = null; if (hit && prior === key) act(hit.action);}
      else pressed.current = null;
      return Boolean(hit);
    }});
  const handle = useRef<(input: string, key: Key) => void>(() => {});
  handle.current = (_input, key) => {
    if (!active || key.eventType === 'release' || key.ctrl || key.meta) return;
    if (key.escape) {if (handoff) onCancel?.(); else (onReturn ?? navigation.close)(); return;}
    if (key.tab) {
      const actions = buttons.flat().map(button => button.action); const at = selected ? actions.indexOf(selected) : -1;
      setSelected(actions[(at + (key.shift ? actions.length - 1 : 1) + actions.length) % actions.length] ?? null); return;
    }
    if (key.return && selected) {act(selected); return;}
    if (!handoff && (key.upArrow || key.downArrow)) move(key.upArrow ? -1 : 1);
    if (key.pageUp || key.pageDown) scroll((key.pageUp ? -1 : 1) * (bodyHeight - 3));
  };
  useInput(useCallback((input, key) => handle.current(input, key), []));
  return <Box width={columns} height={rows} flexDirection="column" backgroundColor={palette.background} overflow="hidden">
    <Text color={palette.foreground} bold>{fitText(`当前会话任务 · ${item ? position + 1 : 0}/${navigation.records.length} · 第 ${navigation.previous.length + 1} 页`, columns)}</Text>
    <Text color={palette.warning}>{fitText(navigation.loading ? '正在读取…' : navigation.problem || navigation.problems.join('；') || (navigation.skipped ? `跳过 ${navigation.skipped} 条损坏或范围不符的记录` : status), columns)}</Text>
    <ReadingPane title="目标与状态" lines={lines} top={top} height={bodyHeight} columns={columns} palette={palette}/>
    {buttons.map((group, i) => <ActionButtons key={i} buttons={group} selected={selected} columns={columns} height={1} palette={palette} targets={targets[i]!}/>)}
    <Text color={palette.secondary}>{fitText('↑↓ 选任务 · 滚轮读详情 · Tab 选动作', columns)}</Text>
    <Text color={palette.secondary}>{fitText('选择与查看不执行 · 返回保留草稿', columns)}</Text>
  </Box>;
}
