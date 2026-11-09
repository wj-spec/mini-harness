import {inspectionPlanLabel} from '../inspection.js';
import {ExcerptBar, excerptBarRows, type ScopedExcerpt} from './excerpt-bar.js';
import {useExcerptSelection} from './excerpt-selection.js';
import React, {useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {Box, Text, useInput, useWindowSize} from 'ink';
import stringWidth from 'string-width';
import path from 'node:path';
import {fitText} from '../markdown.js';
import {getPalette, type Palette, type ThemeName} from '../theme.js';
import {terminalColorLevel} from '../terminal-colors.js';
import {
  changeCounts, statusLetter, statusLabel, totalCounts, type ChangedFile,
} from './git.js';
import {parseDiff, wrapDiffLines, type DiffKind, type WrappedDiffLine} from './diff.js';
import {
  scrollDiff, selectDelta, selectedFile, toggleFocus, toggleFollow,
} from './state.js';
import {readSidebarSignal, type SidebarSignal} from './signal.js';
import {useSidebar, type SidebarReaders} from './use-sidebar.js';
import {sameOwner, type SidebarOwner} from './channel.js';
import {VerificationView, type VerificationSnapshot} from './verification-view.js';
import {PlanReader, type PlanPageReader} from './plan-reader.js';
import {reviewKey} from '../protocol.js';
import {InspectionTabs, type InspectionAction, type InspectionPage} from './inspection-tabs.js';
import {ActionButtons, type ActionTarget} from '../action-buttons.js';
import {useTranscriptMouse} from '../use-transcript-scroll.js';
import type {MouseEvent} from '../mouse.js';

export type SidebarProps = SidebarReaders & {
  workspace: string;
  owner?: SidebarOwner;
  theme?: ThemeName;
  refreshMs?: number;
  size?: {columns: number; rows: number};
  readSignal?: (workspace: string, owner?: SidebarOwner) => SidebarSignal | null;
  signalPollMs?: number;
  onQuit?: () => void;
  onSelectionChange?: (selected: boolean) => void;
  copyText?: (text: string) => string | null;
  active?: boolean;
  readPlanPage?: PlanPageReader;
  embedded?: {status: string; onReturn: () => void; onCompanion: () => void};
};

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

function compactPath(path: string, width: number): string {
  if (stringWidth(path) <= width) return path;
  const name = path.split('/').at(-1) ?? path;
  if (stringWidth(name) + 2 <= width) return `…/${name}`;
  return fitText(name, width);
}

function fileRow(file: ChangedFile, columns: number, agent = false, selected = false): string {
  const letter = statusLetter(file);
  const counts = changeCounts(file);
  const prefix = `${selected ? '›' : ' '}${agent ? '●' : ' '}${letter} `;
  const prefixWidth = stringWidth(prefix);
  const countsWidth = stringWidth(counts);
  const showCounts = countsWidth > 0 && prefixWidth + countsWidth + 8 <= columns;
  const pathWidth = Math.max(1, columns - prefixWidth - (showCounts ? countsWidth : 0));
  const path = compactPath(file.path, pathWidth);
  return `${prefix}${padTo(path, pathWidth)}${showCounts ? counts : ''}`;
}

export function SidebarApp({
  workspace, owner, theme = 'system', refreshMs = 1000, size, readStatus, readDiff, readSignal, signalPollMs = 200, onQuit, copyText, onSelectionChange, active = true, embedded, readPlanPage,
}: SidebarProps) {
  const terminal = useWindowSize();
  const columns = Math.max(12, size?.columns ?? terminal.columns ?? 80);
  const height = Math.max(6, size?.rows ?? terminal.rows ?? 24);
  const [excerpt, setExcerpt] = useState<ScopedExcerpt | null>(null);
  const [extending, setExtending] = useState(false);
  const selectExcerpt = (value: ScopedExcerpt) => {setExcerpt(value); setExtending(false);};
  useEffect(() => {onSelectionChange?.(Boolean(excerpt));}, [Boolean(excerpt), onSelectionChange]);
  const rows = height - 1 - (embedded ? 2 : 0) - (excerpt ? excerptBarRows(columns, excerpt.source.kind === 'plan') : 0);
  const inner = Math.max(6, columns - 2);
  const palette = getPalette(theme, terminalColorLevel());

  const [fileSource, setFileSource] = useState<SidebarSignal['fileSource']>(owner ? null : undefined);
  const sourceKey = JSON.stringify(fileSource && [fileSource.root, fileSource.identity]);
  const {state, updateState: setState, reading, activityPending, refresh, followFiles} = useSidebar(workspace, refreshMs, {readStatus, readDiff}, fileSource);
  const diff = reading.shown;
  const [agentFiles, setAgentFiles] = useState<ReadonlySet<string>>(() => new Set());
  const [planSummary, setPlanSummary] = useState<SidebarSignal['plan']>(undefined);
  const [runScope, setRunScope] = useState<SidebarSignal['run']>(undefined);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [readVersions, setReadVersions] = useState(() => new Map<string, string>());
  useEffect(() => {setReadVersions(new Map());}, [workspace, sourceKey, owner?.instanceId, owner?.parentPid, sessionId]);
  const [pathOpen, setPathOpen] = useState(false);
  const [pathScroll, setPathScroll] = useState(0);
  const [page, setPage] = useState<InspectionPage>('changes');
  const planOpen = page === 'plan';
  const [planReading, setPlanReading] = useState<SidebarSignal['plan']>(undefined);
  const [planReadingScope, setPlanReadingScope] = useState<{run: SidebarSignal['run']; sessionId: string | null}>({run: undefined, sessionId: null});
  const [planIndex, setPlanIndex] = useState(0);
  const [verification, setVerification] = useState<VerificationSnapshot | null>(null);
  const [verificationReading, setVerificationReading] = useState<VerificationSnapshot | null>(null);
  const signalRevision = useRef(0);
  const navigationId = useRef('');
  const [resultMode, setResultMode] = useState(false);
  const readSignalRef = useRef(readSignal ?? readSidebarSignal);
  readSignalRef.current = readSignal ?? readSidebarSignal;

  // 主 TUI 每次工具结果/回合状态变化都会写信号；侧栏收到新版本立即刷新并标记 Agent 改过的文件。
  useEffect(() => {
    signalRevision.current = 0;
    navigationId.current = ''; setResultMode(false); setExcerpt(null); setExtending(false);
    setAgentFiles(new Set()); setPlanSummary(undefined); setRunScope(undefined); setPage('changes'); setPlanReading(undefined);
    setVerification(null); setVerificationReading(null);
    setSessionId(null); setPlanReadingScope({run: undefined, sessionId: null});
    setFileSource(owner ? null : undefined);
    followFiles([]);
    if (!signalPollMs || !owner) return;
    const tick = () => {
      const signal = readSignalRef.current(workspace, owner);
      if (!signal || !sameOwner(signal.scope?.owner, owner) || signal.scope?.workspace !== path.resolve(workspace) ||
          !Number.isSafeInteger(signal.revision) || (signal.revision ?? 0) <= signalRevision.current) return;
      signalRevision.current = signal.revision!;
      setFileSource(signal.fileSource);
      setAgentFiles(new Set(signal.files));
      setPlanSummary(signal.plan);
      setRunScope(signal.run);
      setSessionId(signal.scope.sessionId);
      const snapshot = signal.run && signal.verification ? {run: signal.run, verification: signal.verification, sessionId: signal.scope.sessionId} : null;
      setVerification(snapshot);
      followFiles(signal.files);
      if (signal.navigation && signal.navigation.id !== navigationId.current) {
        navigationId.current = signal.navigation.id;
        setResultMode(signal.navigation.mode === 'result'); setPage(signal.navigation.page); setPathOpen(false);
        setPlanReading(signal.navigation.page === 'plan' ? signal.plan : undefined);
        setPlanReadingScope({run: signal.run, sessionId: signal.scope.sessionId}); setPlanIndex(0);
        setVerificationReading(signal.navigation.page === 'verification' ? snapshot : null);
        setState(previous => ({...previous, follow: true}));
        if (signal.navigation.page !== 'changes') setState(previous => ({...previous, follow: false}));
      }
    };
    tick();
    const timer = setInterval(tick, signalPollMs);
    return () => clearInterval(timer);
  }, [workspace, owner?.instanceId, owner?.parentPid, signalPollMs, followFiles]);

  const parsed = useMemo(() => parseDiff(diff.text), [diff.text]);
  const wrapped = useMemo(() => wrapDiffLines(parsed, inner), [parsed, inner]);
  const totals = totalCounts(state.files);
  const listRows = state.files.length && rows >= 8 ? Math.min(state.files.length, Math.max(1, Math.floor((rows - 5) * 0.32))) : 0;
  const runRows = runScope && height >= 12 ? 1 : 0;
  const diffRows = Math.max(1, rows - listRows - 5 - runRows);
  const index = state.files.findIndex(file => file.path === state.selected);
  const listTop = listRows ? Math.max(0, Math.min(index - listRows + 2, Math.max(0, state.files.length - listRows))) : 0;
  const maxScroll = Math.max(0, wrapped.length - diffRows);
  const anchor = useRef<{path: string | null; width: number; lines: WrappedDiffLine[]; scroll: number} | null>(null);
  let scrolled = Math.min(state.diffScroll, maxScroll);
  if (anchor.current?.path === diff.path && anchor.current.width !== inner) {
    const line = anchor.current.lines[anchor.current.scroll];
    if (line) {
      const position = wrapped.findLastIndex(item => item.sourceLine === line.sourceLine && item.offset <= line.offset);
      if (position >= 0) scrolled = Math.min(position, maxScroll);
    }
  }
  useLayoutEffect(() => {
    anchor.current = {path: diff.path, width: inner, lines: wrapped, scroll: scrolled};
    if (scrolled !== state.diffScroll) setState(previous => ({...previous, diffScroll: scrolled}));
  }, [diff.path, inner, wrapped, scrolled, state.diffScroll, setState]);
  const current = diff.file ?? selectedFile(state);
  const readable = Boolean(diff.path && /^[a-f0-9]{64}$/.test(diff.revision ?? '') && !diff.error && !state.error && !diff.truncated && diff.text.trim());
  const isRead = Boolean(readable && readVersions.get(diff.path!) === diff.revision);
  const readLabel = isRead ? reading.pending?.revision && reading.pending.revision !== diff.revision ? '新版未读 · 旧版已读' : '已读' : '未读';
  const readTargets = useRef<ActionTarget<'toggle'>[]>([]);
  const readPress = useRef<string | null>(null);
  const readIdentity = `${sourceKey}:${diff.path}:${diff.revision}:${columns}:${rows}`;

  const pathLines = useMemo(() => wrapDiffLines(parseDiff(current ? `${fileSource?.root ?? workspace}\n${current.path}${current.originalPath ? `\n← ${current.originalPath}` : ''}` : '未选择文件'), inner), [workspace, sourceKey, current?.path, current?.originalPath, inner]);
  const quit = () => embedded ? embedded.onReturn() : onQuit?.();
  const embeddedTargets = useRef<ActionTarget<'return' | 'companion'>[]>([]);
  const embeddedPress = useRef<string | null>(null);

  const navigate = (next: InspectionPage) => {
    setPathOpen(false);
    if (next === 'plan' && !planReading && planSummary) {
      setPlanReading(planSummary); setPlanReadingScope({run: runScope, sessionId}); setPlanIndex(0);
    }
    if (next === 'verification' && !verificationReading && verification) setVerificationReading(verification);
    setState(previous => ({...previous, follow: false}));
    setPage(next);
  };
  useEffect(() => {
    if (page === 'plan' && !planReading && planSummary) {
      setPlanReading(planSummary); setPlanReadingScope({run: runScope, sessionId}); setPlanIndex(0);
    }
    if (page === 'verification' && !verificationReading && verification) setVerificationReading(verification);
  }, [page, planReading, planSummary, runScope, sessionId, verificationReading, verification]);
  const planChanged = Boolean(planSummary && JSON.stringify([planReading, planReadingScope]) !== JSON.stringify([planSummary, {run: runScope, sessionId}]));
  const verificationChanged = Boolean(verification && JSON.stringify(verificationReading) !== JSON.stringify(verification));
  const pendingUpdate = page === 'plan' ? planChanged : page === 'verification' ? verificationChanged : Boolean(reading.pending || activityPending);
  const followUpdate = page === 'changes' && activityPending && !reading.pending;
  const adopt = () => {
    if (page === 'plan' && planSummary) {setPlanReading(planSummary); setPlanReadingScope({run: runScope, sessionId}); setPlanIndex(0);}
    else if (page === 'verification' && verification) setVerificationReading(verification);
    else if (followUpdate) setState(previous => ({...previous, follow: true}));
    else if (page === 'changes') refresh(true);
  };
  const diffSelection = useExcerptSelection({body: diff.text, lines: wrapped, scroll: scrolled, top: 5 + listRows + runRows,
    bottom: 4 + listRows + runRows + diffRows, columns, identity: JSON.stringify([sourceKey, diff.path, diff.revision]),
    onStart: () => setState(previous => ({...previous, follow: false})),
    onSelect: selected => {if (diff.path && diff.revision && !diff.error) selectExcerpt({...selected, sessionId, requestId: runScope?.requestId ?? null,
      source: {kind: 'diff', path: diff.path, revision: diff.revision, ...(fileSource ? {fileSource} : {})}});},
  });
  const tabTargets = useRef<ActionTarget<InspectionAction>[]>([]);
  const tabPress = useRef<{page: InspectionAction; layout: string} | null>(null);
  const tabLayout = `${columns}:${height}:${page}:${pendingUpdate}:${followUpdate}`;
  const onNavigate = (event: MouseEvent): boolean => {
    if (!active) return false;
    if (embedded && event.type !== 'wheel' && event.button === 0) {
      const hit = embeddedTargets.current.find(target => event.row >= target.top && event.row <= target.bottom && event.column >= target.left && event.column <= target.right);
      const key = `${hit?.action}:${columns}:${height}`;
      if (event.type === 'press') embeddedPress.current = hit ? key : null;
      if (event.type === 'release') {
        if (hit && embeddedPress.current === key) {if (hit.action === 'return') embedded.onReturn(); else embedded.onCompanion();}
        embeddedPress.current = null;
      }
      if (hit) return true;
    } else embeddedPress.current = null;
    if (page === 'changes' && !pathOpen && readable && event.type !== 'wheel' && event.button === 0) {
      const hit = readTargets.current.some(target => event.row >= target.top && event.row <= target.bottom && event.column >= target.left && event.column <= target.right);
      if (event.type === 'press') readPress.current = hit ? readIdentity : null;
      if (event.type === 'release') {
        if (hit && readPress.current === readIdentity) setReadVersions(previous => {
          const next = new Map(previous); next.delete(diff.path!); if (!isRead) next.set(diff.path!, diff.revision!);
          while (next.size > 200) next.delete(next.keys().next().value!); return next;
        });
        readPress.current = null;
      }
      if (hit) return true;
    } else readPress.current = null;
    if (event.type === 'wheel' || event.button !== 0) return false;
    const hit = tabTargets.current.find(target => event.row >= target.top && event.row <= target.bottom && event.column >= target.left && event.column <= target.right);
    if (event.type === 'press') tabPress.current = hit ? {page: hit.action, layout: tabLayout} : null;
    if (event.type === 'release') {
      if (hit && tabPress.current?.page === hit.action && tabPress.current.layout === tabLayout) {if (hit.action === 'refresh') adopt(); else navigate(hit.action);}
      tabPress.current = null;
    }
    return Boolean(hit);
  };
  useTranscriptMouse({enabled: active && (page === 'changes' || page === 'plan' && !(owner && planReading?.target && planReadingScope.sessionId) || page === 'verification' && !verificationReading),
    top: 4 + listRows + runRows, bottom: rows - 1, left: 1, right: columns, onPointer: event => onNavigate(event) || (page === 'changes' && !pathOpen && diffSelection.pointer(event)),
    onScroll: delta => {if (page === 'changes' && !pathOpen) setState(previous => scrollDiff(previous, delta, maxScroll));}});

  useInput((input, key) => {
    if (!active || key.eventType === 'release') return;
    if (embedded && key.ctrl) return;
    if (embedded && key.escape && !pathOpen) {quit(); return;}
    if (key.ctrl && input === 'c') {quit(); return;}
    if (!key.ctrl && !key.meta && input === 'q') {quit(); return;}
    if (!key.ctrl && !key.meta && (input === 'p' || input === 'v')) {
      const next = input === 'p' ? 'plan' : 'verification'; navigate(page === next ? 'changes' : next); return;
    }
    if (key.escape && page !== 'changes') {navigate('changes'); return;}
    if (page === 'verification') return;
    if (planOpen) {
      if (input === 'r' && planSummary) {adopt(); return;}
      if (owner && planReading?.target && planReadingScope.sessionId) return;
      if (key.upArrow || key.downArrow || input === 'k' || input === 'j') {
        const length = planReading?.steps?.length ?? 0;
        setPlanIndex(previous => Math.max(0, Math.min(length - 1, previous + (key.upArrow || input === 'k' ? -1 : 1))));
      }
      return;
    }
    if (pathOpen) {
      if (key.escape || input === 'o') setPathOpen(false);
      else if (key.upArrow || key.downArrow || key.pageUp || key.pageDown) {
        const delta = (key.upArrow || key.pageUp ? -1 : 1) * (key.pageUp || key.pageDown ? rows - 2 : 1);
        setPathScroll(previous => Math.max(0, Math.min(Math.max(0, pathLines.length - (rows - 2)), previous + delta)));
      }
      return;
    }
    if (!key.ctrl && !key.meta && input === 'o') {
      setState(previous => ({...previous, follow: false}));
      setPathOpen(true); setPathScroll(0); return;
    }
    if (!key.ctrl && !key.meta && input === 'r') {
      refresh(true); return;
    }
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

  const acceptanceLabel = planSummary?.acceptance === true ? ' ✓' : planSummary?.acceptance === false ? ' ✗' : '';
  const planProgress = planSummary?.doneSteps === null ? '进度未知' : `${planSummary?.doneSteps}/${planSummary?.stepCount}步`;
  const planLabel = planSummary
    ? ` · 计划 v${planSummary.version} ${planSummary.stale ? '待核对' : planSummary.status} ${planProgress}${acceptanceLabel}`
    : '';
  const header = `${resultMode ? '结果快照 · ' : ''}${state.follow ? '跟随' : '固定'} · 焦点:${state.focus === 'list' ? '文件' : 'diff'}${reading.pending || activityPending ? ' · 有更新' : ''}${reading.pending?.error ? ' · 旧版' : ''}${planLabel}`;
  const directoryLabel = fileSource && fileSource.root !== path.resolve(workspace) ? '任务目录' : '工作区';
  const scope = `${directoryLabel}${state.head ? ' vs HEAD' : '（无 HEAD）'}${!owner ? ' · 未绑定主会话' : agentFiles.size ? ` · ● 本轮触及 ${agentFiles.size}` : ''} · ${state.branch ?? '未连接 Git'} · ${state.files.length} 文件 +${totals.additions} -${totals.deletions}${totals.unknown ? ' ?' : ''}`;
  const footer = planOpen ? '↑↓ 选择 · r 接入 · Esc/p 返回 · q 退出'
    : `Tab 焦点 · ↑↓ ${state.focus === 'list' ? '选择' : '滚动'} · o 路径`;
  const actions = `r 接入  f ${state.follow ? '固定' : '跟随'}${planSummary ? '  p 计划' : ''}${verification ? '  v 验证' : ''}  q 退出`;
  const runLabel = runScope ? `${{running: '运行中', finished: '已结束', unknown: '结果未知', restored: '恢复快照'}[runScope.status]}${runScope.segment !== null ? ` · 段 ${runScope.segment}` : ' · 未绑定运行段'} · ${runScope.objective}` : '';

  const displayedVerification = verificationReading ?? verification;
  const verificationView = displayedVerification ? <VerificationView snapshot={displayedVerification} latest={verification} columns={columns} rows={rows}
    active={active && page === 'verification'} rowOffset={1} onNavigate={onNavigate} pinnedResult={resultMode} onExcerpt={selectExcerpt}
    palette={palette} onRefresh={() => {if (verification) setVerificationReading(verification);}}/> : null;

  const pathView = <Box flexDirection="column" width={columns} height={rows} paddingX={1} overflow="hidden">
    <Text color={palette.accent}>文件路径 · ↑↓ 翻阅</Text>
    <Box flexDirection="column" height={rows - 2} flexShrink={0} overflow="hidden">
      {pathLines.slice(pathScroll, pathScroll + rows - 2).map((line, index) => <Text key={index}>{line.text}</Text>)}
    </Box>
    <Text color={palette.secondary}>Esc/o 返回 · q 退出</Text>
  </Box>;

  const planView = (() => {
    if (!planReading) return null;
    const planUpdate = !planSummary ? '当前无计划 · 保留阅读快照' : JSON.stringify([planReading, planReadingScope]) !== JSON.stringify([planSummary, {run: runScope, sessionId}])
      ? '有更新 · r 接入新快照' : resultMode ? '结果快照 · 主区可回到当前' : '固定阅读 · 后台刷新不替换正文';
    if (owner && planReading.target && planReadingScope.sessionId) return <PlanReader
      readPage={readPlanPage} key={reviewKey(planReading.target) + JSON.stringify(planReadingScope)} workspace={workspace} owner={owner}
      active={active && page === 'plan'} rowOffset={1} onNavigate={onNavigate} sessionId={planReadingScope.sessionId} plan={planReading} update={planUpdate} columns={columns} rows={rows} palette={palette}
      onClose={() => navigate('changes')} requestId={planReadingScope.run?.requestId ?? null} onExcerpt={selectExcerpt} extending={extending} excerpt={excerpt}/>;
    const displayedPlan = planReading;
    const planProgress = displayedPlan.doneSteps === null ? '进度未知' : `${displayedPlan.doneSteps}/${displayedPlan.stepCount}步`;
    const steps = displayedPlan.steps ?? [];
    const bodyRows = Math.max(1, rows - 3);
    const top = Math.max(0, Math.min(planIndex - Math.floor(bodyRows / 2), Math.max(0, steps.length - bodyRows)));
    const acceptance = displayedPlan.acceptance === true ? '✓' : displayedPlan.acceptance === false ? '✗' : '';
    return <Box flexDirection="column" width={columns} height={rows} paddingX={1} backgroundColor={palette.background} overflow="hidden">
      <Text color={palette.accent} backgroundColor={palette.raised} bold>{fitText(`计划 v${displayedPlan.version} ${displayedPlan.stale ? '待核对' : displayedPlan.status} ${planProgress} ${acceptance}${planReadingScope.run?.segment ? ` · 段 ${planReadingScope.run.segment}` : ''}`, inner)}</Text>
      <Text color={palette.secondary}>{fitText(planUpdate, inner)}</Text>
      <Box flexDirection="column" height={bodyRows} flexShrink={0} overflow="hidden">
        {!steps.length ? <>
          <Text>{fitText(displayedPlan.objective, inner)}</Text>
          {displayedPlan.target ? <>
            <Text>{fitText(`${inspectionPlanLabel(displayedPlan.relation)} · ${displayedPlan.target.plan_id} v${displayedPlan.target.plan_version}`, inner)}</Text>
            <Text>{fitText(`任务 ${displayedPlan.target.plan_task_id}`, inner)}</Text>
            <Text>{fitText(`哈希 ${displayedPlan.target.plan_hash.slice(0, 16)}`, inner)}</Text>
          </> : null}
          <Text color={palette.secondary}>{fitText(displayedPlan.problem || '步骤进度未读取；在主区 /plan 查看正文', inner)}</Text>
        </> : null}
        {steps.slice(top, top + bodyRows).map((step, offset) => {
          const index = top + offset;
          const selected = index === planIndex;
          const color = step.status === 'done' ? palette.success : step.status === 'failed' ? palette.error : step.status === 'in_progress' ? palette.warning : palette.foreground;
          return <Text key={step.id} color={selected ? palette.foreground : color}
            backgroundColor={selected ? palette.surfaces.selected : undefined} bold={selected}>
            {`${selected ? '› ' : '  '}${step.status.padEnd(12)} ${step.id} ${step.title}`}
          </Text>;
        })}
      </Box>
      <Text color={palette.secondary}>{fitText(footer, inner)}</Text>
    </Box>;
  })();

  const changesView = <Box flexDirection="column" width={columns} height={rows} paddingX={1} backgroundColor={palette.background} overflow="hidden">
    <Text color={palette.accent} backgroundColor={palette.raised} bold>{fitText(header, inner)}</Text>
    <Text color={palette.secondary}>{fitText(scope, inner)}</Text>
    {runRows ? <Text color={runScope?.status === 'unknown' ? palette.warning : palette.secondary}>{fitText(runLabel, inner)}</Text> : null}
    {listRows ? <Box flexDirection="column" height={listRows} flexShrink={0} overflow="hidden">
      {state.files.slice(listTop, listTop + listRows).map(file => {
        const selected = file.path === state.selected;
        return <Text key={file.path} color={selected ? palette.foreground : statusColor(file, palette)}
          backgroundColor={selected ? palette.surfaces.selected : undefined} bold={selected}>{fileRow(file, inner, agentFiles.has(file.path), selected)}</Text>;
      })}
    </Box> : null}
    <Text color={state.error || reading.pending?.error ? palette.warning : palette.secondary}>{fitText(state.error || reading.pending?.error || (current ? `${readable ? readLabel + ' · ' : ''}${diff.truncated ? '已截断 · ' : ''}${statusLabel(current)} · ${compactPath(current.path, Math.max(8, inner - (diff.truncated ? 25 : 16)))} · 行${wrapped[scrolled] ? wrapped[scrolled]!.sourceLine + 1 : 0}` : '未选择文件'), inner)}</Text>
    <Box flexDirection="column" height={diffRows} flexShrink={0} overflow="hidden">
      {!state.files.length && !diff.path ? <Text color={state.error ? palette.error : palette.secondary}>{fitText(state.error || '工作区没有未提交改动', inner)}</Text> :
        diff.error ? <Text color={palette.error}>{fitText(diff.error, inner)}</Text> :
        !parsed.length ? <Text color={palette.secondary}>{fitText(!diff.path && state.selected ? '正在读取 diff…' : '该文件没有可显示的 diff', inner)}</Text> :
        wrapped.slice(scrolled, scrolled + diffRows).map((line, position) => <React.Fragment key={`${scrolled + position}`}>{diffSelection.renderLine(line, scrolled + position, diffColor(line.kind, palette))}</React.Fragment>)}
    </Box>
    <Text color={palette.secondary}>{fitText(footer, inner)}</Text>
    {readable ? <Box height={1} width={inner}>
      <ActionButtons buttons={[{action: 'toggle', label: isRead ? '撤销' : '标已读'}]} selected={null} columns={10} height={1} paddingX={0} palette={palette} targets={readTargets}/>
      {inner > 12 ? <Text color={palette.secondary}>{fitText(' q退出 · ' + actions, inner - 10)}</Text> : null}
    </Box> : <Text color={palette.secondary}>{fitText(actions, inner)}</Text>}
  </Box>;
  const empty = (label: string) => <Box flexDirection="column" width={columns} height={rows} paddingX={1}>
    <Text color={palette.secondary}>{label}</Text>
    <Text>{owner ? '可在主工作台生成计划或执行任务' : '未绑定主会话 · 请从主工作台 /sidebar 打开'}</Text>
  </Box>;
  return <Box flexDirection="column" width={columns} height={height} backgroundColor={palette.background} overflow="hidden">
    <InspectionTabs page={page} columns={columns} palette={palette} targets={tabTargets} pending={pendingUpdate} follow={followUpdate}/>
    <Box display={page === 'changes' ? 'flex' : 'none'}>{pathOpen ? pathView : changesView}</Box>
    <Box display={page === 'plan' ? 'flex' : 'none'}>{planView ?? empty(resultMode ? '该结果未记录计划' : '当前没有可读取的计划')}</Box>
    <Box display={page === 'verification' ? 'flex' : 'none'}>{verificationView ?? empty(resultMode ? '该结果未记录验证信息' : '当前没有运行验证记录')}</Box>
    {excerpt ? <ExcerptBar active={active} key={JSON.stringify(excerpt)} excerpt={excerpt} copyText={copyText} workspace={workspace} owner={owner} columns={columns} palette={palette} onClear={() => {setExcerpt(null); setExtending(false);}} extending={extending}
      onExtend={excerpt.source.kind === 'plan' ? () => setExtending(value => !value) : undefined}/> : null}
    {embedded ? <>
      <Text color={palette.secondary}>{fitText(`专注检查 · ${embedded.status}`, columns)}</Text>
      <ActionButtons buttons={[{action: 'return', label: '返回草稿'}, {action: 'companion', label: '独立侧栏'}]}
        selected={null} columns={columns} height={1} palette={palette} targets={embeddedTargets}/>
    </> : null}
  </Box>;
}
