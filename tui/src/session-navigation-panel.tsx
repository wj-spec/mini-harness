import React, {useCallback, useLayoutEffect, useRef, useState} from 'react';
import {Box, Text, useInput, type Key} from 'ink';
import {ActionButtons, type ActionTarget} from './action-buttons.js';
import {fitText, textLines} from './markdown.js';
import {ReadingPane} from './workbench.js';
import {useTranscriptMouse} from './use-transcript-scroll.js';
import type {Palette} from './theme.js';
import type {SessionNavigation} from './use-session-navigation.js';
import type {SessionCatalogRow} from './protocol.js';

type Action = 'previous' | 'next' | 'back' | 'open' | 'refresh' | 'retry' | 'older' | 'newer' | 'cancel-open';
const purposes: Record<string, string> = {user: '真实会话', test: '测试会话'};

export function SessionNavigationPanel({navigation, columns, rows, palette, active, status, message, opening, onOpen, onCancelOpen, onReturn}: {
  navigation: SessionNavigation; columns: number; rows: number; palette: Palette; active: boolean;
  status: string; message?: string; opening?: boolean;
  onOpen?: (row: SessionCatalogRow) => void; onCancelOpen?: () => void; onReturn?: () => void;
}) {
  const [chosen, setChosen] = useState<{id: string | null; lost: boolean}>({id: navigation.records[0]?.session_id ?? null, lost: false});
  const [top, setTop] = useState(0); const [selected, setSelected] = useState<Action | null>(null);
  const targets = [useRef<ActionTarget<Action>[]>([]), useRef<ActionTarget<Action>[]>([]), useRef<ActionTarget<Action>[]>([])];
  const pressed = useRef<string | null>(null);
  const lastPage = useRef(navigation.cursor);
  useLayoutEffect(() => {
    if (lastPage.current !== navigation.cursor || !chosen.id && !chosen.lost && navigation.records.length) {
      setChosen({id: navigation.records[0]?.session_id ?? null, lost: false}); setTop(0);
    } else if (chosen.id && !navigation.records.some(item => item.session_id === chosen.id)) {
      setChosen({id: null, lost: true}); setTop(0);
    }
    lastPage.current = navigation.cursor;
  }, [navigation.records, navigation.cursor, chosen]);
  const position = navigation.records.findIndex(item => item.session_id === chosen.id); const item = navigation.records[position];
  const buttons: {action: Action; label: string}[][] = [
    [{action: 'previous', label: '前一条'}, {action: 'next', label: '后一条'}, {action: 'back', label: '返回'}],
    [
      ...(opening ? [{action: 'cancel-open' as const, label: '取消打开'}] :
        item && onOpen ? [{action: 'open' as const, label: '打开会话'}] : []),
      ...(!navigation.loading && !opening ? [{action: navigation.problem ? 'retry' as const : 'refresh' as const, label: navigation.problem ? '重试' : '刷新'}] : []),
    ],
    ...(!navigation.loading && !opening && (navigation.next || navigation.previous.length) ? [[
      ...(navigation.previous.length ? [{action: 'newer' as const, label: '较新'}] : []),
      ...(navigation.next ? [{action: 'older' as const, label: '更早'}] : []),
    ]] : []),
  ];
  const bodyHeight = Math.max(4, rows - 4 - buttons.length);
  const purpose = navigation.purpose ? purposes[navigation.purpose] ?? navigation.purpose : '用途待核对';
  const body = item ? `${item.title}\n${item.session_id} · ${purpose} · ${item.message_count} 条消息${item.truncated ? ' · 预览已截断' : ''}\n更新：${item.updated_at}\n\n${item.preview}` :
    chosen.lost ? '原会话已不在本页，请重新选择。' : '当前工作区没有可读取的旧会话。';
  const lines = textLines([message, body].filter(Boolean).join('\n\n'), Math.max(1, columns - 5));
  const move = (delta: number) => {
    const next = position < 0 ? 0 : Math.max(0, Math.min(navigation.records.length - 1, position + delta));
    setChosen({id: navigation.records[next]?.session_id ?? null, lost: false}); setTop(0);
  };
  const act = (action: Action) => {
    setSelected(null);
    if (action === 'back') (onReturn ?? navigation.close)();
    else if (action === 'previous' || action === 'next') move(action === 'previous' ? -1 : 1);
    else if (action === 'open' && item && onOpen && !opening) onOpen(item);
    else if (action === 'cancel-open') onCancelOpen?.();
    else if (!navigation.loading && ['refresh', 'retry', 'older', 'newer'].includes(action)) navigation[action as 'refresh' | 'retry' | 'older' | 'newer']();
  };
  const scroll = (delta: number) => setTop(value => Math.max(0, Math.min(Math.max(0, lines.length - bodyHeight + 3), value + delta)));
  const layout = `${columns}:${rows}:${item?.session_id}:${navigation.cursor}:${buttons.flat().map(button => button.action).join()}`;
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
    if (key.escape) {if (opening) onCancelOpen?.(); else (onReturn ?? navigation.close)(); return;}
    if (key.tab) {
      const actions = buttons.flat().map(button => button.action); const at = selected ? actions.indexOf(selected) : -1;
      setSelected(actions[(at + (key.shift ? actions.length - 1 : 1) + actions.length) % actions.length] ?? null); return;
    }
    if (key.return && selected) {act(selected); return;}
    if (key.upArrow || key.downArrow) move(key.upArrow ? -1 : 1);
    if (key.pageUp || key.pageDown) scroll((key.pageUp ? -1 : 1) * (bodyHeight - 3));
  };
  useInput(useCallback((input, key) => handle.current(input, key), []));
  return <Box width={columns} height={rows} flexDirection="column" backgroundColor={palette.background} overflow="hidden">
    <Text color={palette.foreground} bold>{fitText(`旧会话 · ${item ? position + 1 : 0}/${navigation.records.length} · 第 ${navigation.previous.length + 1} 页 · ${purpose}`, columns)}</Text>
    <Text color={palette.warning}>{fitText(opening ? '正在打开旧会话…' : navigation.loading ? '正在读取…' : navigation.problem || (navigation.skipped ? `跳过 ${navigation.skipped} 条损坏或范围不符的记录` : status), columns)}</Text>
    <ReadingPane title="会话预览" lines={lines} top={top} height={bodyHeight} columns={columns} palette={palette}/>
    {buttons.map((group, i) => <ActionButtons key={i} buttons={group} selected={selected} columns={columns} height={1} palette={palette} targets={targets[i]!}/>)}
    <Text color={palette.secondary}>{fitText('↑↓ 选会话 · 滚轮读预览 · Tab 选动作 · 打开不会自动运行', columns)}</Text>
    <Text color={palette.secondary}>{fitText(opening ? '正在复核会话身份；取消打开保留当前会话' : '打开后输入 /resume 显式续做 · 返回保留草稿', columns)}</Text>
  </Box>;
}
