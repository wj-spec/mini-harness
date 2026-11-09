import React, {useEffect, useRef, useState} from 'react';
import {Box, Text, measureElement, useCursor, useInput, usePaste, type DOMElement} from 'ink';
import {clipCells, editText, graphemes, lineBoundary, moveVertical, wrapEditor} from './editor.js';
import {commandDefinition, commandMatches} from './commands.js';

type ComposerProps = {
  value: string;
  initialCursor?: number;
  onDraftChange?: (draft: {value: string; cursor: number}) => void;
  appendRevision?: number;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  onSubmitBlocked?: () => void;
  onTab?: (backwards: boolean) => void;
  history: string[];
  disabled?: boolean;
  focused?: boolean;
  placeholder?: string;
  columns: number;
  maxRows?: number;
  accentColor?: string;
  warningColor?: string;
  foregroundColor?: string;
  secondaryColor?: string;
  disabledHint?: string;
  menu?: {count: number; index: number; unavailable?: string; onMove: (delta: number) => void; onAccept: (submit: boolean) => void};
};

function ComposerCursor({x, y}: {x: number; y: number}) {
  const {setCursorPosition} = useCursor();
  setCursorPosition({x, y});
  return null;
}

export function Composer({value, initialCursor, onDraftChange, appendRevision = 0, onChange, onSubmit, onSubmitBlocked, onTab, history, disabled = false, focused = true, placeholder = '输入任务，开始一次编码', columns, maxRows = 3, accentColor, warningColor, foregroundColor, secondaryColor, disabledHint, menu}: ComposerProps) {
  const [cursor, setCursorState] = useState(() => Math.max(0, Math.min(initialCursor ?? graphemes(value).length, graphemes(value).length)));
  useEffect(() => {onDraftChange?.({value, cursor});}, [value, cursor, onDraftChange]);
  const draft = useRef({value, cursor});
  const [, renderSubmission] = useState(0);
  const previous = useRef(value);
  const appended = useRef(appendRevision);
  const navigation = useRef<{index: number; draft: string; cursor: number} | null>(null);
  const box = useRef<DOMElement>(null);
  const [origin, setOrigin] = useState({x: 0, y: 0});
  const height = Math.max(1, maxRows);
  const width = Math.max(4, columns);

  if (value !== previous.current) {
    const preserve = appended.current !== appendRevision && value.startsWith(previous.current);
    appended.current = appendRevision;
    previous.current = value;
    const nextCursor = preserve ? cursor : graphemes(value).length;
    draft.current = {value, cursor: nextCursor};
    navigation.current = null;
    if (cursor !== nextCursor) setCursorState(nextCursor);
  }

  const setCursor = (nextCursor: number) => {
    draft.current.cursor = nextCursor;
    setCursorState(nextCursor);
  };

  const update = (text: string, nextCursor: number, keepHistory = false) => {
    previous.current = text;
    draft.current.value = text;
    if (!keepHistory) navigation.current = null;
    setCursor(nextCursor);
    onChange(text);
  };
  const insert = (text: string) => {
    const next = editText(draft.current.value, draft.current.cursor, 'insert', text);
    update(next.text, next.cursor);
  };
  const browseHistory = (direction: -1 | 1) => {
    const {value, cursor} = draft.current;
    if (history.length === 0) return;
    if (!navigation.current) {
      if (direction === 1) return;
      navigation.current = {index: history.length, draft: value, cursor};
    }
    const saved = navigation.current;
    const nextIndex = Math.max(0, Math.min(history.length, saved.index + direction));
    saved.index = nextIndex;
    const text = nextIndex === history.length ? saved.draft : history[nextIndex];
    update(text, nextIndex === history.length ? saved.cursor : graphemes(text).length, true);
    if (nextIndex === history.length) navigation.current = null;
  };

  useInput((input, key) => {
    if (!focused) return;
    const {value, cursor} = draft.current;
    if (key.eventType === 'release' || key.super || key.hyper || key.meta || key.escape || key.pageUp || key.pageDown) return;
    if (key.ctrl) {
      if (input === 'j') {insert('\n'); return;}
      if (input === 'a') {setCursor(lineBoundary(value, cursor, 'start')); return;}
      if (input === 'e') {setCursor(lineBoundary(value, cursor, 'end')); return;}
      if (input === 'u') {
        const next = editText(value, cursor, 'clearLine');
        update(next.text, next.cursor);
      }
      return;
    }
    if (key.return) {
      if (key.shift) insert('\n');
      else if (value.trim()) {
        if (disabled) onSubmitBlocked?.();
        else if (menu) menu.onAccept(true);
        else onSubmit(value);
        renderSubmission(count => count + 1);
      }
      return;
    }
    if (input === '\n') {insert('\n'); return;}
    if (key.tab) {
      if (onTab) {onTab(key.shift); return;}
      if (menu) {menu.onAccept(false); return;}
      if (value.startsWith('/') && !value.includes('\n')) {
        const matches = commandMatches(value);
        const completion = matches.length === 1 ? matches[0] : matches[0] && matches.every(command => command === matches[0] || command.startsWith(matches[0] + ' ')) ? matches[0] : undefined;
        if (completion) update(completion, completion.length);
      }
      return;
    }
    if (key.leftArrow) {setCursor(Math.max(0, cursor - 1)); return;}
    if (key.rightArrow) {setCursor(Math.min(graphemes(value).length, cursor + 1)); return;}
    if (key.home) {setCursor(lineBoundary(value, cursor, 'start')); return;}
    if (key.end) {setCursor(lineBoundary(value, cursor, 'end')); return;}
    if (key.upArrow || key.downArrow) {
      const direction = key.upArrow ? -1 : 1;
      if (menu) {menu.onMove(direction); return;}
      const atBoundary = direction === -1 ? lineBoundary(value, cursor, 'start') === 0 : lineBoundary(value, cursor, 'end') === graphemes(value).length;
      if (atBoundary) browseHistory(direction);
      else setCursor(moveVertical(value, cursor, direction));
      return;
    }
    if (key.backspace || key.delete) {
      const next = editText(value, cursor, key.backspace ? 'backspace' : 'delete');
      update(next.text, next.cursor);
      return;
    }
    if (input) insert(input);
  });
  usePaste(text => {if (focused) insert(text);});

  const wrapped = wrapEditor(value, cursor, width - 3);
  const matches = commandMatches(value);
  const definition = commandDefinition(value.trim());
  let hint = '';
  if (onTab) hint = focused ? '草稿可编辑 · Tab 选择按钮' : '草稿保留 · Esc 返回编辑';
  else if (menu?.unavailable) hint = `${menu.unavailable} · Tab 可填充`;
  else if (focused && disabled) hint = disabledHint ?? '草稿可编辑 · 当前操作结束后可发送';
  else if (value === '/resume' || value === '/continue') hint = '继续已保存任务 · 开启新一轮预算，先核对进度';
  else if (menu) hint = `↑↓ 选择 · Enter 确认 · Tab 填充 · ${menu.index + 1}/${menu.count}`;
  else if (definition?.required) hint = `填写参数：${definition.command} ${definition.usage}`;
  else if (matches.length) hint = `${matches.join('  ')} · Tab 补全`;
  const needsPosition = wrapped.rows.length > height;
  const showHint = height > 1 && Boolean(hint || needsPosition);
  const visibleHeight = height - (showHint ? 1 : 0);
  const start = Math.max(0, Math.min(wrapped.cursorRow - visibleHeight + 1, wrapped.rows.length - visibleHeight));
  const positionHint = wrapped.rows.length > visibleHeight ? `${start > 0 ? '↑ ' : ''}${start + 1}–${Math.min(start + visibleHeight, wrapped.rows.length)}/${wrapped.rows.length} 行${start + visibleHeight < wrapped.rows.length ? ' ↓' : ''}` : '';
  const status = [positionHint, hint].filter(Boolean).join(' · ');
  const visible = wrapped.rows.slice(start, start + visibleHeight);

  useEffect(() => {
    if (box.current) {
      const metrics = measureElement(box.current);
      setOrigin(previousOrigin => previousOrigin.x === metrics.x && previousOrigin.y === metrics.y ? previousOrigin : {x: metrics.x, y: metrics.y});
    }
  });

  return <Box ref={box} flexDirection="column" height={height} width={width} flexShrink={0}>
    {focused ? <ComposerCursor x={origin.x + 2 + wrapped.cursorColumn} y={origin.y + wrapped.cursorRow - start}/> : null}
    {Array.from({length: visibleHeight}, (_, index) => {
      const row = visible[index];
      const active = focused && wrapped.cursorRow === start + index;
      const prefix = index === 0 && start > 0 ? '↑ ' : index === visibleHeight - 1 && start + visibleHeight < wrapped.rows.length ? '↓ ' : index === 0 && start === 0 ? '› ' : '· ';
      if (!value && index === 0) return <Text key={index} color={foregroundColor} wrap="truncate-end"><Text bold color={disabled ? warningColor : accentColor}>{prefix}</Text><Text inverse={focused}> </Text><Text color={secondaryColor}>{clipCells(placeholder, width - 3)}</Text></Text>;
      if (!row) return <Text key={index}> </Text>;
      const chars = graphemes(row.text);
      const localCursor = cursor - row.start;
      return <Text key={index} color={foregroundColor} wrap="truncate-end"><Text bold color={disabled ? warningColor : accentColor}>{prefix}</Text>{active ? <>{chars.slice(0, localCursor).join('')}<Text inverse>{chars[localCursor] ?? ' '}</Text>{chars.slice(localCursor + 1).join('')}</> : row.text || ' '}</Text>;
    })}
    {showHint ? <Text bold={disabled} color={disabled ? warningColor : secondaryColor} wrap="truncate-end">{clipCells(status, width)}</Text> : null}
  </Box>;
}
