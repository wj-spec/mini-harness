import React, {useCallback, useRef, useState} from 'react';
import {Box, Text, useInput, type Key} from 'ink';
import {ActionButtons, type ActionTarget} from './action-buttons.js';
import {fitText, textLines} from './markdown.js';
import {ReadingPane} from './workbench.js';
import {useTranscriptMouse} from './use-transcript-scroll.js';
import type {Palette} from './theme.js';
import type {LocateItem} from './history-locate.js';

type Action = 'previous' | 'next' | 'jump' | 'back';

export function LocatorPanel({title, items, columns, rows, palette, active, status, onJump, onReturn}: {
  title: string; items: readonly LocateItem[]; columns: number; rows: number; palette: Palette; active: boolean;
  status: string; onJump: (line: number) => void; onReturn: () => void;
}) {
  const [selected, setSelected] = useState(0);
  const [top, setTop] = useState(0);
  const [button, setButton] = useState<Action | null>(null);
  const targets = useRef<ActionTarget<Action>[]>([]);
  const press = useRef<string | null>(null);
  const item = items[Math.max(0, Math.min(items.length - 1, selected))];
  const buttons: {action: Action; label: string}[] = [
    {action: 'previous', label: '上一条'}, {action: 'next', label: '下一条'}, {action: 'jump', label: '定位'}, {action: 'back', label: '返回'},
  ];
  const bodyHeight = Math.max(3, rows - 4);
  const lines = items.flatMap((entry, index) => textLines(
    `${index === selected ? '›' : ' '} ${index + 1}. ${entry.label}\n     ${entry.detail}`, Math.max(1, columns - 5)));
  const clampTop = (value: number) => Math.max(0, Math.min(Math.max(0, lines.length - bodyHeight + 2), value));
  const move = (delta: number) => {
    const next = Math.max(0, Math.min(items.length - 1, selected + delta));
    setSelected(next);
    const at = next * 2;
    setTop(current => clampTop(Math.min(current, Math.max(0, at - bodyHeight + 4))));
    setTop(current => Math.max(current, at > current + bodyHeight - 3 ? Math.max(0, at - bodyHeight + 4) : current));
  };
  const scroll = (delta: number) => setTop(current => clampTop(current + delta));
  const act = (action: Action) => {
    setButton(null);
    if (action === 'back') onReturn();
    else if (action === 'previous' || action === 'next') move(action === 'previous' ? -1 : 1);
    else if (action === 'jump' && item) onJump(item.line);
  };
  const layout = `${columns}:${rows}:${selected}:${items.length}`;
  useTranscriptMouse({enabled: active, top: 3, bottom: rows - 2, left: 1, right: columns,
    onScroll: scroll, onPointer: event => {
      const hit = targets.current.find(target => event.row >= target.top && event.row <= target.bottom && event.column >= target.left && event.column <= target.right);
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
      const actions = buttons.map(entry => entry.action); const at = button ? actions.indexOf(button) : -1;
      setButton(actions[(at + (key.shift ? actions.length - 1 : 1) + actions.length) % actions.length] ?? null); return;
    }
    if (key.return) {if (button) act(button); else if (item) onJump(item.line); return;}
    if (key.upArrow || key.downArrow) move(key.upArrow ? -1 : 1);
    if (key.pageUp || key.pageDown) scroll((key.pageUp ? -1 : 1) * (bodyHeight - 2));
  };
  useInput(useCallback((input, key) => handle.current(input, key), []));
  return <Box width={columns} height={rows} flexDirection="column" backgroundColor={palette.background} overflow="hidden">
    <Text color={palette.foreground} bold>{fitText(`定位 · ${title}`, columns)}</Text>
    <Text color={palette.warning}>{fitText(items.length ? `${selected + 1}/${items.length} · ${item?.detail ?? ''} · ${status}` : status, columns)}</Text>
    <ReadingPane title="定位结果" lines={lines} top={top} height={bodyHeight} columns={columns} palette={palette}/>
    <ActionButtons buttons={buttons} selected={button} columns={columns} height={1} palette={palette} targets={targets}/>
    <Text color={palette.secondary}>{fitText('↑↓ 选择 · Enter 定位 · 返回保留草稿与光标', columns)}</Text>
  </Box>;
}
