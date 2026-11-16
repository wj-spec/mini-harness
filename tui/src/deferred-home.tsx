import React, {useCallback, useRef} from 'react';
import {Box, Text, useInput, type Key} from 'ink';
import {fitText, textLines} from './markdown.js';
import {Composer} from './composer.js';
import {ActionButtons, type ActionTarget} from './action-buttons.js';
import {useTranscriptMouse} from './use-transcript-scroll.js';
import type {Palette} from './theme.js';

type Action = 'connect' | 'demo' | 'quit';
// 稍后配置：可输入并保留草稿，但不连接模型、不能提交真实任务。
export function DeferredHome({workspace, columns, rows, palette, draft, onChange, onBlocked,
  onConnect, onDemo, onQuit, demoStarting = false, demoProblem = '', notice = ''}: {
  workspace: string; columns: number; rows: number; palette: Palette;
  draft: string; onChange: (value: string) => void; onBlocked: () => void;
  onConnect: () => void; onDemo?: () => void; onQuit: () => void;
  demoStarting?: boolean; demoProblem?: string; notice?: string;
}) {
  const compact = columns < 48;
  const targets = useRef<ActionTarget<Action>[]>([]);
  const press = useRef<Action | null>(null);
  const buttons: {action: Action; label: string}[] = [
    {action: 'connect', label: compact ? '连接' : '连接模型'},
    ...(onDemo ? [{action: 'demo' as const, label: compact ? 'Demo' : '先体验 Demo'}] : []),
    {action: 'quit', label: '退出'},
  ];
  const act = (action: Action) => {
    if (action === 'connect') onConnect();
    else if (action === 'demo') onDemo?.();
    else onQuit();
  };
  const pressRef = useRef<(input: string, key: Key) => void>(() => {});
  pressRef.current = (input, key) => {
    if (key.eventType === 'release') return;
    if (key.escape) onConnect();
  };
  useInput(useCallback((input, key) => pressRef.current(input, key), []));
  useTranscriptMouse({enabled: true, top: 1, bottom: rows, left: 1, right: columns,
    onScroll: () => {}, onPointer: event => {
      const hit = targets.current.find(target => event.row >= target.top && event.row <= target.bottom &&
        event.column >= target.left && event.column <= target.right);
      if (event.type === 'press' && event.button === 0) press.current = hit?.action ?? null;
      else if (event.type === 'release') {
        const prior = press.current; press.current = null;
        if (hit && hit.action === prior) act(hit.action);
      }
      return Boolean(hit);
    }});
  const name = workspace.split(/[\\/]/).filter(Boolean).at(-1) ?? workspace;
  const path = workspace.length > Math.max(12, columns - 8) ? '…' + workspace.slice(-(Math.max(12, columns - 8) - 1)) : workspace;
  const status = demoProblem ? demoProblem
    : notice ? notice
    : demoStarting ? '正在启动 Demo（本地模型，不调用真实服务）…'
      : '尚未连接模型；可以输入并保留草稿，但不能提交真实任务。';
  return <Box width={columns} height={rows} flexDirection="column" overflow="hidden" backgroundColor={palette.background}>
    <Text bold color={palette.foreground}>{fitText('Mini · 尚未连接模型', columns)}</Text>
    <Text color={palette.secondary}>{fitText('项目  ' + name + '  ·  ' + path, columns)}</Text>
    {textLines('连接模型后即可发送任务。现在可以先写下想做的事，草稿会一直保留。', columns).slice(0, 2)
      .map((line, index) => <Text key={index} color={palette.secondary}>{line.spans.map(span => span.text).join('')}</Text>)}
    <Box flexGrow={1} flexDirection="column"/>
    <Box backgroundColor={palette.focus} height={Math.min(3, Math.max(2, rows - 10))} width={columns}
      paddingX={2} flexShrink={0} flexDirection="column">
      <Composer value={draft} onChange={onChange} onSubmit={onBlocked} history={[]}
        focused placeholder="写下你想完成的任务（尚未连接模型）" columns={Math.max(1, columns - 4)}
        maxRows={Math.min(3, Math.max(2, rows - 10))} accentColor={palette.accent} warningColor={palette.warning}
        foregroundColor={palette.foreground} secondaryColor={palette.secondary}/>
    </Box>
    <ActionButtons buttons={buttons} selected={null} columns={columns} height={2} palette={palette}
      targets={targets} paddingX={compact ? 0 : 1}/>
    <Box height={2} flexDirection="column"><Text color={demoProblem ? palette.warning : palette.secondary}>
      {textLines(status, columns).slice(0, 2).map(line => line.spans.map(span => span.text).join('')).join('\n')}</Text></Box>
    <Text color={palette.secondary}>{fitText(compact ? 'Esc 返回配置 Ctrl+Q 退出' : 'Enter 不会发送 · Esc 返回配置 · Ctrl+Q 退出', columns)}</Text>
  </Box>;
}
