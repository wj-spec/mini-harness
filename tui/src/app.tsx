import {rmSync} from 'node:fs';
import path from 'node:path';
import React, {useEffect, useMemo, useReducer, useRef, useState} from 'react';
import {CommandMenu} from './command-menu.js';
import {Box, Text, useApp, useInput, usePaste, useWindowSize} from 'ink';
import type {BackendClient} from './backend.js';
import {commandMatches} from './protocol.js';
import {buildSkillMenu} from './skills-menu.js';
import type {ApprovalDecision, BackendCommand, RunResult, SkillsCommandArgs} from './protocol.js';
import {goalDecision, goalLabel, goalPrompt, goalStatusText, resumeGoal, startGoal as newGoal, type GoalMode} from './goal.js';
import {loadGoal, saveGoal} from './goal-store.js';
import {initialState as emptyState, reduceView, resultReason, type ViewState} from './view-state.js';
import {Composer} from './composer.js';
import {fitText, textLines} from './markdown.js';
import {detailLines, errorLines, liveLines, metricsText, ReadingPane, transcriptLines, welcomeLines} from './workbench.js';
import {AgentHeader, agentStatus, StatusPanel} from './agent-status.js';
import {getPalette, type ThemeName} from './theme.js';
import {terminalColorLevel} from './terminal-colors.js';
import {useTranscriptMouse} from './use-transcript-scroll.js';
import {contextLines} from './context-view.js';
import {copyToClipboard} from './clipboard.js';
import {selectedText, type Selection, type SelectionPoint} from './selection.js';
import {loadProfile, projectProfilePath, saveProfile} from './project-profile.js';
import {appendProfileAudit, exportUserProfile, loadUserProfile, looksSensitive, pruneUserProfile, readProfileAudit, saveUserProfile, userProfileFile, type UserPreference} from './user-profile.js';
import {mergeProposals, proposePreferences, readSessionUserMessages} from './preference-observer.js';
import {adjustSidebarSplitForTui, closeSidebarWindowForTui, killSidebarForTui, launchSidebarForTui, runSidebarSetup} from './sidebar/launch.js';
import {agentTouchedFiles} from './sidebar/agent-activity.js';
import {writeSidebarSignal, type SidebarSignalInput} from './sidebar/signal.js';
import type {LaunchResult, SidebarMode, TerminalHost} from './sidebar/host.js';
import type {SetupResult} from './sidebar/launch.js';

type Scroll = {follow: boolean; top: number};
const backendCommands: Record<string, BackendCommand> = {
  '/resume': 'resume', '/continue': 'resume', '/permissions': 'permissions', '/permissions reset': 'permissions_reset',
  '/cost': 'cost', '/costs': 'cost', '/tools': 'tools', '/diff': 'diff', '/mcp': 'mcp',
};
const approvalKeys: Record<string, ApprovalDecision> = {y: 'once', s: 'session', p: 'workspace', n: 'deny'};

export function App({client, initialState = emptyState, windowSize, initialTheme = 'system', copyText = copyToClipboard, profilePath, userProfilePath, launchSidebar = launchSidebarForTui, setupSidebar = runSidebarSetup, publishSidebarSignal = writeSidebarSignal, autoObserve = process.env.VIBE_AUTO_OBSERVE === '1'}: {
  client: BackendClient; initialState?: ViewState; windowSize?: {columns: number; rows: number}; initialTheme?: ThemeName;
  copyText?: (text: string) => string | null; profilePath?: string; userProfilePath?: string;
  launchSidebar?: (workspace: string, mode?: SidebarMode, totalColumns?: number) => LaunchResult;
  setupSidebar?: () => SetupResult;
  publishSidebarSignal?: (workspace: string, input: SidebarSignalInput) => void;
  autoObserve?: boolean;
}) {
  const [state, dispatch] = useReducer(reduceView, initialState);
  const [input, setInput] = useState('');
  const [menuIndex, setMenuIndex] = useState(0);
  const [theme, setTheme] = useState<ThemeName>(initialTheme);
  const [quitting, setQuitting] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [feedbackTone, setFeedbackTone] = useState<'info' | 'error'>('error');
  const [scroll, setScroll] = useState<Scroll>({follow: true, top: 0});
  const [selectedTool, setSelectedTool] = useState<number | null>(null);
  const [detail, setDetail] = useState<number | null>(null);
  const [contextOpen, setContextOpen] = useState(false);
  const [contextSelection, setContextSelection] = useState<string | null>(null);
  const [overlayScroll, setOverlayScroll] = useState({key: '', top: 0});
  const [reading, setReading] = useState(false);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [goal, setGoal] = useState<GoalMode | null>(null);
  const closing = useRef(false);
  const activeRun = useRef(state.activeId);
  const answeredApproval = useRef<string | null>(null);
  const controlRequests = useRef(new Map<string, 'steer' | 'cancel'>());
  const followups = useRef<string[]>([]);
  const sidebarWindow = useRef<{host: TerminalHost; windowId: string} | null>(null);
  const dragAnchor = useRef<SelectionPoint | null>(null);
  const goalRef = useRef<GoalMode | null>(null);
  goalRef.current = goal;
  const goalDoneRef = useRef<((result: RunResult) => boolean) | null>(null);
  const goalLoadedSession = useRef('');
  const goalSaveError = useRef(false);
  const autoObserveRef = useRef<(() => void) | null>(null);
  const aboutResetPending = useRef(false);
  const latestState = useRef(state);
  latestState.current = state;
  const quittingRef = useRef(quitting);
  quittingRef.current = quitting;
  activeRun.current = state.activeId;
  const startQueuedFollowup = () => {
    if (!followups.current.length) return;
    const current = latestState.current;
    if (quittingRef.current || current.phase === 'closed') return;
    const text = followups.current.shift()!;
    try {
      const id = client.run(text);
      activeRun.current = id;
      dispatch({type: 'begin', id, text});
      setFeedback(''); setScroll({follow: true, top: 0});
    } catch (error) {
      followups.current.unshift(text);
      dispatch({type: 'notice', text: `后续消息未能启动：${String(error)}`});
    }
  };
  const history = useMemo(() => state.entries.filter(entry => entry.kind === 'task').map(entry => entry.text), [state.entries]);
  const {exit} = useApp();
  const terminal = useWindowSize();
  const columns = Math.max(1, windowSize?.columns ?? terminal.columns);
  const rows = Math.max(1, windowSize?.rows ?? terminal.rows);
  const palette = getPalette(theme);
  const tools = state.entries.filter(entry => entry.kind === 'tool' && entry.call);
  const chosenTool = tools.find(entry => entry.id === selectedTool) ?? tools.at(-1);
  const detailEntry = state.entries.find(entry => entry.id === detail);
  const overlayKey = state.approval ? `approval-${state.approval.id}` : contextOpen ? 'context' : detailEntry ? `tool-${detailEntry.id}` : '';
  const composerRows = rows >= 32 ? 5 : rows >= 18 ? 3 : 1;
  const headerRows = rows >= 26 && columns >= 96 ? 5 : rows >= 18 ? 3 : 2;
  const sidebar = columns >= 118 && rows >= 24;
  const readerColumns = sidebar ? columns - 33 : columns;
  const compactRows = !sidebar && rows >= 24 && !overlayKey ? 3 : 0;
  const skillMenu = !reading && !overlayKey && !quitting ? buildSkillMenu(input, state.skills) : null;
  const menuMatches = skillMenu?.matches ?? (!reading && !overlayKey && !quitting ? commandMatches(input) : []);
  const paneBase = rows - headerRows - compactRows - composerRows - 4;
  const menuRows = rows >= 18 ? Math.min(menuMatches.length, 5, Math.max(0, paneBase - 4)) : 0;
  const paneRows = Math.max(4, paneBase - menuRows);
  const menuPosition = Math.min(menuIndex, Math.max(0, menuMatches.length - 1));
  const bodyRows = Math.max(1, paneRows - 3);
  const bodyColumns = Math.max(1, readerColumns - 5);
  const decisions = state.approval?.decisions ?? ['once', 'deny'];
  const approvalLabels: Record<string, [string, string]> = {
    once: ['y 允许一次', 'y一次'], session: ['s 精确规则', 's规则'],
    workspace: ['p 工作区', 'p工作区'], deny: ['n 拒绝', 'n拒绝'],
  };
  const fullApprovalHint = decisions.map(decision => approvalLabels[decision]?.[0]).filter(Boolean).join(' / ');
  const approvalHint = fitText(fullApprovalHint, columns) === fullApprovalHint ? fullApprovalHint :
    decisions.map(decision => approvalLabels[decision]?.[1]).filter(Boolean).join(' ');
  const isEmpty = !state.entries.length && !state.live && !state.error;
  const completedLines = useMemo(() => transcriptLines({...state, live: '', error: null}, bodyColumns, chosenTool?.id ?? null), [state.entries, bodyColumns, chosenTool?.id]);
  const transcript = useMemo(() => [
    ...completedLines,
    ...(state.live ? [{spans: [{text: ''}]}, ...liveLines(state.live, bodyColumns)] : []),
    ...(state.error ? errorLines(state.error, bodyColumns) : []),
  ], [completedLines, state.live, state.error, bodyColumns]);
  const lines = useMemo(() => {
    if (state.approval) return [
      ...textLines(`审批 › ${state.approval.tool}`, bodyColumns, 'heading'),
      ...(decisions.includes('session') ? textLines('s 精确规则：本进程内记住同一工作区、工具及目标；命令内容与目录必须相同。', bodyColumns, 'notice') : []),
      ...(decisions.includes('workspace') ? textLines('p 工作区授权：本进程内允许原生文件写入、编辑与命令；MCP 仍单独审批。', bodyColumns, 'notice') : []),
      ...textLines('预览', bodyColumns, 'label'),
      ...textLines(state.approval.preview, bodyColumns),
    ];
    if (contextOpen) return contextLines(state.contextSnapshots, contextSelection, bodyColumns);
    if (detailEntry) return detailLines(detailEntry, bodyColumns);
    return isEmpty ? welcomeLines(state, bodyColumns) : transcript;
  }, [state.approval, contextOpen, state.contextSnapshots, contextSelection, detailEntry, bodyColumns, isEmpty, transcript]);
  const maximum = Math.max(0, lines.length - bodyRows);
  const top = overlayKey ? Math.min(overlayScroll.key === overlayKey ? overlayScroll.top : 0, maximum) :
    isEmpty ? Math.min(scroll.top, maximum) : scroll.follow ? maximum : Math.min(scroll.top, maximum);
  const viewport = useRef({top, maximum, overlayKey});
  viewport.current = {top, maximum, overlayKey};
  const readerFocused = reading || Boolean(overlayKey);
  const selectionBody = {
    top: headerRows + compactRows + 3,
    bottom: headerRows + compactRows + 2 + bodyRows,
    left: 3,
    right: 2 + bodyColumns,
  };
  const toSelectionPoint = (row: number, column: number): SelectionPoint => ({
    line: Math.max(0, Math.min(Math.max(0, lines.length - 1), top + row - selectionBody.top)),
    cell: Math.max(0, Math.min(bodyColumns, column - selectionBody.left)),
  });
  const copySelection = (chosen: Selection) => {
    const text = selectedText(lines, chosen);
    if (!text.trim()) {setSelection(null); return;}
    const method = copyText(text);
    if (!method) {announce(`复制失败：所选文本 ${text.length} 字符，系统剪贴板与 OSC 52 都不可用`); return;}
    announce(`已复制所选文本：${text.length} 字符（${method}）`);
  };

  useEffect(() => client.subscribe(event => {
    if (event.type === 'ack' || event.type === 'error') {
      const kind = event.id === null ? undefined : controlRequests.current.get(event.id);
      if (kind) {
        controlRequests.current.delete(event.id!);
        if (event.type === 'ack') {
          if (kind === 'steer') dispatch({type: 'notice', text: '纠偏已提交，将在下一次模型请求生效'});
          else dispatch({type: 'notice', text: '后端已确认停止请求，等待收尾…'});
        } else if (kind === 'steer') {
          dispatch({type: 'notice', text: `纠偏未送达：${event.code}: ${event.message}`});
          setFeedback(`纠偏未送达：${event.message}`);
        } else {
          dispatch({type: 'notice', text: `停止请求未生效：${event.code}: ${event.message}`});
        }
        return;
      }
    }
    if (event.type === 'approval_request') {setDetail(null); setContextOpen(false); answeredApproval.current = null;}
    if (event.type === 'status' && event.phase === 'starting' && event.id) activeRun.current = event.id;
    const finished = (event.type === 'done' || event.type === 'command_result') && event.id === activeRun.current;
    if (finished) activeRun.current = null;
    if (event.type === 'status' && event.phase === 'idle' && activeRun.current === 'startup') activeRun.current = null;
    if (event.type === 'closed') {activeRun.current = null; followups.current = [];}
    dispatch({type: 'event', event});
    if (finished && event.type === 'done') {
      if (goalDoneRef.current?.(event.result)) return;
      if (event.result.status === 'done' || event.result.status === 'stopped') autoObserveRef.current?.();
      if (event.result.status === 'cancelled') {
        const dropped = followups.current.length;
        followups.current = [];
        dispatch({type: 'notice', text: '已取消：已发生的修改仍需核对'});
        if (dropped) dispatch({type: 'notice', text: `已取消，${dropped} 条排队消息已丢弃`});
      } else {
        startQueuedFollowup();
      }
    }
  }), [client]);
  usePaste(() => {}, {isActive: readerFocused});
  // 只读信号文件：侧栏据此即时刷新，并标记本轮 Agent 写/改过的文件。
  const publishedSignal = useRef('');
  useEffect(() => {
    if (!state.workspace) return;
    const last = state.entries.at(-1);
    const signature = `${state.activeId ?? ''}:${state.entries.length}:${last?.id ?? 0}:${last?.pending ? 1 : 0}:${state.phase}:${state.lastStatus ?? ''}`;
    if (signature === publishedSignal.current) return;
    publishedSignal.current = signature;
    try {
      publishSidebarSignal(state.workspace, {
        signature, files: agentTouchedFiles(state.entries),
        active: state.phase === 'running' || state.phase === 'waiting_approval',
      });
    } catch {}
  }, [state.workspace, state.entries, state.activeId, state.phase, state.lastStatus, publishSidebarSignal]);

  const notice = (text: string) => {dispatch({type: 'notice', text}); setScroll({follow: true, top: 0});};
  const announce = (text: string) => {notice(text); setFeedback(text); setFeedbackTone('info');};
  const reportError = (text: string) => {notice(text); setFeedback(text); setFeedbackTone('error');};
  const launchGoalSegment = (nextGoal: GoalMode, display: string) => {
    try {
      const id = client.run(goalPrompt(nextGoal));
      goalRef.current = nextGoal;
      setGoal(nextGoal);
      activeRun.current = id;
      dispatch({type: 'begin', id, text: display});
      setFeedback(''); setScroll({follow: true, top: 0});
      return true;
    } catch (error) {
      const paused = {...nextGoal, active: false, pausedReason: 'launch_failed'};
      goalRef.current = paused; setGoal(paused);
      dispatch({type: 'notice', text: `目标模式启动失败：${String(error)}`});
      return false;
    }
  };
  const handleGoalDone = (result: RunResult): boolean => {
    const current = goalRef.current;
    if (!current?.active) return false;
    const decision = goalDecision(current, result);
    goalRef.current = decision.goal;
    setGoal(decision.goal);
    dispatch({type: 'notice', text: decision.notice});
    if (!decision.continue || !decision.prompt) return false;
    return launchGoalSegment(decision.goal, `目标续段 · 第 ${decision.goal.segment} 段`);
  };
  goalDoneRef.current = handleGoalDone;
  useEffect(() => {
    if (!state.sessionPath || goalLoadedSession.current === state.sessionPath) return;
    goalLoadedSession.current = state.sessionPath;
    const restored = loadGoal(state.sessionPath);
    if (!restored) return;
    goalRef.current = restored;
    setGoal(restored);
    dispatch({type: 'notice', text: `目标状态已从会话恢复：${goalStatusText(restored)}${restored.active ? '' : ' · /goal resume 继续'}`});
  }, [state.sessionPath]);
  useEffect(() => {
    if (!state.sessionPath || !goal || goalLoadedSession.current !== state.sessionPath) return;
    try {
      saveGoal(state.sessionPath, goal);
    } catch (error) {
      if (!goalSaveError.current) {
        goalSaveError.current = true;
        dispatch({type: 'notice', text: `目标状态保存失败：${String(error)}`});
      }
    }
  }, [state.sessionPath, goal]);
  const handleGoalCommand = (rest: string) => {
    const current = goalRef.current;
    if (!rest) {
      notice(current ? `${goalStatusText(current)}\n目标：${current.objective}` : '用法：/goal <目标>；/goal stop；/goal resume');
      return;
    }
    if (rest === 'stop') {
      if (!current) {notice('当前没有目标模式。'); return;}
      const stopped = {...current, active: false, pausedReason: 'user_stopped'};
      goalRef.current = stopped; setGoal(stopped);
      notice('目标模式已停止；已开始的运行仍需核对。');
      if (activeRun.current && state.phase !== 'idle' && state.phase !== 'closed') {
        try {
          const id = client.cancel(activeRun.current);
          controlRequests.current.set(id, 'cancel');
          dispatch({type: 'cancel'});
        } catch (error) {dispatch({type: 'notice', text: `取消请求未送达：${String(error)}`});}
      }
      return;
    }
    if (rest === 'resume') {
      if (!current) {notice('当前没有目标模式；先输入 /goal <目标>。'); return;}
      if (current.active) {notice(`目标模式已在进行中 · ${goalLabel(current)}`); return;}
      if (state.phase !== 'idle' || activeRun.current) {notice('当前任务运行中，结束后再 /goal resume。'); return;}
      const resumed = resumeGoal(current);
      notice(`恢复目标模式 · ${goalLabel(resumed)}`);
      launchGoalSegment(resumed, `目标恢复 · 第 ${resumed.segment} 段`);
      return;
    }
    if (current?.active) {notice('已有目标模式；先 /goal stop 或 /goal resume。'); return;}
    if (state.phase !== 'idle' || activeRun.current) {notice('当前任务运行中，结束后再启动目标模式。'); return;}
    const next = newGoal(rest);
    notice(`目标模式启动 · ${goalLabel(next)} · 最多 ${next.maxSegments} 段`);
    launchGoalSegment(next, next.objective);
  };
  const usesDefaultLauncher = launchSidebar === launchSidebarForTui;
  const openSidebarPanel = (mode: SidebarMode = 'split') => {
    if (quitting) return;
    const workspace = state.workspace;
    if (!workspace) {notice('尚未连接工作区，暂时无法打开变更侧栏'); return;}
    try {
      const result = launchSidebar(workspace, mode, columns);
      announce(result.message);
      if (result.host && result.windowId) sidebarWindow.current = {host: result.host, windowId: result.windowId};
      if (mode === 'split' && result.outcome === 'started' && result.host === 'ghostty' && usesDefaultLauncher) {
        const totalColumns = columns;
        setTimeout(() => adjustSidebarSplitForTui(workspace, totalColumns), 1200);
      }
    } catch (error) {
      reportError(`打开变更侧栏失败：${String(error)}`);
    }
  };
  const setupSidebarConfig = () => {
    if (quitting) return;
    try {
      const result = setupSidebar();
      if (result.ok) announce(result.message);
      else reportError(result.message);
    } catch (error) {
      reportError(`写入 Ghostty 侧栏配置失败：${String(error)}`);
    }
  };
  const shutdown = () => {
    if (closing.current) return;
    closing.current = true;
    followups.current = [];
    setQuitting(true);
    // 工作台退出时联动收起同一进程启动的侧栏；崩溃场景由侧栏自己的父进程看护兜底。
    if (state.workspace) {
      try {killSidebarForTui(state.workspace);} catch {}
    }
    const windowInfo = sidebarWindow.current;
    if (windowInfo) {
      try {closeSidebarWindowForTui(windowInfo.host, windowInfo.windowId);} catch {}
    }
    void client.shutdown().then(() => exit()).catch(error => {
      notice(`退出时发生错误：${String(error)}`);
      exit(error instanceof Error ? error : new Error(String(error)));
    });
  };
  const navigate = (target: number) => {
    const current = viewport.current;
    const next = Math.max(0, Math.min(target, current.maximum));
    viewport.current = {...current, top: next};
    if (current.overlayKey) setOverlayScroll({key: current.overlayKey, top: next});
    else setScroll({follow: next === current.maximum, top: next});
  };
  const moveReading = (delta: number) => navigate(viewport.current.top + delta);
  useTranscriptMouse({
    onScroll: delta => {setSelection(null); moveReading(delta);},
    top: headerRows + compactRows + 1, bottom: headerRows + compactRows + paneRows, left: 1, right: readerColumns,
    body: selectionBody,
    onDragStart: point => {
      const start = toSelectionPoint(point.row, point.column);
      dragAnchor.current = start;
      setSelection({anchor: start, head: start});
    },
    onDragMove: point => {
      const anchor = dragAnchor.current;
      if (anchor) setSelection({anchor, head: toSelectionPoint(point.row, point.column)});
    },
    onDragEnd: point => {
      const anchor = dragAnchor.current;
      dragAnchor.current = null;
      if (!anchor) return;
      const chosen = {anchor, head: toSelectionPoint(point.row, point.column)};
      setSelection(chosen);
      copySelection(chosen);
    },
    enabled: !quitting && rows >= 11 && columns >= 24,
  });
  const openTool = () => {if (chosenTool) {setDetail(chosenTool.id); setOverlayScroll({key: `tool-${chosenTool.id}`, top: 0});}};
  const openContext = () => {setDetail(null); setContextSelection(null); setContextOpen(true); setOverlayScroll({key: 'context', top: 0});};
  const copyTarget = (): [string, string] | null => {
    if (detailEntry) {
      const args = detailEntry.call ? `参数：\n${JSON.stringify(detailEntry.call.args, null, 2)}` : '';
      const output = detailEntry.output ? `输出：\n${detailEntry.output}` : '';
      return [[detailEntry.text, args, output].filter(Boolean).join('\n\n'), '工具详情'];
    }
    if (state.approval) return [state.approval.preview, '审批预览'];
    const latest = [...state.entries].reverse().find(entry => entry.kind === 'assistant' && entry.text.trim());
    return latest ? [latest.text, '最近回复'] : null;
  };
  const projectFile = () => profilePath ?? (state.workspace ? projectProfilePath(state.workspace) : null);
  const projectCommand = (argument: string) => {
    const file = projectFile();
    if (!file) {announce('尚未连接工作区，无法读取项目背景'); return;}
    const entries = loadProfile(file);
    const rest = argument.trim();
    if (!rest) {
      if (!entries.length) {announce('项目背景为空。用 /project add <文本> 添加，或直接编辑 .vibe/project-profile.json'); return;}
      const lines = entries.map((entry, index) => `${index + 1}. ${entry.text}（来源：${entry.source}）`);
      announce(`项目背景（${entries.length} 条）：\n${lines.join('\n')}`);
      return;
    }
    if (rest.startsWith('add ')) {
      const text = rest.slice(4).trim();
      if (!text) {announce('用法：/project add <文本>'); return;}
      saveProfile(file, [...entries, {text, source: 'user', updated_at: new Date().toISOString()}]);
      announce(`已添加项目背景（来源 user）：${text}`);
      return;
    }
    if (rest.startsWith('remove ')) {
      const index = Number(rest.slice(7).trim());
      if (!Number.isInteger(index) || index < 1 || index > entries.length) {announce(`用法：/project remove <1-${entries.length}>`); return;}
      const [removed] = entries.splice(index - 1, 1);
      saveProfile(file, entries);
      announce(`已删除项目背景：${removed?.text ?? ''}`);
      return;
    }
    if (rest.startsWith('source ')) {
      const parsed = /^(\S+)\s+(\S[\s\S]*)$/.exec(rest.slice('source '.length).trim());
      const index = Number(parsed?.[1]);
      const source = parsed?.[2].trim() ?? '';
      if (!parsed || !Number.isInteger(index) || index < 1 || index > entries.length || !source) {
        announce(`用法：/project source <1-${entries.length}> <来源>`);
        return;
      }
      entries[index - 1] = {...entries[index - 1]!, source, updated_at: new Date().toISOString()};
      saveProfile(file, entries);
      announce(`已更新项目背景来源：${source}`);
      return;
    }
    announce('用法：/project；/project add <文本>；/project remove <序号>；/project source <序号> <来源>');
  };
  const aboutFile = () => userProfilePath ?? userProfileFile();
  const runAutoObserve = () => {
    if (!autoObserve) return;
    const current = latestState.current;
    if (!current.sessionPath || current.sessionPurpose === 'test') return;
    try {
      const file = aboutFile();
      const profile = loadUserProfile(file);
      const proposals = proposePreferences(profile, readSessionUserMessages(current.sessionPath), current.sessionId);
      if (!proposals.length) return;
      const merged = mergeProposals(profile, proposals);
      if (!merged.added && !merged.updated) return;
      saveUserProfile(file, {...profile, entries: merged.entries});
      appendProfileAudit(file, 'auto-observe', merged.entries);
      dispatch({type: 'notice', text: `已自动提出 ${merged.added + merged.updated} 条待确认偏好（不会自动注入）；/about 查看`});
    } catch {
      // 自动观察是旁路能力，失败不能影响任务本身。
    }
  };
  autoObserveRef.current = runAutoObserve;
  const aboutCommand = (argument: string) => {
    const file = aboutFile();
    const maintained = pruneUserProfile(loadUserProfile(file));
    const profile = maintained.profile;
    if (maintained.removed || maintained.stale) {
      saveUserProfile(file, profile);
      appendProfileAudit(file, 'prune', profile.entries);
    }
    const rest = argument.trim();
    const now = new Date().toISOString();
    const newId = () => `pref-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    const statusLabel = (status: string) => status === 'confirmed' ? '已确认' : status === 'proposed' ? '待确认' : '已停用';
    const saveEntries = (entries: UserPreference[], action = 'update') => {
      saveUserProfile(file, {...profile, entries});
      appendProfileAudit(file, action, entries);
    };
    const indexOf = (value: string) => {
      const index = Number(value);
      return Number.isInteger(index) && index >= 1 && index <= profile.entries.length ? index : null;
    };
    if (rest !== 'reset confirm') aboutResetPending.current = false;
    if (rest === 'reset') {
      aboutResetPending.current = true;
      announce('即将删除全部个人偏好和导出文件；再次输入 /about reset confirm 确认');
      return;
    }
    if (rest === 'reset confirm') {
      if (!aboutResetPending.current) {announce('请先输入 /about reset，再确认'); return;}
      aboutResetPending.current = false;
      rmSync(file, {force: true});
      rmSync(path.join(path.dirname(file), 'exports'), {recursive: true, force: true});
      rmSync(path.join(path.dirname(file), 'user-profile-audit.jsonl'), {force: true});
      announce('已删除全部个人偏好与导出文件；新任务不会再注入历史偏好');
      return;
    }
    if (rest === 'audit') {
      const records = readProfileAudit(file);
      if (!records.length) {announce('暂无偏好审计记录'); return;}
      announce(`偏好审计（最近 ${records.length} 条）：\n${records.map(record => `${record.ts.slice(0, 19)} · ${record.action} · ${record.count} 条`).join('\n')}`);
      return;
    }
    if (rest === 'export') {
      const exported = exportUserProfile(profile, file);
      announce(`已导出 ${exported.count} 条偏好到 ${exported.path}；疑似凭据条目未导出`);
      return;
    }
    if (rest === 'observe') {
      if (!state.sessionPath) {announce('当前会话还没有可读取的会话目录；无法观察偏好。'); return;}
      try {
        const proposals = proposePreferences(profile, readSessionUserMessages(state.sessionPath), state.sessionId);
        if (!proposals.length) {announce('未从本会话发现新的明确长期偏好；proposed 不会自动注入。'); return;}
        const merged = mergeProposals(profile, proposals);
        if (!merged.added && !merged.updated) {announce('本会话偏好已存在，未新增 proposed。'); return;}
        saveEntries(merged.entries, 'observe');
        announce(`已提出待确认偏好（不会自动注入）：新增 ${merged.added}、更新 ${merged.updated}、跳过 ${merged.skipped}、疑似冲突 ${merged.conflicts}；输入 /about 查看，/about confirm <序号> 确认`);
      } catch (error) {announce(`观察偏好失败：${String(error)}`);}
      return;
    }
    if (!rest) {
      const stateText = profile.enabled ? '已启用' : '已停用';
      if (!profile.entries.length) {announce(`关于你（${stateText}）：暂无条目。用 /about add <文本> 添加，/about propose <文本> 记录待确认观察`); return;}
      const lines = profile.entries.map((entry, index) => {
        const triggers = entry.triggers?.length ? `；触发：${entry.triggers.join(',')}` : '';
        const expiry = entry.expires_at ? `；过期：${entry.expires_at.slice(0, 10)}` : '';
        const conflict = entry.status === 'proposed' && entry.evidence?.some(value => value.startsWith('conflict:')) ? '；与已确认偏好冲突待确认' : '';
        return `${index + 1}. [${statusLabel(entry.status)}] ${entry.text}（来源：${entry.source}${triggers}${expiry}${conflict}）`;
      });
      announce(`关于你（${stateText}，${profile.entries.length} 条）：\n${lines.join('\n')}`);
      return;
    }
    if (rest === 'on' || rest === 'off') {
      saveUserProfile(file, {...profile, enabled: rest === 'on'});
      appendProfileAudit(file, rest === 'on' ? 'enable-all' : 'disable-all', profile.entries);
      announce(`个人偏好已${rest === 'on' ? '启用' : '停用'}；新任务${rest === 'on' ? '会' : '不会'}注入`);
      return;
    }
    if (rest.startsWith('add ') || rest.startsWith('propose ')) {
      const proposed = rest.startsWith('propose ');
      const text = rest.slice(proposed ? 8 : 4).trim();
      if (!text) {announce(`用法：/about ${proposed ? 'propose' : 'add'} <文本>`); return;}
      if (looksSensitive(text)) {announce('这段内容疑似密钥/凭据，未写入偏好，请先脱敏'); return;}
      const entry: UserPreference = {
        text, source: proposed ? 'observation' : 'user', status: proposed ? 'proposed' : 'confirmed',
        id: newId(), kind: 'preference', scope: 'global', triggers: [],
        confidence: proposed ? 0.5 : 1.0, created_at: now, updated_at: now,
        ...(proposed ? {} : {last_confirmed_at: now}),
      };
      saveEntries([...profile.entries, entry], proposed ? 'propose' : 'add');
      announce(`${proposed ? '已记录待确认观察' : '已添加已确认偏好'}：${text}`);
      return;
    }
    if (rest.startsWith('edit ')) {
      const match = /^edit\s+(\d+)\s+(.+)$/s.exec(rest);
      const index = match ? indexOf(match[1]!) : null;
      if (index === null) {announce(`用法：/about edit <1-${profile.entries.length}> <新文本>`); return;}
      const text = match![2]!.trim();
      if (!text) {announce('新文本不能为空'); return;}
      if (looksSensitive(text)) {announce('新文本疑似密钥/凭据，未写入偏好，请先脱敏'); return;}
      saveEntries(profile.entries.map((entry, position) => position === index - 1 ? {...entry, text, updated_at: now} : entry), 'edit');
      announce(`已修改第 ${index} 条偏好；新任务生效`);
      return;
    }
    if (rest.startsWith('triggers ')) {
      const match = /^triggers\s+(\d+)\s+(.+)$/s.exec(rest);
      const index = match ? indexOf(match[1]!) : null;
      if (index === null) {announce(`用法：/about triggers <1-${profile.entries.length}> <词1,词2|->`); return;}
      const raw = match![2]!.trim();
      const triggers = raw === '-' ? [] : raw.split(/[,，\s]+/u).map(item => item.trim()).filter(Boolean).slice(0, 20);
      saveEntries(profile.entries.map((entry, position) => position === index - 1 ? {...entry, triggers, updated_at: now} : entry), 'triggers');
      announce(triggers.length ? `已设置第 ${index} 条触发词：${triggers.join(', ')}` : `已清除第 ${index} 条触发词（作为通用偏好）`);
      return;
    }
    if (rest.startsWith('disable ') || rest.startsWith('enable ')) {
      const disable = rest.startsWith('disable ');
      const rawIndex = rest.slice(disable ? 8 : 7).trim();
      const index = indexOf(rawIndex);
      if (index === null) {announce(`用法：/about ${disable ? 'disable' : 'enable'} <1-${profile.entries.length}>`); return;}
      saveEntries(profile.entries.map((entry, position) => position === index - 1 ? {
        ...entry, status: disable ? 'stale' : 'confirmed', updated_at: now,
        ...(disable ? {} : {last_confirmed_at: now}),
      } : entry), disable ? 'disable' : 'enable');
      announce(`已${disable ? '停用' : '启用'}第 ${index} 条偏好；新任务生效`);
      return;
    }
    if (rest.startsWith('expire ')) {
      const match = /^expire\s+(\d+)\s+(.+)$/s.exec(rest);
      const index = match ? indexOf(match[1]!) : null;
      if (index === null) {announce(`用法：/about expire <1-${profile.entries.length}> <天数|ISO 时间|->`); return;}
      const raw = match![2]!.trim();
      let expires = '';
      if (raw !== '-') {
        const days = Number(raw);
        const parsed = Number.isFinite(days) && days > 0 ? new Date(Date.now() + days * 86_400_000) : new Date(raw);
        if (Number.isNaN(parsed.getTime())) {announce('过期时间必须是正天数、ISO 时间或 -'); return;}
        expires = parsed.toISOString();
      }
      saveEntries(profile.entries.map((entry, position) => {
        if (position !== index - 1) return entry;
        if (expires) return {...entry, expires_at: expires, updated_at: now};
        const {expires_at: _ignored, ...rest} = entry;
        return {...rest, updated_at: now};
      }), 'expire');
      announce(expires ? `已设置第 ${index} 条过期时间：${expires.slice(0, 10)}` : `已清除第 ${index} 条过期时间`);
      return;
    }
    if (rest.startsWith('clear ')) {
      const target = rest.slice(6).trim();
      if (target !== 'proposed' && target !== 'stale') {announce('用法：/about clear proposed|stale'); return;}
      const before = profile.entries.length;
      const entries = profile.entries.filter(entry => entry.status !== target);
      saveEntries(entries, 'clear');
      announce(`已清理 ${before - entries.length} 条 ${target === 'proposed' ? '待确认' : '已停用'}偏好；已确认条目不受影响`);
      return;
    }
    if (rest.startsWith('resolve ')) {
      const index = indexOf(rest.slice(8).trim());
      if (index === null) {announce(`用法：/about resolve <1-${profile.entries.length}>`); return;}
      const replacement = profile.entries[index - 1];
      const conflictEvidence = replacement?.status === 'proposed'
        ? replacement.evidence?.find(value => value.startsWith('conflict:')) : undefined;
      if (!replacement || !conflictEvidence) {announce('该条不是待处理的冲突 proposal；先 /about observe 或检查条目状态'); return;}
      const key = conflictEvidence.slice('conflict:'.length);
      const oldIndex = profile.entries.findIndex((entry, position) =>
        position !== index - 1 && entry.status !== 'proposed' &&
        (entry.id === key || entry.text.slice(0, 24) === key || entry.text.startsWith(key)));
      if (oldIndex < 0) {announce('找不到冲突对应的旧偏好；请用 /about disable 手动处理'); return;}
      saveEntries(profile.entries.map((entry, position) => {
        if (position === oldIndex) return {...entry, status: 'stale', updated_at: now};
        if (position !== index - 1) return entry;
        return {...entry, status: 'confirmed', last_confirmed_at: now, updated_at: now};
      }), 'resolve');
      announce(`已用第 ${index} 条替换冲突旧偏好；旧条目已停用，新条目已确认`);
      return;
    }
    if (rest.startsWith('confirm ')) {
      const index = indexOf(rest.slice(8).trim());
      if (index === null) {announce(`用法：/about confirm <1-${profile.entries.length}>`); return;}
      const entries = profile.entries.map((entry, position) => position === index - 1 ? {
        ...entry, status: 'confirmed' as const, last_confirmed_at: now, updated_at: now,
      } : entry);
      saveEntries(entries, 'confirm');
      announce(`已确认第 ${index} 条偏好，将在下一次任务生效`);
      return;
    }
    if (rest.startsWith('remove ')) {
      const index = indexOf(rest.slice(7).trim());
      if (index === null) {announce(`用法：/about remove <1-${profile.entries.length}>`); return;}
      const removed = profile.entries[index - 1];
      saveEntries(profile.entries.filter((_, position) => position !== index - 1), 'remove');
      announce(`已删除偏好：${removed?.text ?? ''}`);
      return;
    }
    announce('用法：/about；/about add|propose <文本>；/about observe 观察本会话；/about export 导出；/about edit <序号> <文本>；/about triggers <序号> <词1,词2|->；/about expire <序号> <天数|ISO|->；/about confirm|disable|enable <序号>；/about clear proposed|stale；/about resolve <序号>；/about audit；/about export；/about reset confirm；/about remove <序号>；/about on|off');
  };

  const copyCurrent = () => {
    const target = copyTarget();
    if (!target) {announce('没有可复制的 Agent 回复'); return;}
    const [text, label] = target;
    const method = copyText(text);
    if (!method) {announce(`复制失败：${label} ${text.length} 字符，系统剪贴板与 OSC 52 都不可用`); return;}
    announce(`已复制${label}：${text.length} 字符（${method}）`);
  };

  useEffect(() => {setSelection(null); dragAnchor.current = null;}, [overlayKey]);

  useInput((keyInput, key) => {
    if (key.eventType === 'release') return;
    if (key.ctrl && keyInput === 'q') {shutdown(); return;}
    if (key.ctrl && keyInput === 'y') {copyCurrent(); return;}
    if (key.ctrl && keyInput === 'g') {openSidebarPanel(); return;}
    if (key.ctrl && keyInput === 'c') {
      if (activeRun.current && state.phase !== 'stopping') {
        try {
          const id = client.cancel(activeRun.current);
          controlRequests.current.set(id, 'cancel');
          dispatch({type: 'cancel'});
          dispatch({type: 'notice', text: '停止请求已发送，等待后端确认…'});
        } catch (error) {reportError(`取消请求未送达：${String(error)}`);}
      } else if (!state.activeId) shutdown();
      return;
    }
    if (key.pageUp) {moveReading(-Math.max(1, bodyRows - 1)); return;}
    if (key.pageDown) {moveReading(Math.max(1, bodyRows - 1)); return;}
    if (key.ctrl && (keyInput === 'p' || keyInput === 'n')) {moveReading(keyInput === 'p' ? -1 : 1); return;}
    if ((readerFocused || key.ctrl) && (key.upArrow || key.downArrow)) {moveReading(key.upArrow ? -1 : 1); return;}
    if (key.ctrl && keyInput === 'r' && !overlayKey) {setReading(value => !value); return;}
    const home = key.home;
    const end = key.end;
    if ((readerFocused || key.ctrl) && (home || end)) {navigate(home ? 0 : viewport.current.maximum); return;}
    if (key.escape && selection) {setSelection(null); return;}
    if (key.escape && !state.approval && contextOpen) {setContextOpen(false); return;}
    if (key.escape && !state.approval && detailEntry) {setDetail(null); return;}
    if (key.escape && reading && !state.approval) {setReading(false); return;}
    if (reading && keyInput === ' ' && !state.approval && !detailEntry) {moveReading(Math.max(1, bodyRows - 1)); return;}
    if (state.approval) {
      const decision = approvalKeys[keyInput.toLowerCase()];
      if (!quitting && !key.ctrl && !key.meta && decision && decisions.includes(decision) && answeredApproval.current !== state.approval.id) {
        try {client.approve(state.approval.id, decision); answeredApproval.current = state.approval.id; setFeedback(''); dispatch({type: 'approval_sent'});}
        catch (error) {reportError(`审批响应未送达：${String(error)}`);}
      }
      return;
    }
    if (key.ctrl && keyInput === 'k') {contextOpen ? setContextOpen(false) : openContext(); return;}
    if (contextOpen) {
      if (key.leftArrow || key.rightArrow || keyInput === 'l') {
        const selected = contextSelection ? state.contextSnapshots.findIndex(snapshot => snapshot.snapshot_id === contextSelection) : -1;
        const current = selected < 0 ? state.contextSnapshots.length - 1 : selected;
        const next = Math.max(0, Math.min(state.contextSnapshots.length - 1, current + (key.leftArrow ? -1 : 1)));
        setContextSelection(keyInput === 'l' ? null : state.contextSnapshots[next]?.snapshot_id ?? null);
        setOverlayScroll({key: 'context', top: 0});
      }
      return;
    }
    if (key.ctrl && keyInput === 'o') {detailEntry ? setDetail(null) : openTool(); return;}
    if (key.tab && !input.startsWith('/') && tools.length && !detailEntry) {
      const index = tools.findIndex(entry => entry.id === chosenTool?.id);
      const next = tools[(index + (key.shift ? tools.length - 1 : 1)) % tools.length]!;
      setSelectedTool(next.id);
      const at = transcript.findIndex(line => line.entryId === next.id);
      setScroll({follow: false, top: Math.max(0, at - 1)});
    }
  });

  const submit = (value: string) => {
    const text = value.trim();
    if (!text || quitting) return;
    setSelection(null);
    if (text === '/quit' || text === '/exit') {setInput(''); shutdown(); return;}
    if (text === '/context') {setInput(''); openContext(); return;}
    if (text === '/copy') {setInput(''); copyCurrent(); return;}
    if (text === '/sidebar setup') {setInput(''); setupSidebarConfig(); return;}
    if (text === '/sidebar split') {setInput(''); openSidebarPanel('split'); return;}
    if (text === '/sidebar quick') {setInput(''); openSidebarPanel('quick'); return;}
    if (text === '/sidebar window') {setInput(''); openSidebarPanel('window'); return;}
    if (text === '/sidebar') {setInput(''); openSidebarPanel(); return;}
    if (text === '/project' || text.startsWith('/project ')) {setInput(''); projectCommand(text.slice('/project'.length)); return;}
    if (text === '/about' || text.startsWith('/about ')) {setInput(''); aboutCommand(text.slice('/about'.length)); return;}
    if (text === '/goal' || text.startsWith('/goal ')) {setInput(''); handleGoalCommand(text.slice('/goal'.length).trim()); return;}
    if (text === '/help') {
      setInput('');
      notice('Enter 发送（运行中提交纠偏，下一次模型请求生效）；Ctrl+J 换行；输入焦点下 ↑↓ 编辑或浏览输入历史。滚轮 / 触控板在对话区回看；PgUp/PgDn 翻页，Ctrl+P/N 或 Ctrl+↑/↓ 逐行。Ctrl+R 切换阅读 / 输入焦点，阅读时 ↑↓ 滚动、空格下一页，Esc 回输入。Ctrl+Home/End 到首尾，End 恢复跟随最新；鼠标在对话区拖选，松开即复制所选文本。Tab 选择工具，Ctrl+O 查看全文。Ctrl+K 或 /context 查看上下文分区，←/→ 回放本轮快照，l 跟随最新，Esc 返回。Ctrl+Y 或 /copy 复制最近回复、工具详情或审批预览；Ctrl+G 或 /sidebar 打开右侧 Git 变更侧栏（Ghostty；Terminal 为独立窗口）；/sidebar window 独立窗口；/project 查看项目背景（add/remove/source）；/about 查看个人偏好（add/propose/edit/triggers/confirm/disable/enable/remove/on/off）；Ctrl+C 取消当前操作，空闲时退出；Ctrl+Q 退出。审批时 y 单次、s 精确规则、p 工作区、n 拒绝，以面板选项为准。\n/goal <目标> 目标模式（长任务自动续段，/goal stop 停止，/goal resume 恢复）；/resume 显式续做已保存任务；/permissions 查看授权，/permissions reset 撤销；/cost 会话花费；/tools 可用工具；/diff 工作区差异；/mcp 服务状态；/skills 技能菜单（↑↓ 选择，Tab 补全，Enter 执行；refresh 重扫，activate NAME 激活，unload NAME 卸载）；/followup TEXT 排队后续消息，本轮结束后自动继续；/session 会话路径；/sidebar 在窗口内右侧分屏（跟随窗口尺寸）；/sidebar quick 屏幕右侧 quick terminal（不挤占 TUI）；/sidebar setup 写入 Ghostty 右侧配置；/theme system|light|dark 主题。');
      return;
    }
    if (text.startsWith('/theme')) {
      setInput('');
      const choice = text.split(/\s+/)[1];
      if (choice === 'system' || choice === 'light' || choice === 'dark') {setTheme(choice); notice(`主题已切换：${choice}`);}
      else notice(`当前主题：${theme}；终端颜色：${['无色', '16 色', '256 色', '真彩色'][terminalColorLevel()]}。用法：/theme system|light|dark`);
      return;
    }
    if (text === '/session') {setInput(''); notice(`会话：${state.sessionId || '待连接'}\n${state.sessionPath || '后端尚未提供会话路径。'}`); return;}
    let followupText: string | null = null;
    if (text.split(/\s+/, 1)[0] === '/followup') {
      const rest = text.slice('/followup'.length).trim();
      if (!rest) {setInput(''); notice('用法：/followup <文本>；运行中排队，本轮结束后自动发送，空闲时直接提交。'); return;}
      if (state.phase === 'running' && activeRun.current) {
        if (state.activeCommand && state.activeCommand !== 'resume') {
          notice('命令执行中，结束后再发送');
          return;
        }
        followups.current.push(rest);
        setInput('');
        notice(`已排队为后续消息：${rest}`);
        return;
      }
      followupText = rest;
    }
    const parts = text.split(/\s+/);
    const skillCommand = parts[0] === '/skill' || parts[0] === '/skills';
    let skillArgs: SkillsCommandArgs | undefined;
    if (skillCommand) {
      const action = parts[1] ?? 'list';
      if ((action === 'list' || action === 'refresh') && parts.length <= 2) skillArgs = {action};
      else if ((action === 'activate' || action === 'unload') && parts.length === 3) skillArgs = {action, name: parts[2]!};
      else {setInput(''); notice('用法：/skills；/skills refresh；/skills activate NAME；/skills unload NAME'); return;}
    }
    const command = skillCommand ? 'skills' : backendCommands[text];
    if (command) {
      if (state.phase !== 'idle' || activeRun.current) return;
      try {
        const id = client.command(command, skillArgs);
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command});
        setInput(''); setFeedback(''); setScroll({follow: true, top: 0});
      } catch (error) {reportError(`操作未送达：${String(error)}`);}
      return;
    }
    if (text.startsWith('/') && followupText === null) {setInput(''); notice(`未知命令 ${text.split(/\s/)[0]}；输入 /help 查看可用操作。`); return;}
    if (state.phase === 'running' && activeRun.current) {
      if (state.activeCommand && state.activeCommand !== 'resume') {
        notice('命令执行中，结束后再发送');
        return;
      }
      try {
        const id = client.steer(activeRun.current, text);
        controlRequests.current.set(id, 'steer');
        setInput('');
        notice('纠偏已发送，等待后端确认');
      } catch (error) {reportError(`纠偏未送达：${String(error)}`);}
      return;
    }
    if (state.phase !== 'idle' || activeRun.current) return;
    if (goalRef.current?.active) {notice('目标模式进行中；先 /goal stop 再开始新任务。'); return;}
    const task = followupText ?? text;
    try {
      const id = client.run(task);
      activeRun.current = id;
      dispatch({type: 'begin', id, text: task});
      setInput(''); setFeedback(''); setScroll({follow: true, top: 0});
    } catch (error) {notice(`未能提交任务：${String(error)}`);}
  };

  const status = agentStatus(state);
  const phase = quitting ? '正在退出，等待后端清理' : status.label;
  const position = `${Math.min(top + 1, lines.length)}–${Math.min(top + bodyRows, lines.length)}/${lines.length}`;
  const goalTitle = goal?.active ? `目标模式 · ${goalLabel(goal)} · ` : '';
  const paneTitle = goalTitle + (state.approval ? `等待审批 · ${position}` : contextOpen ? `上下文分区 · ${position}` : detailEntry ? `工具详情 · ${position}` : isEmpty ? '工作台首页 · 输入任务开始' : `对话 · ${reading ? '阅读焦点 · ' : ''}${scroll.follow ? '跟随最新' : `回看 · 下方 ${maximum-top} 行`} · ${position}`);
  const goalHint = goal ? `${goalStatusText(goal)} · ` : '';
  const hint = goalHint + (state.approval ? `${approvalHint} · 滚轮/PgUp/PgDn 翻页 · Ctrl+C 取消` : contextOpen ? '←/→ 选快照 · l 最新 · ↑↓/滚轮 翻阅 · Esc 返回' : detailEntry ? '滚轮/↑↓ 翻阅 · Home/End 首尾 · Esc 返回' :
    reading ? '阅读焦点：↑↓滚动  空格翻页  End最新  Esc输入  Ctrl+Q退出' :
      state.phase === 'running' ? (columns < 60 ? 'Enter 纠偏 · /followup 排队 · Ctrl+C 停止' : 'Enter 提交纠偏  /followup 文本 排队后续  Ctrl+C 停止  Ctrl+Q 退出') :
        state.lastStatus === 'done' ? (columns < 60 ? '任务完成 · /diff 核对 · /resume 续做' : '任务完成  /diff 核对改动  /resume 续做  /copy 复制回复  Ctrl+Q 退出') :
        state.lastStatus === 'cancelled' ? (columns < 60 ? '已取消 · 核对修改 · /resume 续做' : '已取消  核对已发生的修改  /resume 续做  /copy 复制回复  Ctrl+Q 退出') :
        state.lastStatus === 'error' ? (columns < 60 ? '运行失败 · /resume 重试' : '运行失败  查看错误与工具记录  /resume 重试  Ctrl+Q 退出') :
        state.lastStatus === 'stopped' ? (columns < 60 ? '任务已暂停 · /resume 续做' : '任务已暂停  /resume 续做  /context 查看进度  Ctrl+Q 退出') :
        isEmpty ? (columns < 60 ? '输入任务开始 · Ctrl+Q退出 · /help' : '直接描述任务开始  /help 命令  Ctrl+K 上下文  Ctrl+Q 退出') :
        columns < 60 ? 'Ctrl+K上下文 · Ctrl+Q退出 · /help' : '拖选复制  Ctrl+R阅读  Ctrl+K上下文  Ctrl+Y复制回复  Ctrl+Q退出');
  const composerDisabled = quitting || state.phase === 'starting' || state.phase === 'stopping' || state.phase === 'waiting_approval' || state.phase === 'closed';
  const composerPlaceholder = state.approval ? '审批面板已聚焦，请选择授权范围' : contextOpen ? '上下文详情已聚焦，Esc 返回输入' : detailEntry ? '工具详情已聚焦，Esc 返回输入' : reading ? '正在阅读历史，Esc 返回输入' :
    state.phase === 'closed' ? '可以回看已有记录，退出后重新启动' : state.phase === 'starting' ? '可以先写下任务目标与约束' : state.phase === 'stopping' ? '正在停止 · 收到停止结果后可发送' :
      state.phase === 'running' ? '运行中：Enter 提交纠偏，/followup 文本排队后续' :
        state.lastStatus === 'done' ? '任务完成 · 输入新任务，或 /resume 续做' :
        state.lastStatus === 'cancelled' ? '已取消 · 核对修改后输入新任务，或 /resume 续做' :
        state.lastStatus === 'error' ? '运行失败 · 输入新任务，或 /resume 重试' :
        state.lastStatus === 'stopped' ? '任务已暂停 · 输入新任务，或 /resume 续做' : '描述任务，或输入 / 查看命令';
  const effectivePlaceholder = goal?.active ? `目标模式 · ${goalLabel(goal)} · 输入纠偏，/goal stop` : composerPlaceholder;
  const unavailableHint = state.phase === 'closed' ? '连接已断开 · Ctrl+Q 退出后重启' : state.phase === 'starting' ? '可编辑草稿 · 后端就绪后可发送' :
    state.phase === 'stopping' ? '正在停止 · 收到停止结果后可发送' : undefined;
  // PM/UX: 完成/错误/暂停/取消后给出下一步引导，减少用户困惑。

  if (rows < 11 || columns < 24) return <Box width={columns} height={rows} backgroundColor={palette.background} flexDirection="column" overflow="hidden">
    <Text color={palette.foreground} bold>{fitText('mini-harness', columns)}</Text>
    {rows > 1 ? <Text color={palette.foreground}>{fitText('请将终端调整到至少 24×11', columns)}</Text> : null}
    {rows > 2 ? <Text color={palette.secondary}>{fitText(state.approval ? `${approvalHint} Ctrl+C取消` : 'Ctrl+Q 退出', columns)}</Text> : null}
  </Box>;

  return <Box flexDirection="column" width={columns} height={rows} backgroundColor={palette.background} overflow="hidden">
    <AgentHeader state={state} columns={columns} height={headerRows} palette={palette}/>
    {compactRows ? <StatusPanel state={state} columns={columns} height={compactRows} palette={palette}/> : null}
    <Box flexDirection="row" width={columns} height={paneRows} flexShrink={0}>
      <ReadingPane title={paneTitle} lines={lines} top={top} height={paneRows} columns={readerColumns} palette={palette} overlay={readerFocused} selection={selection}/>
      {sidebar ? <><Box width={1}/><StatusPanel state={state} columns={32} height={paneRows} palette={palette}/></> : null}
    </Box>
    <Box backgroundColor={state.approval ? palette.surfaces.warning : readerFocused ? palette.surface : palette.focus} height={composerRows + 2} width={columns} paddingX={2} paddingY={1} flexShrink={0}>
      <Composer value={input} onChange={text => {setMenuIndex(0); setInput(text);}} onSubmit={submit} history={history} disabled={composerDisabled} disabledHint={unavailableHint} focused={!readerFocused && !quitting} placeholder={effectivePlaceholder} columns={Math.max(1, columns - 4)} maxRows={composerRows} accentColor={palette.accent} warningColor={palette.warning} foregroundColor={palette.foreground} secondaryColor={palette.secondary}
        menu={menuRows ? {
          count: menuMatches.length,
          index: menuPosition,
          onMove: delta => setMenuIndex(position => (position + delta + menuMatches.length) % menuMatches.length),
          onAccept: () => {const next = menuMatches[menuPosition]; setMenuIndex(0); setInput(next);},
        } : undefined}/>
    </Box>
    {menuRows ? <CommandMenu matches={menuMatches} index={menuPosition} rows={menuRows} columns={columns} accentColor={palette.accent} foregroundColor={palette.foreground} secondaryColor={palette.secondary} surfaceColor={palette.surface} descriptions={skillMenu?.descriptions}/> : null}
    <Text color={state.error || state.phase === 'closed' || (feedback && feedbackTone === 'error') ? palette.error : state.approval ? palette.warning : palette.foreground}>{fitText(feedback || `${phase}${resultReason(state.lastResult) ? ` · ${resultReason(state.lastResult)}` : ''}${metricsText(state) ? `  ·  ${metricsText(state)}` : ''}`, columns)}</Text>
    <Text color={palette.secondary}>{fitText(hint, columns)}</Text>
  </Box>;
}
