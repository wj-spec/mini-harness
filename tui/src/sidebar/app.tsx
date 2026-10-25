import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {Box, Text, useInput, useWindowSize} from 'ink';
import stringWidth from 'string-width';
import {fitText} from '../markdown.js';
import {getPalette, type Palette, type ThemeName} from '../theme.js';
import {terminalColorLevel} from '../terminal-colors.js';
import {
  changeCounts, readFileDiff, readWorkspaceStatus, statusLetter, statusLabel, totalCounts,
  type ChangedFile, type DiffResult, type StatusResult,
} from './git.js';
import {diffStats, parseDiff, wrapDiffLines, type DiffKind} from './diff.js';
import {
  applyWorkspace, initialSidebarState, scrollDiff, selectDelta, selectedFile, toggleFocus, toggleFollow,
} from './state.js';

export type SidebarProps = {
  workspace: string;
  theme?: ThemeName;
  refreshMs?: number;
  size?: {columns: number; rows: number};
  readStatus?: (workspace: string) => StatusResult;
  readDiff?: (workspace: string, file: ChangedFile, hasHead: boolean) => DiffResult;
  onQuit?: () => void;
};

type DiffState = {path: string | null; text: string; error: string | null};

function statusColor(file: ChangedFile, palette: Palette): string {
  switch (file.status) {
    case 'added': return palette.success;
    case 'deleted': return palette.error;
    case 'untracked': return palette.accent;
    case 'renamed': return palette.accent;
    case 'conflicted': return palette.warning;
    default: return palette.warning;
  }
}

function diffColor(kind: DiffKind, palette: Palette): string {
  switch (kind) {
    case 'add': return palette.success;
    case 'del': return palette.error;
    case 'hunk': return palette.accent;
    case 'meta': return palette.secondary;
    case 'note': return palette.warning;
    default: return palette.foreground;
  }
}

function padTo(text: string, width: number): string {
  return text + ' '.repeat(Math.max(0, width - stringWidth(text)));
}

function fileRow(file: ChangedFile, columns: number): string {
  const letter = statusLetter(file);
  const counts = changeCounts(file);
  const prefix = `${letter} `;
  const prefixWidth = stringWidth(prefix);
  const countsWidth = stringWidth(counts);
  const showCounts = countsWidth > 0 && prefixWidth + countsWidth + 8 <= columns;
  const pathWidth = Math.max(1, columns - prefixWidth - (showCounts ? countsWidth : 0));
  const path = fitText(file.path, pathWidth);
  return `${prefix}${padTo(path, pathWidth)}${showCounts ? counts : ''}`;
}

export function SidebarApp({
  workspace, theme = 'system', refreshMs = 1000, size, readStatus, readDiff, onQuit,
}: SidebarProps) {
  const terminal = useWindowSize();
  const columns = Math.max(12, size?.columns ?? terminal.columns ?? 80);
  const rows = Math.max(6, size?.rows ?? terminal.rows ?? 24);
  const inner = Math.max(6, columns - 2);
  const palette = getPalette(theme, terminalColorLevel());

  const [state, setState] = useState(initialSidebarState);
  const [diff, setDiff] = useState<DiffState>({path: null, text: '', error: null});

  const readStatusRef = useRef(readStatus ?? readWorkspaceStatus);
  readStatusRef.current = readStatus ?? readWorkspaceStatus;
  const readDiffRef = useRef(readDiff ?? ((root: string, file: ChangedFile, hasHead: boolean) => readFileDiff(root, file, hasHead)));
  readDiffRef.current = readDiff ?? ((root: string, file: ChangedFile, hasHead: boolean) => readFileDiff(root, file, hasHead));

  const refresh = useCallback(() => {
    const result = readStatusRef.current(workspace);
    if (result.ok) setState(previous => applyWorkspace(previous, result.status));
    else setState(previous => ({...previous, error: result.message}));
  }, [workspace]);

  useEffect(() => {
    refresh();
    if (!refreshMs) return;
    const timer = setInterval(refresh, refreshMs);
    return () => clearInterval(timer);
  }, [refresh, refreshMs]);

  useEffect(() => {
    const file = state.files.find(item => item.path === state.selected) ?? null;
    if (!file) {
      setDiff({path: null, text: '', error: null});
      return;
    }
    const result = readDiffRef.current(workspace, file, state.head !== null);
    if (result.ok) setDiff({path: file.path, text: result.text, error: null});
    else setDiff({path: file.path, text: '', error: result.message});
  }, [workspace, state.selected, state.signature, state.head]);

  const parsed = useMemo(() => parseDiff(diff.text), [diff.text]);
  const wrapped = useMemo(() => wrapDiffLines(parsed, inner), [parsed, inner]);
  const stats = useMemo(() => diffStats(parsed), [parsed]);
  const totals = totalCounts(state.files);
  const listRows = state.files.length ? Math.min(state.files.length, Math.max(2, Math.floor((rows - 3) * 0.32))) : 0;
  const diffRows = Math.max(1, rows - (listRows ? listRows + 3 : 2));
  const index = state.files.findIndex(file => file.path === state.selected);
  const listTop = listRows ? Math.max(0, Math.min(index - listRows + 2, Math.max(0, state.files.length - listRows))) : 0;
  const maxScroll = Math.max(0, wrapped.length - diffRows);
  const scrolled = Math.min(state.diffScroll, maxScroll);
  const current = selectedFile(state);

  const quit = () => onQuit?.();

  useInput((input, key) => {
    if (key.eventType === 'release') return;
    if (key.ctrl && input === 'c') {quit(); return;}
    if (!key.ctrl && !key.meta && input === 'q') {quit(); return;}
    if (!key.ctrl && !key.meta && input === 'r') {refresh(); return;}
    if (!key.ctrl && !key.meta && input === 'f') {setState(previous => toggleFollow(previous)); return;}
    if (key.tab) {setState(previous => toggleFocus(previous)); return;}
    if (key.pageUp) {setState(previous => scrollDiff(previous, -Math.max(1, diffRows - 1), maxScroll)); return;}
    if (key.pageDown) {setState(previous => scrollDiff(previous, Math.max(1, diffRows - 1), maxScroll)); return;}
    if (key.home || input === 'g') {setState(previous => scrollDiff(previous, -maxScroll, maxScroll)); return;}
    if (key.end || input === 'G') {setState(previous => scrollDiff(previous, maxScroll, maxScroll)); return;}
    if (key.upArrow || (!key.ctrl && !key.meta && input === 'k')) {
      if (state.focus === 'diff') setState(previous => scrollDiff(previous, -1, maxScroll));
      else setState(previous => selectDelta(previous, -1));
      return;
    }
    if (key.downArrow || (!key.ctrl && !key.meta && input === 'j')) {
      if (state.focus === 'diff') setState(previous => scrollDiff(previous, 1, maxScroll));
      else setState(previous => selectDelta(previous, 1));
      return;
    }
  });

  const header = `变更 · ${state.branch ?? '未连接 Git'} · ${state.files.length} 个文件 ${state.files.length ? `+${totals.additions} -${totals.deletions}${totals.unknown ? ' ?' : ''}` : ''} · ${state.follow ? '跟随' : '手动'}`;
  const footer = state.error
    ? state.error
    : `${current ? `${statusLabel(current)} · ${current.path}` : '未选择文件'} · ↑↓ 选择 · Tab ${state.focus === 'list' ? '滚动 diff' : '回到列表'} · f ${state.follow ? '暂停跟随' : '开启跟随'} · r 刷新 · q 退出`;

  return <Box flexDirection="column" width={columns} height={rows} paddingX={1} backgroundColor={palette.background} overflow="hidden">
    <Text color={palette.accent} backgroundColor={palette.raised} bold>{fitText(header, inner)}</Text>
    {listRows ? <Box flexDirection="column" height={listRows} flexShrink={0} overflow="hidden">
      {state.files.slice(listTop, listTop + listRows).map(file => {
        const selected = file.path === state.selected;
        return <Text key={file.path} color={selected ? palette.foreground : statusColor(file, palette)}
          backgroundColor={selected ? palette.surfaces.selected : undefined} bold={selected}>{fileRow(file, inner)}</Text>;
      })}
    </Box> : null}
    {listRows ? <Text color={palette.secondary}>{fitText(current ? `── ${current.path}${current.originalPath ? ` ← ${current.originalPath}` : ''}  +${stats.additions} -${stats.deletions}  ${wrapped.length ? `${scrolled + 1}-${Math.min(wrapped.length, scrolled + diffRows)}/${wrapped.length}` : ''}` : '── 未选择文件', inner)}</Text> : null}
    <Box flexDirection="column" height={diffRows} flexShrink={0} overflow="hidden">
      {!state.files.length ? <Text color={state.error ? palette.error : palette.secondary}>{fitText(state.error || '工作区没有未提交改动', inner)}</Text> :
        !current ? <Text color={palette.secondary}>{fitText('选择一个文件查看 diff', inner)}</Text> :
        diff.error ? <Text color={palette.error}>{fitText(diff.error, inner)}</Text> :
        !parsed.length ? <Text color={palette.secondary}>{fitText('该文件没有可显示的 diff', inner)}</Text> :
        wrapped.slice(scrolled, scrolled + diffRows).map((line, position) => <Text key={`${scrolled + position}`} color={diffColor(line.kind, palette)}>{line.text}</Text>)}
    </Box>
    <Text color={state.error ? palette.error : palette.secondary}>{fitText(footer, inner)}</Text>
  </Box>;
}
