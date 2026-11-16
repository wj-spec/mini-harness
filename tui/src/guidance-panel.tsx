import React, {useCallback, useRef, useState} from 'react';
import {Box, Text, useInput, type Key} from 'ink';
import {ActionButtons, type ActionTarget} from './action-buttons.js';
import {fitText, textLines} from './markdown.js';
import {ReadingPane} from './workbench.js';
import {useTranscriptMouse} from './use-transcript-scroll.js';
import type {Palette} from './theme.js';
import type {GuideAction, GuidePage} from './guidance.js';

type Action = 'primary' | 'tips' | 'back' | 'previous' | 'next';
export function GuidePanel({pages, columns, rows, palette, active, tips, context, onAction, onToggle, onReturn}: {
  pages: GuidePage[]; columns: number; rows: number; palette: Palette; active: boolean;
  tips: boolean; context: string; onAction: (action: GuideAction) => string | void;
  onToggle: () => void; onReturn: () => void;
}) {
  const [index, setIndex] = useState(0);
  const [top, setTop] = useState(0);
  const [selected, setSelected] = useState<Action>('primary');
  const [problem, setProblem] = useState('');
  const targets = useRef<ActionTarget<Action>[]>([]);
  const navigation = useRef<ActionTarget<Action>[]>([]);
  const press = useRef<string | null>(null);
  const page = pages[index]!;
  const paneRows = Math.max(4, rows - 5);
  const lines = textLines(page.body, Math.max(1, columns - 5));
  const maximum = Math.max(0, lines.length - Math.max(1, paneRows - 3));
  const buttons: {action: Action; label: string}[] = [
    ...(page.action ? [{action: 'primary' as const, label: page.label!}] : []),
    {action: 'tips', label: tips ? '关提示' : '开提示'}, {action: 'back', label: '返回'},
  ];
  const focused = buttons.some(button => button.action === selected) ? selected : 'back';
  const scroll = (delta: number) => setTop(value => Math.max(0, Math.min(maximum, value + delta)));
  const move = (delta: number) => {setIndex(value => (value + delta + pages.length) % pages.length); setTop(0); setProblem(''); setSelected('primary');};
  const act = (action: Action) => {
    if (!active) return;
    if (action === 'back') onReturn();
    else if (action === 'tips') {onToggle(); setProblem(tips ? '提示已关闭（本窗口）；可随时重开' : '提示已开启（本窗口）');}
    else if (action === 'previous' || action === 'next') move(action === 'next' ? 1 : -1);
    else if (page.action) setProblem(onAction(page.action) || '');
  };
  const layout = `${columns}:${rows}:${index}:${tips}`;
  useTranscriptMouse({enabled: active, top: 4, bottom: rows - 2, left: 1, right: columns,
    onScroll: scroll, onPointer: event => {
      const hit = [...targets.current, ...navigation.current].find(target => event.row >= target.top && event.row <= target.bottom && event.column >= target.left && event.column <= target.right);
      const key = hit ? `${layout}:${hit.action}` : null;
      if (event.type === 'press' && event.button === 0) press.current = key;
      else if (event.type === 'release') {const before = press.current; press.current = null; if (hit && key === before) act(hit.action);}
      else press.current = null;
      return Boolean(hit);
    }});
  const handle = useRef<(input: string, key: Key) => void>(() => {});
  handle.current = (input, key) => {
    if (!active || key.eventType === 'release' || key.ctrl || key.meta) return;
    if (key.escape) {onReturn(); return;}
    if (key.leftArrow || key.rightArrow) {move(key.rightArrow ? 1 : -1); return;}
    if (key.tab) {
      const at = buttons.findIndex(button => button.action === focused);
      setSelected(buttons[(at + (key.shift ? -1 : 1) + buttons.length) % buttons.length]!.action); return;
    }
    if (key.return) {act(focused); return;}
    if (index === 0 && /^[123]$/.test(input)) {
      setProblem(onAction((['example-read', 'example-edit', 'example-review'] as const)[Number(input) - 1]!) || ''); return;
    }
    if (key.upArrow || key.downArrow) scroll(key.upArrow ? -1 : 1);
    else if (key.pageUp || key.pageDown) scroll((key.pageUp ? -1 : 1) * Math.max(1, paneRows - 4));
    else if (key.home) setTop(0);
    else if (key.end) setTop(maximum);
  };
  useInput(useCallback((input, key) => handle.current(input, key), []));
  return <Box width={columns} height={rows} flexDirection="column" backgroundColor={palette.background} overflow="hidden">
    <Text color={palette.foreground} bold>{fitText('功能区指引 · Esc 返回草稿', columns)}</Text>
    <Box height={1} width={columns}>
      <ActionButtons buttons={[{action: 'previous', label: '前区'}, {action: 'next', label: '后区'}]} selected={null}
        columns={20} height={1} palette={palette} targets={navigation}/>
      <Text color={palette.accent}>{fitText(`${index + 1}/${pages.length} ${page.title}`, columns - 20)}</Text>
    </Box>
    <Text color={problem ? palette.warning : palette.secondary}>{fitText(problem || context, columns)}</Text>
    <ReadingPane title={page.title} lines={lines} top={Math.min(top, maximum)} height={paneRows} columns={columns} palette={palette}/>
    <ActionButtons buttons={buttons} selected={focused} columns={columns} height={1} palette={palette} targets={targets}/>
    <Text color={palette.secondary}>{fitText(columns < 60 ? '←→区 ↑↓滚动 Tab选 Enter确认' : '←→ 功能区 · ↑↓/滚轮 阅读 · Tab 选择动作 · Enter 确认 · Esc 返回', columns)}</Text>
  </Box>;
}
