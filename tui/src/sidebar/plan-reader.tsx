import {inspectionPlanLabel} from '../inspection.js';
import {reviewKey, type ReviewRef} from '../protocol.js';
import {useExcerptSelection} from './excerpt-selection.js';
import type {ScopedExcerpt} from './excerpt-bar.js';
import type {ExcerptPoint} from './draft-channel.js';
import React, {useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {Box, Text, useInput} from 'ink';
import {ActionButtons, type ActionTarget} from '../action-buttons.js';
import {useTranscriptMouse} from '../use-transcript-scroll.js';
import {fitText, visibleText} from '../markdown.js';
import type {Palette} from '../theme.js';
import type {SidebarOwner} from './channel.js';
import type {SidebarPlanSummary} from './signal.js';
import {requestPlanPage} from './requests.js';
import {parseDiff, wrapDiffLines, type WrappedDiffLine} from './diff.js';
import type {MouseEvent} from '../mouse.js';

export type PlanPageReader = (target: ReviewRef, cursor: number) => Promise<{body: string; nextCursor: number | null}>;
type Page = {body: string; cursor: number; next: number | null; previous: number[]; loaded: boolean};
type Action = 'previous' | 'next' | 'retry' | 'back';

export function PlanReader({workspace, owner, sessionId, plan, update, columns, rows, palette, onClose, active = true, rowOffset = 0, onNavigate, requestId = null, onExcerpt, extending = false, excerpt, readPage}: {
  workspace: string; owner: SidebarOwner; sessionId: string; plan: SidebarPlanSummary;
  update: string; columns: number; rows: number; palette: Palette; onClose: () => void;
  readPage?: PlanPageReader;
  requestId?: string | null; onExcerpt?: (excerpt: ScopedExcerpt) => void; extending?: boolean; excerpt?: ScopedExcerpt | null;
  active?: boolean; rowOffset?: number; onNavigate?: (event: MouseEvent) => boolean;
}) {
  const [page, setPage] = useState<Page>({body: '', cursor: 0, next: null, previous: [], loaded: false});
  const [pending, setPending] = useState(false);
  const [problem, setProblem] = useState('');
  const retry = useRef({cursor: 0, previous: [] as number[]});
  const pages = useRef(new Map<number, string>());
  const controller = useRef<AbortController | null>(null);
  const load = async (cursor: number, previous: number[]) => {
    controller.current?.abort(); const active = new AbortController(); controller.current = active;
    retry.current = {cursor, previous}; setPending(true); setProblem('');
    try {
      const result = readPage ? {outcome: 'page' as const, ...await readPage(plan.target!, cursor), message: ''}
        : await requestPlanPage(workspace, owner, sessionId, plan.target!, cursor, active.signal);
      if (active.signal.aborted) return;
      if (result.outcome === 'error') setProblem(result.message);
      else {
        pages.current.delete(cursor); pages.current.set(cursor, result.body);
        while (pages.current.size > 3) pages.current.delete(pages.current.keys().next().value!);
        setPage({body: result.body, cursor, next: result.nextCursor, previous, loaded: true});
      }
    } catch (error) {if (!active.signal.aborted) setProblem(error instanceof Error ? error.message : '正文读取失败');}
    finally {if (!active.signal.aborted) setPending(false);}
  };
  useEffect(() => {void load(0, []); return () => controller.current?.abort();}, []);
  const inner = Math.max(6, columns - 2); const bodyRows = Math.max(1, rows - 4);
  const fallback = [plan.objective, `${inspectionPlanLabel(plan.relation)} · ${plan.target?.plan_id}`,
    `任务 ${plan.target?.plan_task_id}`, `哈希 ${plan.target?.plan_hash.slice(0, 16)}`].join('\n');
  const lines = useMemo(() => wrapDiffLines(parseDiff(page.loaded ? page.body : fallback), inner), [page, fallback, inner]);
  const [position, setPosition] = useState(0);
  const maximum = Math.max(0, lines.length - bodyRows);
  const anchor = useRef<{page: Page; width: number; lines: WrappedDiffLine[]; position: number} | null>(null);
  let scroll = Math.min(position, maximum);
  if (anchor.current?.page !== page) scroll = 0;
  else if (anchor.current.width !== inner) {
    const line = anchor.current.lines[anchor.current.position];
    if (line) scroll = Math.max(0, Math.min(maximum, lines.findLastIndex(item => item.sourceLine === line.sourceLine && item.offset <= line.offset)));
  }
  useLayoutEffect(() => {
    anchor.current = {page, width: inner, lines, position: scroll};
    if (scroll !== position) setPosition(scroll);
  }, [page, inner, lines, scroll, position]);
  const move = (delta: number) => setPosition(Math.max(0, Math.min(maximum, scroll + delta)));
  const buttons: {action: Action; label: string}[] = [
    ...(!pending && page.previous.length ? [{action: 'previous' as const, label: '上页'}] : []),
    ...(!pending && problem ? [{action: 'retry' as const, label: '重试'}] : !pending && page.next !== null ? [{action: 'next' as const, label: '下页'}] : []),
    {action: 'back', label: '返回'},
  ];
  const activate = (action: Action) => {
    if (action === 'back') {onClose(); return;}
    if (pending) return;
    if (action === 'retry') void load(retry.current.cursor, retry.current.previous);
    else if (action === 'next' && page.next !== null) void load(page.next, [...page.previous, page.cursor]);
    else if (action === 'previous' && page.previous.length) void load(page.previous.at(-1)!, page.previous.slice(0, -1));
  };
  useInput((input, key) => {
    if (!active || key.eventType === 'release' || key.ctrl || key.meta) return;
    if (key.rightArrow) activate('next');
    else if (key.leftArrow) activate('previous');
    else if (input === 'r' && problem) activate('retry');
    else if (key.home || input === 'g') setPosition(0);
    else if (key.end || input === 'G') setPosition(maximum);
    else if (key.upArrow || key.downArrow || key.pageUp || key.pageDown || input === 'j' || input === 'k') {
      move((key.upArrow || key.pageUp || input === 'k' ? -1 : 1) * (key.pageUp || key.pageDown ? Math.max(1, bodyRows - 1) : 1));
    }
  });
  const offsetAt = (point: ExcerptPoint, end = false) => {
    const source = page.body.split('\n');
    let offset = Array.from(source.slice(0, point.line).join('\n') + (point.line ? '\n' : '')).length;
    let shown = 0;
    for (const char of Array.from(source[point.line]!)) {
      const width = visibleText(char).length;
      if (shown + width > point.offset) {if (end && shown < point.offset) offset++; break;}
      shown += width; offset++;
    }
    return page.cursor + offset;
  };
  const selection = useExcerptSelection({body: page.body, lines, scroll, top: rowOffset + 3, bottom: rowOffset + rows - 2, columns,
    identity: `${reviewKey(plan.target!)}:${page.cursor}:${page.body}`,
    onSelect: selected => {
      if (!page.loaded || pending) return;
      let cursor = offsetAt(selected.start); const end = offsetAt(selected.end, true); let text = Array.from(page.body).slice(cursor - page.cursor, end - page.cursor).join('');
      if (extending) {
        if (excerpt?.source.kind !== 'plan' || reviewKey(excerpt.source.target) !== reviewKey(plan.target!)) {
          setProblem('续选来源不一致，请取消续选后重选'); return;
        }
        cursor = excerpt.source.cursor;
        if (end <= cursor || end - cursor > 32_000) {setProblem('续选终点无效或范围过大，原选区保留'); return;}
        text = ''; let position = cursor;
        while (position < end) {
          const item = [...pages.current].find(([start, body]) => start <= position && start + Array.from(body).length > position);
          if (!item) {setProblem('跨页内容未保留，请缩小范围重选'); return;}
          const chars = Array.from(item[1]); const chunk = chars.slice(position - item[0], end - item[0]);
          text += chunk.join(''); position += chunk.length;
        }
      }
      text = visibleText(text);
      if (Buffer.byteLength(text) > 32_000) {setProblem('选区超过 32KB，原选区保留'); return;}
      setProblem(''); onExcerpt?.({...selected, text, sessionId, requestId,
        source: {kind: 'plan', target: plan.target!, cursor, length: end - cursor, revision: plan.target!.plan_hash}});
    }});
  const targets = useRef<ActionTarget<Action>[]>([]);
  const press = useRef<{action: Action; layout: string} | null>(null);
  const layout = `${page.cursor}:${pending}:${problem}:${columns}:${rows}`;
  useTranscriptMouse({enabled: active, top: rowOffset + 3, bottom: rowOffset + rows - 2, left: 1, right: columns, onScroll: move,
    onPointer: event => {
      if (onNavigate?.(event)) return true;
      if (event.type === 'wheel') return false;
      if (event.button !== 0) return true;
      const hit = targets.current.find(item => event.row >= item.top && event.row <= item.bottom && event.column >= item.left && event.column <= item.right);
      if (event.type === 'press') press.current = hit ? {action: hit.action, layout} : null;
      if (event.type === 'release') {
        if (hit && press.current?.action === hit.action && press.current.layout === layout) activate(hit.action);
        press.current = null;
      }
      return Boolean(hit) || selection.pointer(event);
    }});
  const progress = plan.doneSteps === null ? '进度未知' : `${plan.doneSteps}/${plan.stepCount}步`;
  const notice = pending ? '正在读取正文…' : problem;
  const status = update.startsWith('有更新') || update.startsWith('当前无计划')
    ? `${update}${notice ? ' · ' + notice : ''}` : notice || update;
  return <Box flexDirection="column" width={columns} height={rows} backgroundColor={palette.background} overflow="hidden">
    <Box paddingX={1}><Text color={palette.accent} bold>{fitText(`计划 v${plan.version} · 第 ${page.previous.length + 1} 页 · ${plan.relation?.startsWith('revision-') ? inspectionPlanLabel(plan.relation) + ' · ' : ''}${plan.status} ${progress}`, inner)}</Text></Box>
    <Box paddingX={1}><Text color={problem ? palette.warning : palette.secondary}>{fitText(status, inner)}</Text></Box>
    <Box paddingX={1} flexDirection="column" height={bodyRows} flexShrink={0} overflow="hidden">
      {lines.slice(scroll, scroll + bodyRows).map((line, index) => <React.Fragment key={index}>{selection.renderLine(line, scroll + index)}</React.Fragment>)}
    </Box>
    <ActionButtons buttons={buttons} selected={null} columns={columns} height={1} palette={palette} targets={targets}/>
    <Box paddingX={1}><Text color={palette.secondary}>{fitText('↑↓/滚轮 翻阅 · ←→ 换页 · Esc 返回', inner)}</Text></Box>
  </Box>;
}
