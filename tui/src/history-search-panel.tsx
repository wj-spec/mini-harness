import React, {useCallback, useRef, useState} from 'react';
import {Box, Text, useInput, type Key} from 'ink';
import {ActionButtons, type ActionTarget} from './action-buttons.js';
import {fitText, textLines} from './markdown.js';
import {ReadingPane} from './workbench.js';
import {useTranscriptMouse} from './use-transcript-scroll.js';
import type {Palette} from './theme.js';
import type {HistorySearchMatch} from './protocol.js';

type Action = 'previous' | 'next' | 'open' | 'more' | 'back';

export function HistorySearchPanel({query, matches, columns, rows, palette, active, loading, problem, next, truncated, skipped, onOpen, onMore, onReturn}: {
  query: string; matches: readonly HistorySearchMatch[]; columns: number; rows: number; palette: Palette; active: boolean;
  loading: boolean; problem: string; next: string | null; truncated: boolean; skipped: number;
  onOpen: (match: HistorySearchMatch) => void; onMore: () => void; onReturn: () => void;
}) {
  const [selected, setSelected] = useState(0);
  const [top, setTop] = useState(0);
  const [button, setButton] = useState<Action | null>(null);
  const targets = [useRef<ActionTarget<Action>[]>([]), useRef<ActionTarget<Action>[]>([])];
  const press = useRef<string | null>(null);
  const item = matches[Math.max(0, Math.min(matches.length - 1, selected))];
  const buttons: {action: Action; label: string}[][] = [
    [{action: 'previous', label: '上一条'}, {action: 'next', label: '下一条'}, {action: 'open', label: '打开会话'}],
    [...(next || loading ? [{action: 'more' as const, label: loading ? '读取中…' : '更多'}] : []), {action: 'back', label: '返回'}],
  ];
  const bodyHeight = Math.max(3, rows - 4 - buttons.length + 1);
  const lines = matches.flatMap((match, index) => textLines(
    `${index === selected ? '›' : ' '} ${index + 1}. [${match.title || match.session_id}] ${match.role} · ${match.snippet}\n     ${match.session_id} · ${match.updated_at}`,
    Math.max(1, columns - 5)));
  const status = loading ? '正在检索会话记录…' : problem || (matches.length ? `${selected + 1}/${matches.length}${next ? ' · 还有更多' : ''}${skipped ? ` · 跳过 ${skipped} 条损坏记录` : ''}${truncated ? ' · 已达扫描上限' : ''}` : '没有匹配的会话消息；换个关键词再试。');
  const clampTop = (value: number) => Math.max(0, Math.min(Math.max(0, lines.length - bodyHeight + 2), value));
  const move = (delta: number) => {
    const at = Math.max(0, Math.min(matches.length - 1, selected + delta));
    setSelected(at);
    const line = at * 2;
    setTop(current => clampTop(line < current ? line : line > current + bodyHeight - 3 ? Math.max(0, line - bodyHeight + 4) : current));
  };
  const act = (action: Action) => {
    setButton(null);
    if (action === 'back') onReturn();
    else if (action === 'previous' || action === 'next') move(action === 'previous' ? -1 : 1);
    else if (action === 'open' && item) onOpen(item);
    else if (action === 'more' && !loading) onMore();
  };
  const layout = `${columns}:${rows}:${selected}:${matches.length}:${loading}:${button}`;
  useTranscriptMouse({enabled: active, top: 3, bottom: rows - buttons.length - 1, left: 1, right: columns,
    onScroll: delta => setTop(current => clampTop(current + delta)), onPointer: event => {
      const hit = targets.flatMap(ref => ref.current).find(target => event.row >= target.top && event.row <= target.bottom && event.column >= target.left && event.column <= target.right);
      const key = hit ? `${layout}:${hit.action}` : null;
      if (event.type === 'press' && event.button === 0) press.current = key;
      else if (event.type === 'release') {const prior = press.current; press.current = null; if (hit && prior === key) act(hit.action);}
      else press.current = null;
      return Boolean(hit);
    }});
  const handle = useRef<(input: string, key: Key) => void>(() => {});
  handle.current = (_input, key) => {
    if (!active || key.eventType === 'release' || key.ctrl || key.meta) return;
    if (key.escape) {onReturn(); return;}
    if (key.tab) {
      const actions = buttons.flat().map(entry => entry.action); const at = button ? actions.indexOf(button) : -1;
      setButton(actions[(at + (key.shift ? actions.length - 1 : 1) + actions.length) % actions.length] ?? null); return;
    }
    if (key.return) {if (button) act(button); else if (item) onOpen(item); return;}
    if (key.upArrow || key.downArrow) move(key.upArrow ? -1 : 1);
    if (key.pageUp || key.pageDown) setTop(current => clampTop(current + (key.pageUp ? -1 : 1) * (bodyHeight - 2)));
  };
  useInput(useCallback((input, key) => handle.current(input, key), []));
  return <Box width={columns} height={rows} flexDirection="column" backgroundColor={palette.background} overflow="hidden">
    <Text color={palette.foreground} bold>{fitText(`历史检索 · ${query} · ${matches.length} 条${next ? '+' : ''}`, columns)}</Text>
    <Text color={palette.warning}>{fitText(status, columns)}</Text>
    <ReadingPane title="匹配的旧结论" lines={lines} top={top} height={bodyHeight} columns={columns} palette={palette}/>
    {buttons.map((group, index) => <ActionButtons key={index} buttons={group} selected={button} columns={columns} height={1} palette={palette} targets={targets[index]!}/>) }
    <Text color={palette.secondary}>{fitText('↑↓ 选择 · Enter 打开并定位 · 打开前复核会话身份', columns)}</Text>
  </Box>;
}
