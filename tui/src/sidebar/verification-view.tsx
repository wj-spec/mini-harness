import React, {useLayoutEffect, useMemo, useRef, useState} from 'react';
import {Box, Text, useInput} from 'ink';
import {createHash} from 'node:crypto';
import {verificationContent, type VerificationSnapshot} from './verification-content.js';
export type {VerificationSnapshot} from './verification-content.js';
import {useExcerptSelection} from './excerpt-selection.js';
import type {ScopedExcerpt} from './excerpt-bar.js';
import {fitText} from '../markdown.js';
import type {Palette} from '../theme.js';
import {parseDiff, wrapDiffLines, type WrappedDiffLine} from './diff.js';
import {useTranscriptMouse} from '../use-transcript-scroll.js';
import type {MouseEvent} from '../mouse.js';

export function VerificationView({snapshot, latest, columns, rows, palette, onRefresh, active = true, rowOffset = 0, onNavigate, pinnedResult = false, onExcerpt}: {
  snapshot: VerificationSnapshot; latest: VerificationSnapshot | null; columns: number; rows: number;
  palette: Palette; onRefresh: () => void;
  active?: boolean; rowOffset?: number; onNavigate?: (event: MouseEvent) => boolean;
  pinnedResult?: boolean; onExcerpt?: (excerpt: ScopedExcerpt) => void;
}) {
  const inner = Math.max(6, columns - 2);
  const bodyRows = Math.max(1, rows - 3);
  const body = verificationContent(snapshot);
  const revision = createHash('sha256').update(body).digest('hex');
  const lines = useMemo(() => wrapDiffLines(parseDiff(body), inner), [body, inner]);
  const [position, setPosition] = useState(0);
  const anchor = useRef<{snapshot: VerificationSnapshot; width: number; lines: WrappedDiffLine[]; position: number} | null>(null);
  const maximum = Math.max(0, lines.length - bodyRows);
  let scroll = Math.min(position, maximum);
  if (anchor.current?.snapshot !== snapshot) scroll = 0;
  else if (anchor.current.width !== inner) {
    const line = anchor.current.lines[anchor.current.position];
    if (line) scroll = Math.max(0, Math.min(maximum, lines.findLastIndex(item => item.sourceLine === line.sourceLine && item.offset <= line.offset)));
  }
  useLayoutEffect(() => {
    anchor.current = {snapshot, width: inner, lines, position: scroll};
    if (scroll !== position) setPosition(scroll);
  }, [snapshot, inner, lines, scroll, position]);
  useInput((input, key) => {
    if (!active || key.eventType === 'release' || key.ctrl || key.meta) return;
    if (input === 'r' && latest) {onRefresh(); return;}
    if (key.home || input === 'g') setPosition(0);
    else if (key.end || input === 'G') setPosition(maximum);
    else if (key.upArrow || key.downArrow || key.pageUp || key.pageDown || input === 'j' || input === 'k') {
      const delta = (key.upArrow || key.pageUp || input === 'k' ? -1 : 1) * (key.pageUp || key.pageDown ? bodyRows - 1 : 1);
      setPosition(Math.max(0, Math.min(maximum, scroll + delta)));
    }
  });
  const selection = useExcerptSelection({body, lines, scroll, top: rowOffset + 3, bottom: rowOffset + rows - 1, columns, identity: revision,
    onSelect: selected => onExcerpt?.({...selected, sessionId: snapshot.sessionId, requestId: snapshot.run.requestId, source: {kind: 'verification', revision}})});
  useTranscriptMouse({enabled: active, top: rowOffset + 3, bottom: rowOffset + rows - 1, left: 1, right: columns,
    onPointer: event => Boolean(onNavigate?.(event)) || selection.pointer(event), onScroll: delta => setPosition(Math.max(0, Math.min(maximum, scroll + delta)))});
  const update = !latest ? '当前无运行 · 保留阅读快照' : JSON.stringify(latest) !== JSON.stringify(snapshot)
    ? '有更新 · r 接入新快照' : pinnedResult ? '结果快照 · 主区可回到当前' : '固定阅读 · 后台刷新不替换正文';
  return <Box flexDirection="column" width={columns} height={rows} paddingX={1} backgroundColor={palette.background} overflow="hidden">
    <Text bold color={palette.accent}>{fitText(`验证记录 · 行 ${scroll + 1}/${lines.length}`, inner)}</Text>
    <Text color={palette.secondary}>{fitText(update, inner)}</Text>
    <Box flexDirection="column" height={bodyRows} flexShrink={0} overflow="hidden">
      {lines.slice(scroll, scroll + bodyRows).map((line, index) => <React.Fragment key={index}>{selection.renderLine(line, scroll + index)}</React.Fragment>)}
    </Box>
    <Text color={palette.secondary}>{fitText('↑↓ 翻阅 · r 接入 · Esc/v 返回 · q 退出', inner)}</Text>
  </Box>;
}
