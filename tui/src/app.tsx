import {readSidebarLink} from './sidebar/lifecycle.js';
import {useSidebarDraftRequests} from './sidebar/use-draft-requests.js';
import {reapStaleSidebars} from './sidebar/lifecycle.js';
import {formatExcerptLabel, type DraftRequest} from './sidebar/draft-channel.js';
import {spawn, spawnSync} from 'node:child_process';
import {createHash, randomUUID} from 'node:crypto';
import {readFileSync, readdirSync, rmSync, statSync} from 'node:fs';
import path from 'node:path';
import React, {useEffect, useMemo, useReducer, useRef, useState} from 'react';
import {CommandMenu} from './command-menu.js';
import {Box, Text, useApp, useInput, usePaste, useWindowSize} from 'ink';
import type {BackendClient} from './backend.js';
import {backendCommand, backendLaunchOptions} from './backend-launch.js';
import {parseAttachCommand} from './attach-command.js';
import {AUTONOMY_LABELS, AUTONOMY_LEVELS, commandMatches} from './protocol.js';
import {commandDefinition, commandHelp, commandUnavailable} from './commands.js';
import {approvalTitle, splitApprovalPreview} from './approval-view.js';
import {ApprovalActions, approvalButtons, type ApprovalAction, type ApprovalTarget} from './approval-actions.js';
import {ActionButtons, type ActionTarget} from './action-buttons.js';
import {FollowupPanel} from './followup-panel.js';
import {useFollowups} from './use-followups.js';
import {useTaskNavigation} from './use-task-navigation.js';
import {useTaskConnection, type TaskDraft} from './use-task-connection.js';
import type {TaskConnectionTarget} from './backend.js';
import {TaskNavigationPanel} from './task-navigation-panel.js';
import {useSessionNavigation} from './use-session-navigation.js';
import {SessionNavigationPanel} from './session-navigation-panel.js';
import {usePermissionPanel} from './use-permission-panel.js';
import {PermissionPanel} from './permission-panel.js';
import {LocatorPanel} from './locator-panel.js';
import {HistorySearchPanel} from './history-search-panel.js';
import {WorkbenchLogPanel} from './workbench-log-panel.js';
import {errorItems, firstLineByEntry, roundItems, searchTranscript, type LocateItem} from './history-locate.js';
import {validHistorySearchPage, validRoundsLedgerPage, type HistorySearchMatch, type RoundsLedgerPage} from './protocol.js';
import type {SessionCatalogRow} from './protocol.js';
import {usePlanReview} from './use-plan-review.js';
import {SidebarApp} from './sidebar/app.js';
import {useInspectionHistory} from './use-inspection-history.js';
import {usePlanRevision, type RevisionAction} from './use-plan-revision.js';
import {buildSkillMenu} from './skills-menu.js';
import type {ApprovalDecision, AutonomyLevel, BackendCommand, RunResult, SkillsCommandArgs} from './protocol.js';
import {goalDecision, goalLabel, goalPrompt, goalStatusText, resumeGoal, startGoal as newGoal, type GoalMode} from './goal.js';
import {loadGoal, saveGoal} from './goal-store.js';
import {initialState as emptyState, reduceView, resultReason, type ViewState} from './view-state.js';
import {Composer} from './composer.js';
import {fitText, textLines, type ReadingLine} from './markdown.js';
import {detailLines, errorLines, liveLines, metricsText, planStepLine, ReadingPane, transcriptLines, welcomeLines} from './workbench.js';
import {buildPlanProgressView, PlanProgressPanel, planListLines} from './plan-progress-panel.js';
import {PlanReviewBar} from './plan-review-bar.js';
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
import {adjustSidebarSplitForTui, closeSidebarWindowForTui, focusExistingSidebarForTui, killSidebarForTui, launchSidebarForTui, runSidebarSetup} from './sidebar/launch.js';
import {agentTouchedFiles} from './sidebar/agent-activity.js';
import {removeSidebarSignal, writeSidebarSignal, type SidebarSignalInput} from './sidebar/signal.js';
import {createSidebarOwner, type SidebarOwner} from './sidebar/channel.js';
import {verificationInspection, type InspectionSnapshot} from './inspection.js';
import {useSidebarPlanRequests} from './sidebar/use-plan-requests.js';
import type {LaunchResult, SidebarMode, TerminalHost} from './sidebar/host.js';
import type {SetupResult} from './sidebar/launch.js';

type Scroll = {follow: boolean; top: number};
const backendCommands: Record<string, BackendCommand> = {
  '/resume': 'resume', '/continue': 'resume', '/permissions': 'permissions', '/permissions reset': 'permissions_reset',
  '/cost': 'cost', '/costs': 'cost', '/tools': 'tools', '/diff': 'diff', '/mcp': 'mcp', '/evidence': 'evidence', '/trace': 'trace', '/queue': 'queue',
  '/task': 'tasks', '/tasks': 'tasks', '/approvals': 'approvals',
};
const approvalKeys: Record<string, ApprovalDecision> = {y: 'once', s: 'session', p: 'workspace', n: 'deny'};
const FAKE_DEMO_PROMPT = '创建 .vibe/fake-demo.txt 并验证';
const fakeDemoPrompt = () => {
  const mode = (process.env.VIBE_FAKE_DEMO ?? '').trim().toLowerCase();
  if (mode === 'plan' || mode === 'revision') return '/plan 写入 .vibe/plan-demo.txt 并校验内容';
  return /^(1|true|yes)$/.test(mode) ? FAKE_DEMO_PROMPT : '';
};

function latestPlanArtifact(sessionPath: string): Record<string, unknown> | null {
  if (!sessionPath) return null;
  try {
    const tasksRoot = path.join(sessionPath, 'tasks');
    let best: {mtime: number; payload: Record<string, unknown>; file: string} | null = null;
    for (const task of readdirSync(tasksRoot, {withFileTypes: true})) {
      if (!task.isDirectory()) continue;
      const planDir = path.join(tasksRoot, task.name, 'plan');
      let files: string[] = [];
      try {files = readdirSync(planDir).filter(name => /^plan\.v\d+\.json$/.test(name));} catch {continue;}
      for (const name of files) {
        const file = path.join(planDir, name);
        try {
          const stats = statSync(file);
          const payload = JSON.parse(readFileSync(file, 'utf8')) as Record<string, unknown>;
          if (!best || stats.mtimeMs > best.mtime) best = {mtime: stats.mtimeMs, payload, file};
        } catch {}
      }
    }
    return best?.payload ?? null;
  } catch {return null;}
}

function latestExecutablePlanArtifact(sessionPath: string): Record<string, unknown> | null {
  if (!sessionPath) return null;
  try {
    const tasksRoot = path.join(sessionPath, 'tasks');
    let best: {mtime: number; payload: Record<string, unknown>} | null = null;
    for (const task of readdirSync(tasksRoot, {withFileTypes: true})) {
      if (!task.isDirectory()) continue;
      const planDir = path.join(tasksRoot, task.name, 'plan');
      let files: string[] = [];
      try {files = readdirSync(planDir).filter(name => /^plan\.v\d+\.json$/.test(name));} catch {continue;}
      for (const name of files) {
        const file = path.join(planDir, name);
        try {
          const stats = statSync(file);
          const payload = JSON.parse(readFileSync(file, 'utf8')) as Record<string, unknown>;
          const approval = payload.approval && typeof payload.approval === 'object' ? payload.approval as Record<string, unknown> : {};
          if (payload.status === 'approved' && approval.stale !== true && (!best || stats.mtimeMs > best.mtime)) {
            best = {mtime: stats.mtimeMs, payload};
          }
        } catch {}
      }
    }
    return best?.payload ?? null;
  } catch {return null;}
}

function latestPlanText(sessionPath: string, full = false): string | null {
  if (!sessionPath) return null;
  try {
    const tasksRoot = path.join(sessionPath, 'tasks');
    let best: {mtime: number; payload: Record<string, unknown>; file: string} | null = null;
    for (const task of readdirSync(tasksRoot, {withFileTypes: true})) {
      if (!task.isDirectory()) continue;
      const planDir = path.join(tasksRoot, task.name, 'plan');
      let files: string[] = [];
      try {files = readdirSync(planDir).filter(name => /^plan\.v\d+\.json$/.test(name));} catch {continue;}
      for (const name of files) {
        const file = path.join(planDir, name);
        try {
          const stats = statSync(file);
          const payload = JSON.parse(readFileSync(file, 'utf8')) as Record<string, unknown>;
          if (!best || stats.mtimeMs > best.mtime) best = {mtime: stats.mtimeMs, payload, file};
        } catch {}
      }
    }
    if (!best) return null;
    const payload = best.payload;
    const lines = [`计划：${String(payload.objective ?? '')}`, `状态：${String(payload.status ?? '')} · version ${String(payload.version ?? 1)}`];
    const approval = payload.approval && typeof payload.approval === 'object' ? payload.approval as Record<string, unknown> : {};
    if (approval.approved_by) {
      const hash = String(approval.plan_hash ?? '').slice(0, 12);
      lines.push(`批准：${String(approval.approved_by)} · ${String(approval.approved_at ?? '')}${hash ? ` · hash ${hash}` : ''}${approval.stale === true ? ' · stale' : ''}`);
    } else if (approval.stale === true) {
      lines.push('批准：已 stale');
    }
    const reviewTtl = Number(process.env.VIBE_PLAN_REVIEW_TIMEOUT_SECONDS ?? 0);
    if (reviewTtl > 0 && payload.status === 'proposed') {
      const remaining = reviewTtl - (Date.now() - best.mtime) / 1000;
      lines.push(remaining > 0
        ? `审批剩余：${Math.ceil(remaining)}s`
        : '审批窗口已过期；请修订后重新提出');
    }
    const criteria = Array.isArray(payload.acceptance_criteria) ? payload.acceptance_criteria : [];
    if (criteria.length) {
      lines.push('验收标准：');
      for (const item of criteria) lines.push(`- [${item?.confirmed === true ? '已确认' : '候选'}] ${String(item?.text ?? '')}`);
    }
    const verification = Array.isArray(payload.verification_plan) ? payload.verification_plan : [];
    try {
      const saved = JSON.parse(readFileSync(path.join(path.dirname(best.file), '..', `acceptance.v${String(payload.version ?? 1)}.json`), 'utf8')) as Record<string, unknown>;
      if (saved.status === true || saved.status === false) {
        lines.push(`验收结果：${saved.status === true ? '✓ 通过' : '✗ 失败'}${Array.isArray(saved.codes) && saved.codes.length ? ` · ${saved.codes.join(',')}` : ''}`);
      }
    } catch {}
    if (verification.length) {
      lines.push(`验收命令（${verification.length}）：`);
      for (const item of verification.slice(0, 5)) {
        lines.push(`- ${String(item?.id ?? '?')} [${String(item?.source ?? '?')}] ${String(item?.command ?? '')}`);
      }
    }
    const steps = Array.isArray(payload.steps) ? payload.steps : [];
    const execution: Record<string, string> = {};
    try {
      const exec = JSON.parse(readFileSync(path.join(path.dirname(best.file), `execution.v${String(payload.version ?? 1)}.json`), 'utf8'));
      for (const record of Array.isArray(exec.steps) ? exec.steps : []) {
        if (record && typeof record.step_id === 'string') execution[record.step_id] = String(record.status ?? '');
      }
    } catch {}
    lines.push(`步骤（${steps.length}）：`);
    const visible = full ? steps : steps.slice(0, 3);
    for (const step of visible) {
      const stepId = String(step?.step_id ?? '?');
      lines.push(`- ${stepId} ${String(step?.title ?? '')} [${String(step?.kind ?? '')}]${execution[stepId] ? ` · ${execution[stepId]}` : ''}`);
    }
    if (!full && steps.length > visible.length) lines.push(`… 共 ${steps.length} 步；/plan all 查看全部`);
    return lines.slice(0, full ? 200 : 40).join('\n');
  } catch {return null;}
}

export function App({client, attach = false, active = true, initialDraft, onDraftChange, onDetach, stopOnConnect = false, onSessionSwitch, initialFocus, initialState = emptyState, windowSize, initialTheme = 'system', copyText = copyToClipboard, profilePath, userProfilePath, launchSidebar = launchSidebarForTui, focusSidebar = focusExistingSidebarForTui, setupSidebar = runSidebarSetup, publishSidebarSignal = writeSidebarSignal, autoObserve = process.env.VIBE_AUTO_OBSERVE === '1'}: {
  client: BackendClient; attach?: boolean; initialState?: ViewState; windowSize?: {columns: number; rows: number}; initialTheme?: ThemeName;
  copyText?: (text: string) => string | null; profilePath?: string; userProfilePath?: string;
  launchSidebar?: (workspace: string, mode?: SidebarMode, totalColumns?: number, owner?: SidebarOwner) => LaunchResult;
  focusSidebar?: (workspace: string, owner?: SidebarOwner) => boolean;
  setupSidebar?: () => SetupResult;
  publishSidebarSignal?: (workspace: string, input: SidebarSignalInput) => void;
  autoObserve?: boolean;
  active?: boolean; initialDraft?: TaskDraft; onDraftChange?: (draft: TaskDraft) => void; onDetach?: () => void; stopOnConnect?: boolean;
  onSessionSwitch?: (client: BackendClient, options?: {focusQuery?: string}) => void | Promise<void>;
  initialFocus?: string;
}) {
  const [state, dispatch] = useReducer(reduceView, initialState);
  const taskConnection = useTaskConnection(client);
  const surfaceActive = active && !taskConnection.connection;
  const surfaceActiveRef = useRef(surfaceActive); surfaceActiveRef.current = surfaceActive;
  const stopOnConnectRef = useRef(stopOnConnect);
  const [handoff, setHandoff] = useState<{target: TaskConnectionTarget; stop: boolean; mode: 'choose' | 'wait'} | null>(null);
  const handoffRef = useRef(handoff); handoffRef.current = handoff;
  const connectRef = useRef(taskConnection.connect); connectRef.current = taskConnection.connect;
  const [sidebarOwner] = useState(createSidebarOwner);
  const history = useInspectionHistory(client, state.workspace, state.sessionId, state.phase === 'closed', attach ? state.attachedTaskId : undefined);
  const [historyIndex, setHistoryIndex] = useState<number | null>(null);
  useEffect(() => setHistoryIndex(null), [history.open, history.cursor]);
  const historyRef = useRef(history); historyRef.current = history;
  const historyAvailable = (!attach || Boolean(state.attachedTaskId)) && Boolean(state.sessionId) && Boolean(client.capabilities?.().includes('inspection-history'));
  const navigation = useTaskNavigation(client, state.workspace, state.sessionId, state.phase === 'closed');
  const navigationRef = useRef(navigation); navigationRef.current = navigation;
  const navigationAvailable = Boolean(state.sessionId && client.capabilities?.().includes('task-navigation'));
  const sessionNav = useSessionNavigation(client, state.workspace, state.sessionId, state.phase === 'closed');
  const sessionNavRef = useRef(sessionNav); sessionNavRef.current = sessionNav;
  const sessionNavAvailable = Boolean(state.sessionId && client.capabilities?.().includes('session-navigation'));
  const permissionPanel = usePermissionPanel(client, state.workspace, state.sessionId, state.phase === 'closed');
  const permissionPanelRef = useRef(permissionPanel); permissionPanelRef.current = permissionPanel;
  const permissionPanelAvailable = Boolean(client.capabilities?.().includes('permission-panel'));
  const historySearchAvailable = Boolean(client.capabilities?.().includes('history-search'));
  const roundsLedgerAvailable = Boolean(client.capabilities?.().includes('rounds-ledger'));
  const [locator, setLocator] = useState<{title: string; items: LocateItem[]; kind: 'find' | 'rounds' | 'errors' | 'ledger'} | null>(null);
  const [highlightLine, setHighlightLine] = useState<number | null>(null);
  const [logsOpen, setLogsOpen] = useState(false);
  const [searchPanel, setSearchPanel] = useState<{query: string; matches: HistorySearchMatch[]; next: string | null; loading: boolean; problem: string; truncated: boolean; skipped: number} | null>(null);
  const searchRequest = useRef<{id: string; append: boolean; timer: ReturnType<typeof setTimeout>} | null>(null);
  const roundsRequest = useRef<{id: string; timer: ReturnType<typeof setTimeout>} | null>(null);
  const ledgerItemsRef = useRef<(page: RoundsLedgerPage) => LocateItem[]>(() => []);
  const localRoundsRef = useRef<() => void>(() => {});
  const initialFocusDone = useRef(false);
  useEffect(() => () => {
    if (searchRequest.current) clearTimeout(searchRequest.current.timer);
    if (roundsRequest.current) clearTimeout(roundsRequest.current.timer);
  }, []);
  const sessionSwitch = useRef<AbortController | null>(null);
  useEffect(() => () => {sessionSwitch.current?.abort();}, []);
  const sidebarRequests = useSidebarPlanRequests(client, state, sidebarOwner);
  const sidebarRequestsRef = useRef(sidebarRequests); sidebarRequestsRef.current = sidebarRequests;
  const [inspectionSelection, setInspectionSelection] = useState<{id: string; page: 'changes' | 'plan' | 'verification'; snapshot: InspectionSnapshot | null} | null>(null);
  const [inspectionFocused, setInspectionFocused] = useState(false);
  const [inspectionMounted, setInspectionMounted] = useState(false);
  const showFocusedInspection = () => {setInspectionMounted(true); setInspectionFocused(true);};
  const inspectionTargets = useRef<ActionTarget<'current' | 'history' | 'older' | 'newer' | 'retry' | 'back' | 'focus'>[]>([]);
  const inspectionPress = useRef<{key: string; entryId: number | null; page: 'changes' | 'plan' | 'verification' | 'current' | 'history' | 'older' | 'newer' | 'retry' | 'back' | 'focus'} | null>(null);
  const [input, setInput] = useState(() => initialDraft?.value ?? fakeDemoPrompt());
  const [menuIndex, setMenuIndex] = useState(0);
  const [theme, setTheme] = useState<ThemeName>(initialTheme);
  const [quitting, setQuitting] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [feedbackTone, setFeedbackTone] = useState<'info' | 'error'>('error');
  const [scroll, setScroll] = useState<Scroll>({follow: true, top: 0});
  const [selectedTool, setSelectedTool] = useState<number | null>(null);
  const [detail, setDetail] = useState<number | null>(null);
  const [contextOpen, setContextOpen] = useState(false);
  const [planOpen, setPlanOpen] = useState(false);
  const [planLines, setPlanLines] = useState<string[]>([]);
  const [planVersion, setPlanVersion] = useState(0);
  const [planSelection, setPlanSelection] = useState(0);
  const [planStatus, setPlanStatus] = useState('');
  const [planStale, setPlanStale] = useState(false);
  const [contextSelection, setContextSelection] = useState<string | null>(null);
  const [overlayScroll, setOverlayScroll] = useState({key: '', top: 0});
  const [reading, setReading] = useState(false);
  const [planBarIndex, setPlanBarIndex] = useState(0);
  const [planBarHidden, setPlanBarHidden] = useState(false);
  const [planListOpen, setPlanListOpen] = useState(false);
  const [approvalSelection, setApprovalSelection] = useState<{id: string; decision: ApprovalAction | null} | null>(null);
  const [approvalMore, setApprovalMore] = useState<string | null>(null);
  const approvalTargets = useRef<ApprovalTarget[]>([]);
  const approvalPress = useRef<{layout: string; action: ApprovalAction} | null>(null);
  const revisionTargets = useRef<ActionTarget<RevisionAction>[]>([]);
  const revisionPress = useRef<{layout: string; action: RevisionAction} | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [goal, setGoal] = useState<GoalMode | null>(null);
  const closing = useRef(false);
  const activeRun = useRef(state.activeId);
  const answeredApprovals = useRef(new Set<string>());
  const approvalRun = useRef(state.activeId);
  const controlRequests = useRef(new Map<string, 'steer' | 'cancel' | 'approval'>());
  const modeRequest = useRef<{id: string; draft: string; timer: ReturnType<typeof setTimeout>} | null>(null);
  const modeRequestIds = useRef(new Set<string>());
  useEffect(() => () => {if (modeRequest.current) clearTimeout(modeRequest.current.timer);}, []);
  const followups = useFollowups({workspace: state.workspace, sessionId: state.sessionId, sessionPath: state.sessionPath,
    ...(attach ? {taskId: state.attachedTaskId ?? 'pending-attach'} : {})});
  const followupsRef = useRef(followups); followupsRef.current = followups;
  const failedFollowupVerification = useRef<string | null>(null);
  const followupTargets = useRef<ActionTarget<'followups' | 'tasks' | 'parent'>[]>([]);
  const followupPress = useRef<string | null>(null);
  const followupDraftDirty = useRef(false);
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
  const revision = usePlanRevision(client, text => {setFeedback(text); setFeedbackTone('info');}, (id, target, objective, receipt) => {
    if (activeRun.current || latestState.current.phase !== 'idle') return false;
    activeRun.current = id;
    dispatch({type: 'begin', id, text: `修订计划 v${target.plan_version}：${objective}`, revision: receipt});
    return true;
  }, (source, target, activate) => reviewRef.current.showRevision(source, target, activate), () => !activeRun.current && latestState.current.phase === 'idle', result => dispatch({type: 'revision_receipt', result}));
  const revisionRef = useRef(revision); revisionRef.current = revision;
  const review = usePlanReview(client, (result, problem) => dispatch({type: 'review_card', result, problem}), text => {setFeedback(text); setFeedbackTone('info');}, (id, result) => {
    if (activeRun.current || latestState.current.phase !== 'idle') return false;
    activeRun.current = id;
    dispatch({type: 'begin', id, text: `执行计划 v${result.target?.plan_version}：${result.summary?.objective ?? ''}`, plan: result});
    return true;
  }, () => !activeRun.current && latestState.current.phase === 'idle', (target, objective, history) => {
    if (history) revisionRef.current.openHistory(target, objective); else revisionRef.current.open(target, objective);
  });
  const reviewRef = useRef(review);
  reviewRef.current = review;
  // 附回只把一行占位符放进草稿；发送时再展开成完整引用交给模型，显示保留占位。
  const excerptAttachments = useRef(new Map<string, string>());
  const expandExcerpts = (raw: string): string => {
    let expanded = raw;
    for (const [token, full] of excerptAttachments.current) {
      if (!expanded.includes(token)) continue;
      expanded = expanded.split(token).join(full);
    }
    while (excerptAttachments.current.size > 100) {
      const oldest = excerptAttachments.current.keys().next().value;
      if (oldest === undefined) break;
      excerptAttachments.current.delete(oldest);
    }
    return expanded;
  };
  const startQueuedFollowup = () => {
    const current = latestState.current;
    if (!surfaceActiveRef.current || quittingRef.current || current.phase === 'closed' || current.attachOffline || activeRun.current) return;
    followupsRef.current.start((text, requestId) => {
      const id = client.run(expandExcerpts(text), undefined, requestId);
      activeRun.current = id;
      dispatch({type: 'begin', id, text});
      setFeedback(''); setScroll({follow: true, top: 0});
      return id;
    });
  };
  const inputHistory = useMemo(() => state.entries.filter(entry => entry.kind === 'task').map(entry => entry.text), [state.entries]);
  const {exit} = useApp();
  type QuitOption = {label: string; action: () => void; disabled?: boolean};
  const [quitPrompt, setQuitPrompt] = useState<{options: QuitOption[]; index: number} | null>(null);
  const waitForExit = useRef(false);
  const backgroundAfterRun = useRef(false);
  const pendingBackgroundPlan = useRef(false);
  const backgroundSpawnRef = useRef<((taskId: string) => boolean) | null>(null);
  const shutdownRef = useRef<() => void>(() => {});
  const lastTaskId = useRef<string | null>(null);
  const mergeApprovalId = useRef<string | null>(null);
  const terminal = useWindowSize();
  const columns = Math.max(1, windowSize?.columns ?? terminal.columns);
  const rows = Math.max(1, windowSize?.rows ?? terminal.rows);
  const minimumColumns = state.approval || review.focused || revision.mode || followups.open || navigation.open || sessionNav.open || permissionPanel.open || locator || searchPanel || logsOpen || planListOpen ? 32 : 24;
  const minimumRows = state.approval || review.focused || revision.mode || followups.open || navigation.open || sessionNav.open || permissionPanel.open || locator || searchPanel || logsOpen || planListOpen ? 12 : 11;
  const inspectionVisible = surfaceActive && inspectionFocused && !quitPrompt;
  const followupsVisible = surfaceActive && followups.open && !quitPrompt;
  const navigationVisible = surfaceActive && navigation.open && !quitPrompt;
  const sessionNavVisible = surfaceActive && sessionNav.open && !quitPrompt;
  const permissionPanelVisible = surfaceActive && permissionPanel.open && !quitPrompt;
  const locatorVisible = surfaceActive && Boolean(locator) && !quitPrompt;
  const searchPanelVisible = surfaceActive && Boolean(searchPanel) && !quitPrompt;
  const logsVisible = surfaceActive && logsOpen && !quitPrompt;
  const secondaryPanelVisible = locatorVisible || searchPanelVisible || logsVisible;
  const terminalTooSmall = rows < minimumRows || columns < minimumColumns;
  const palette = getPalette(theme);
  const tools = state.entries.filter(entry => entry.kind === 'tool' && entry.call);
  const chosenTool = tools.find(entry => entry.id === selectedTool) ?? tools.at(-1);
  const detailEntry = state.entries.find(entry => entry.id === detail);
  const overlayKey = state.approval ? `approval-${state.approval.id}` : revision.mode ? `revision-${revision.key}-${revision.mode}${revision.mode === 'history' ? `-${revision.history?.cursor ?? ''}-${revision.history?.index}` : ''}` : review.focused ? `review-${review.key}-${review.body ? review.cursor : 'actions'}` : contextOpen ? 'context' : planOpen ? 'plan' : detailEntry ? `tool-${detailEntry.id}` : planListOpen ? 'plan-list' : history.open ? 'inspection-history' : '';
  const composerRows = rows >= 32 ? 5 : rows >= 18 ? 3 : 1;
  const compactApproval = Boolean((state.approval || revision.mode) && rows < 18);
  const compactLayout = rows >= 18 && rows < 26;
  const compactQuit = Boolean(quitPrompt && rows < 18);
  const composerPadding = rows < 18 || compactApproval || compactLayout ? 0 : 1;
  const headerRows = rows >= 26 && columns >= 96 ? 5 : rows >= 18 ? 3 : compactApproval ? 1 : 2;
  const sidebar = columns >= 118 && rows >= 24;
  const readerColumns = sidebar ? columns - 33 : columns;
  const compactRows = !sidebar && rows >= 24 && !overlayKey ? compactLayout ? state.contextSnapshots.length ? 1 : 0 : 3 : 0;
  const skillMenu = !reading && !overlayKey && !quitting ? buildSkillMenu(input, state.skills) : null;
  const menuMatches = skillMenu?.matches ?? (!reading && !overlayKey && !quitting ? commandMatches(input) : []);
  const submissionBlock = review.decisionPending ? '计划决策处理中' : revision.decisionPending ? '修订请求处理中' : quitting ? '正在退出' :
    state.phase === 'closed' ? '连接已断开' : state.phase === 'starting' ? '后端尚未就绪' : state.phase === 'stopping' ? '正在停止' :
      state.phase === 'waiting_approval' ? '等待审批' : undefined;
  const commandContext = {busy: state.phase !== 'idle' || Boolean(activeRun.current), activeCommand: state.activeCommand,
    tasks: client.capabilities?.().includes('tasks') ?? false, blocked: submissionBlock, taskNavigation: navigationAvailable, sessionNavigation: sessionNavAvailable, permissionPanel: permissionPanelAvailable,
    planModeControl: client.capabilities?.().includes('plan-mode-control') ?? false, modePending: Boolean(modeRequest.current)};
  const menuUnavailable = Object.fromEntries(menuMatches.map(command => [command, commandUnavailable(command, commandContext)]));
  const approvalRows = state.approval ? rows >= 18 ? 3 : 1 : 0;
  const revisionRows = !state.approval && revision.mode ? rows >= 18 ? 3 : 1 : 0;
  const historyVisible = overlayKey === 'inspection-history';
  const inspectionButtons: {action: 'current' | 'history' | 'older' | 'newer' | 'retry' | 'back' | 'focus'; label: string}[] = historyVisible
    ? [...(history.loading ? [] : history.problem ? [{action: 'retry' as const, label: '重试'}] : [
      ...(history.previous.length ? [{action: 'newer' as const, label: '较新'}] : []),
      ...(history.next ? [{action: 'older' as const, label: '更早'}] : [])]), {action: 'back', label: '返回'}]
    : [...(state.workspace ? [{action: 'focus' as const, label: columns < 48 ? '检查' : '专注检查'}] : []),
      ...(historyAvailable ? [{action: 'history' as const, label: columns < 48 ? '历史' : '历史结果'}] : []),
      ...(inspectionSelection?.snapshot ? [{action: 'current' as const, label: columns < 48 ? '当前' : '回到当前'}] : [])];
  const inspectionInline = !overlayKey && inspectionButtons.length === 1 && inspectionButtons[0]?.action === 'focus';
  const inspectionRows = !inspectionInline && (!overlayKey || historyVisible) && inspectionButtons.length ? 1 : 0;
  const followupRows = (onDetach || followups.count || followups.problem || navigationAvailable) && (!overlayKey || (navigationAvailable || onDetach) && state.approval) ? 1 : 0;
  const planProgress = state.planProgress;
  const planBarSteps = review.card?.steps ?? [];
  const planBarSelected = Math.max(0, Math.min(planBarIndex, Math.max(0, planBarSteps.length - 1)));
  const planBarShown = Boolean(review.card && !review.focused && !planBarHidden && !state.approval && !revision.mode && !contextOpen &&
    !planOpen && !planListOpen && !detailEntry && !historyVisible && !reading && !quitting && !compactQuit && !review.problem &&
    state.phase === 'idle' && !activeRun.current &&
    (review.card.outcome === 'ready' || review.card.outcome === 'approved_not_started') && planBarSteps.length > 0 &&
    (review.card.outcome === 'approved_not_started' || review.card.available_actions.includes('approve-execute')));
  const planBarRows = planBarShown ? (rows < 18 ? 3 : Math.min(6, planBarSteps.length) + 3) : 0;
  const planProgressVisible = Boolean(planProgress && planProgress.steps.length) && !compactQuit && !planBarShown && !planListOpen && !state.approval && !review.focused &&
    !revision.mode && !contextOpen && !planOpen && !detailEntry && !historyVisible && !reading && !quitting && rows >= 18;
  const planProgressView = planProgressVisible && planProgress ? buildPlanProgressView(planProgress, columns, rows) : null;
  const planProgressRows = planProgressView?.rows ?? 0;
  const followupButtons: {action: 'tasks' | 'followups' | 'parent'; label: string}[] = [
    ...(onDetach ? [{action: 'parent' as const, label: '返回原会话'}] : []),
    ...(navigationAvailable ? [{action: 'tasks' as const, label: '任务'}] : []),
    ...(followups.count || followups.problem ? [{action: 'followups' as const, label: `后续 ${followups.count}`}] : [])];
  const followupButtonsWidth = (onDetach ? 16 : 0) + (navigationAvailable ? 10 : 0) + (followups.count || followups.problem ? 13 : 0);
  const composerHeight = planBarShown ? planBarRows : composerRows + composerPadding * 2;
  const paneBase = rows - followupRows - planProgressRows - inspectionRows - headerRows - compactRows - composerHeight - 2 - approvalRows - revisionRows;
  const menuRows = rows >= 18 ? Math.min(menuMatches.length, 5, Math.max(0, paneBase - 4)) : 0;
  const paneRows = Math.max(4, paneBase - menuRows);
  const menuPosition = Math.min(menuIndex, Math.max(0, menuMatches.length - 1));
  const bodyRows = Math.max(1, paneRows - 3);
  const bodyColumns = Math.max(1, readerColumns - 5);
  const decisions: ApprovalDecision[] = state.approval?.decisions ?? ['once', 'deny'];
  const approvalExpanded = Boolean(state.approval && approvalMore === state.approval.id);
  const approvalChoices = approvalButtons(decisions, state.approval?.tool ?? '', approvalExpanded, columns);
  const approvalLayout = `${state.approval?.id}:${columns}:${rows}:${approvalExpanded}`;
  const isEmpty = !state.entries.length && !state.live && !state.error;
  const completedLines = useMemo(() => transcriptLines({...state, live: '', error: null}, bodyColumns, chosenTool?.id ?? null), [state.entries, bodyColumns, chosenTool?.id]);
  const transcript = useMemo(() => [
    ...completedLines,
    ...(state.live ? [{spans: [{text: ''}]}, ...liveLines(state.live, bodyColumns)] : []),
    ...(state.error ? errorLines(state.error, bodyColumns) : []),
  ], [completedLines, state.live, state.error, bodyColumns]);
  const approvalView = state.approval ? splitApprovalPreview(state.approval.preview) : null;
  const lines = useMemo<ReadingLine[]>(() => {
    if (state.approval && approvalView) return [
      ...textLines(approvalTitle(state.approval.tool, state.approval.tierLabel ?? approvalView.tier), bodyColumns, 'heading'),
      ...(approvalExpanded && compactApproval ? [
        ...textLines('仅本进程有效，MCP 单独审批', bodyColumns, 'notice'),
        ...(decisions.includes('session') ? textLines('精确规则：记住此工具与目标', bodyColumns, 'notice') : []),
        ...(decisions.includes('workspace') ? textLines('工作区：允许原生写入和命令', bodyColumns, 'notice') : []),
      ] : []),
      ...(state.approval.tierLabel || approvalView.tier ? textLines(`层级：${state.approval.tierLabel ?? approvalView.tier}`, bodyColumns, 'notice') : []),
      ...(state.approval.rule ? textLines(`命中规则：[${state.approval.rule.source}] ${state.approval.rule.tool}${state.approval.rule.decision ? ` → ${state.approval.rule.decision}` : ''}${state.approval.rule.note ? ` · ${state.approval.rule.note}` : ''}`, bodyColumns, 'notice') : []),
      ...(approvalExpanded && decisions.includes('session') ? textLines('精确规则：本进程内记住同一工作区、工具及目标；命令内容与目录必须相同。', bodyColumns, 'notice') : []),
      ...(approvalExpanded && decisions.includes('workspace') ? textLines('工作区授权：本进程内允许原生文件写入、编辑与命令；MCP 仍单独审批。', bodyColumns, 'notice') : []),
      ...textLines('预览', bodyColumns, 'label'),
      ...textLines(approvalView.body, bodyColumns),
    ];
    if (revision.mode === 'history' && revision.history) {
      const history = revision.history; const entry = history.records[history.index];
      const label = {absent: '无记录', pending: '准备中', running: '修订中', paused: '已暂停', incomplete: '未完成', submitted: '已提交', unavailable: '不可续做'};
      return [
        ...textLines(fitText(history.objective.replace(/\s+/g, ' '), bodyColumns), bodyColumns, 'notice'),
        ...textLines(history.pending ? '正在读取修订记录…' : history.problem ?? (entry ? `原计划 v${entry.target?.plan_version} · ${label[entry.outcome]}` : '没有已发送的修订反馈'), bodyColumns, 'heading'),
        ...(!history.pending && !history.problem && entry ? textLines(entry.feedback, bodyColumns) : []),
      ];
    }
    if (revision.mode && revision.record) return [
      ...textLines(fitText(revision.record.objective.replace(/\s+/g, ' '), bodyColumns), bodyColumns, 'notice'),
      ...textLines(revision.mode === 'confirm' ? '确认后只读修订，新版仍需审阅' : revision.record.pending ? '修订请求处理中…' : revision.record.problem ?? revision.record.receipt?.message ?? '反馈已保留', bodyColumns, 'heading'),
      ...(revision.record.receipt ? textLines(`修订状态：${revision.record.receipt.outcome}${revision.record.receipt.task ? ` · ${revision.record.receipt.task.status} · 段 ${revision.record.receipt.task.segment}` : ''}`, bodyColumns) : []),
      ...textLines(revision.mode === 'confirm' ? revision.record.draft : revision.record.submitted ?? revision.record.draft, bodyColumns),
      ...textLines(`\n原计划：${revision.record.target.plan_id} v${revision.record.target.plan_version}`, bodyColumns, 'notice'),
    ];
    if (review.focused && review.body && review.card) return [
      ...(review.card.outcome === 'unavailable' ? textLines(`旧版缓存，仅供核对：${review.card.message}`, bodyColumns, 'notice') : []),
      ...textLines(review.bodyText, bodyColumns),
      ...textLines(`\n计划身份：\n${JSON.stringify(review.card.target, null, 2)}`, bodyColumns, 'notice'),
    ];
    if (review.focused && review.card) return [
      ...textLines(review.pending ? '等待回执…' : review.choice ? `› ${review.choices.find(choice => choice.value === review.choice)?.label}` : '↑↓ 选择动作（未选）', bodyColumns, 'heading'),
      ...review.choices.map(choice => ({spans: [{text: fitText(`${choice.key} ${choice.label}`, bodyColumns)}]})),
      ...(review.card.steps?.length ? [
        ...textLines(`步骤 ${review.card.steps.length}`, bodyColumns, 'label'),
        ...review.card.steps.slice(0, 6).map(step => ({spans: [{text: fitText(planStepLine(step), bodyColumns)}]})),
        ...(review.card.steps.length > 6 ? textLines(`…另有 ${review.card.steps.length - 6} 步，Ctrl+R 正文查看`, bodyColumns, 'notice') : []),
      ] : []),
      ...textLines(fitText((review.card.summary?.objective ?? review.card.message).replace(/\s+/g, ' '), bodyColumns), bodyColumns),
      ...textLines(review.card.message, bodyColumns),
      ...(review.card.launch ? textLines(`绑定任务：${review.card.launch.task_id} · ${review.card.launch.status}`, bodyColumns) : []),
      ...(review.card.outcome === 'launched' ? textLines(`已有运行记录；/task 核对，/attach ${review.card.launch.task_id} 查看连接方式`, bodyColumns, 'notice') : []),
      ...(review.problem ? textLines(review.problem, bodyColumns, 'notice') : []),
    ];
    if (historyVisible) return [
      ...(history.taskId ? textLines(`筛选任务：${history.taskId}`, bodyColumns, 'notice') : []),
      ...textLines(history.loading ? '正在读取历史结果…' : history.problem || (history.records.length ? '选择原运行的检查对象；读取不会执行任务。' : '没有保存的检查记录；旧会话不会从历史文字补造结果。'), bodyColumns, 'notice'),
      ...(history.skipped ? textLines(`跳过 ${history.skipped} 条损坏或身份不明的记录`, bodyColumns, 'notice') : []),
      ...transcriptLines({...state, entries: history.records.map((snapshot, index) => ({id: -index - 1, kind: 'notice' as const, inspection: snapshot,
        text: `${historyIndex === index ? '› ' : `${index + 1}. `}${snapshot.scope.objective}\n${snapshot.scope.status === 'unknown' ? '未取得结束结果' : snapshot.verification.verified === true ? '已验证' : snapshot.verification.verified === false ? '验证未通过' : '未验证'} · ${snapshot.scope.requestId}${snapshot.scope.segment ? ' · 段 ' + snapshot.scope.segment : ''}`}))}, bodyColumns, null),
    ];
    if (planListOpen) return state.planProgress ? planListLines(state.planProgress, bodyColumns) : [];
    if (contextOpen) return contextLines(state.contextSnapshots, contextSelection, bodyColumns);
    if (planOpen) return planLines.flatMap((line, index) =>
      textLines(`${index === planSelection ? '› ' : '  '}${line}`, bodyColumns));
    if (detailEntry) return detailLines(detailEntry, bodyColumns);
    return isEmpty ? welcomeLines(state, bodyColumns) : transcript;
  }, [history, historyIndex, historyVisible, state.approval, approvalView, approvalExpanded, compactApproval, revision, review, contextOpen, planListOpen, state.planProgress, planOpen, planLines, planSelection, state.contextSnapshots, contextSelection, detailEntry, bodyColumns, isEmpty, transcript]);
  const maximum = Math.max(0, lines.length - bodyRows);
  const top = overlayKey ? Math.min(overlayScroll.key === overlayKey ? overlayScroll.top : 0, maximum) :
    isEmpty ? Math.min(scroll.top, maximum) : scroll.follow ? maximum : Math.min(scroll.top, maximum);
  useEffect(() => {
    if (!initialFocus || initialFocusDone.current) return;
    const found = searchTranscript(lines, initialFocus);
    if (!found.length) return;
    initialFocusDone.current = true;
    setHighlightLine(found[0]!.line);
    setScroll({follow: false, top: Math.max(0, found[0]!.line - 2)});
    dispatch({type: 'notice', text: `已在旧会话中定位“${initialFocus}”`});
  }, [initialFocus, lines.length]);
  const paneLines = useMemo(() => highlightLine === null ? lines : lines.map((line, index) =>
    index === highlightLine ? {...line, highlight: true} : line), [lines, highlightLine]);
  const viewport = useRef({top, maximum, overlayKey});
  viewport.current = {top, maximum, overlayKey};
  const approvalFocused = Boolean(state.approval && approvalSelection?.id === state.approval.id);
  const readerFocused = state.approval ? approvalFocused : reading || Boolean(overlayKey);
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
    let stopTarget: string | null | undefined;
    const pendingSearch = searchRequest.current;
    if (event.type === 'history_search' && pendingSearch && event.id === pendingSearch.id) {
      clearTimeout(pendingSearch.timer); searchRequest.current = null;
      if (event.workspace !== latestState.current.workspace || event.session_id !== latestState.current.sessionId || !validHistorySearchPage(event)) {
        setSearchPanel(prev => prev ? {...prev, loading: false, problem: '检索回执不匹配，保留已读结果'} : prev);
        return;
      }
      setSearchPanel(prev => prev ? {...prev, query: event.query,
        matches: pendingSearch.append ? [...prev.matches, ...event.matches] : event.matches,
        next: event.next_cursor, loading: false, problem: '', truncated: event.truncated, skipped: event.skipped} : prev);
      return;
    }
    if (event.type === 'error' && pendingSearch && event.id === pendingSearch.id) {
      clearTimeout(pendingSearch.timer); searchRequest.current = null;
      setSearchPanel(prev => prev ? {...prev, loading: false, problem: event.message} : prev);
      return;
    }
    const pendingRounds = roundsRequest.current;
    if (event.type === 'rounds_ledger' && pendingRounds && event.id === pendingRounds.id) {
      clearTimeout(pendingRounds.timer); roundsRequest.current = null;
      if (event.workspace !== latestState.current.workspace || event.session_id !== latestState.current.sessionId || !validRoundsLedgerPage(event)) {
        dispatch({type: 'notice', text: '轮次账本回执不匹配；保留正文轮次。'});
        return;
      }
      setLocator({title: `轮次账本 · ${event.total} 轮`, items: ledgerItemsRef.current(event), kind: 'ledger'});
      return;
    }
    if (event.type === 'error' && pendingRounds && event.id === pendingRounds.id) {
      clearTimeout(pendingRounds.timer); roundsRequest.current = null;
      dispatch({type: 'notice', text: `轮次账本未读取：${event.message}；改用正文轮次。`});
      localRoundsRef.current();
      return;
    }
    if (navigationRef.current.receive(event) || event.type === 'task_catalog') return;
    if (sessionNavRef.current.receive(event) || event.type === 'session_catalog') return;
    if (permissionPanelRef.current.receive(event) || event.type === 'permission_overview') return;
    if (historyRef.current.receive(event) || event.type === 'inspection_history') return;
    if (sidebarRequestsRef.current.receive(event)) return;
    if (event.type === 'error' && ['default_mode_unavailable', 'session_busy', 'session_unavailable'].includes(event.code) && event.id === activeRun.current) {
      if (!followupsRef.current.rejected(event.id!)) {
        const task = latestState.current.entries.findLast(entry => entry.kind === 'task');
        if (task) setInput(draft => draft || task.text);
        followupsRef.current.pause('任务未启动，待办保留');
      }
      activeRun.current = null;
    }
    if (revisionRef.current.receive(event) || event.type === 'plan_revision' || event.type === 'plan_revision_history') return;
    if (reviewRef.current.receive(event) || event.type === 'plan_review') return;
    const mode = modeRequest.current;
    if (event.id && modeRequestIds.current.has(event.id) && (event.type === 'command_result' || event.type === 'error')) {
      modeRequestIds.current.delete(event.id);
      if (mode?.id !== event.id) return;
    }
    if (event.type === 'closed') modeRequestIds.current.clear();
    if (mode && (event.type === 'closed' || event.id === mode.id && (event.type === 'command_result' || event.type === 'error'))) {
      clearTimeout(mode.timer); modeRequest.current = null;
      if (event.type === 'command_result') {
        setInput(draft => draft === mode.draft ? '' : draft);
        dispatch({type: 'notice', text: event.text});
        setFeedback('默认模式已确认；当前任务模式不变'); setFeedbackTone('info');
        return;
      }
      setFeedback(event.type === 'error' ? `默认模式未确认：${event.message}；草稿保留，可 /plan status 查询` : '连接已断开，默认模式结果待确认；重开后核对');
      setFeedbackTone('error');
      if (event.type === 'error') return;
    }
    if (event.type === 'ack' || event.type === 'error') {
      const kind = event.id === null ? undefined : controlRequests.current.get(event.id);
      if (kind) {
        controlRequests.current.delete(event.id!);
        if (event.type === 'ack') {
          if (kind === 'steer') dispatch({type: 'notice', text: '纠偏已提交，将在下一次模型请求生效'});
          else if (kind === 'approval') setFeedback('');
          else dispatch({type: 'notice', text: '后端已确认停止请求，等待收尾…'});
        } else if (kind === 'approval') {
          reportError(`审批未生效：${event.code}: ${event.message}；Ctrl+C 停止后核对`);
        } else if (kind === 'steer') {
          dispatch({type: 'notice', text: `纠偏未送达：${event.code}: ${event.message}`});
          setFeedback(`纠偏未送达：${event.message}`);
        } else {
          dispatch({type: 'notice', text: `停止请求未生效：${event.code}: ${event.message}`});
        }
        return;
      }
    }
    if (event.type === 'approval_request') {
      if (event.id !== activeRun.current) return;
      if (approvalRun.current !== event.id) {
        approvalRun.current = event.id;
        answeredApprovals.current.clear();
      }
      if (answeredApprovals.current.has(event.approval_id) ||
          latestState.current.phase === 'stopping' || latestState.current.phase === 'closed') return;
      if (latestState.current.approval?.id !== event.approval_id) {
        reviewRef.current.blur();
        revisionRef.current.close();
        setDetail(null); setContextOpen(false); setPlanOpen(false); setReading(false);
        setApprovalSelection(null);
        setApprovalMore(null); approvalPress.current = null;
        setFeedback('');
      }
    }
    if (event.type === 'approval_resolved' && event.id === activeRun.current && latestState.current.approval?.id === event.approval_id) {
      answeredApprovals.current.add(event.approval_id);
      setApprovalSelection(null);
      setFeedback('审批已被处理，草稿保留');
    }
    if (event.type === 'ready' && event.tasks && event.tasks.length) {
      const running = event.tasks.filter(task => task.worker_status && task.worker_status !== 'exited' && task.worker_status !== 'failed' && task.worker_status !== 'interrupted');
      const summary = event.tasks.slice(0, 5).map(task => `${task.task_id} ${task.status}${task.worker_status ? `/${task.worker_status}` : ''}${task.pending_approvals ? ` 待审批${task.pending_approvals}` : ''}`).join('；');
      dispatch({type: 'notice', text: `检测到 ${event.tasks.length} 个任务${running.length ? `（${running.length} 个后台 worker 活跃）` : ''}：${summary}${event.tasks.length > 5 ? ' …' : ''}\n输入 /task 查看；/attach 生成 attach 命令。`});
    }
    if (event.type === 'status' && event.phase === 'idle' && startupBlocked.current) {
      startupBlocked.current = false;
      setFeedback('后端已就绪 · 草稿已保留，再按 Enter 发送');
      dispatch({type: 'notice', text: '后端已就绪；草稿仍保留，按 Enter 发送'});
    }
    if (!attach && event.type === 'ready' && event.resumed && client.capabilities?.().includes('plan-review')) reviewRef.current.discover(true);
    if (event.type === 'subagent_event') {
      notice(`子 Agent ${event.status}：${event.task_id}${event.summary ? ` · ${event.summary}` : ''}`);
    }
    if ((event.type === 'error' || event.type === 'closed') && pendingBackgroundPlan.current) {
      pendingBackgroundPlan.current = false;
      dispatch({type: 'notice', text: '后台计划准备失败；查看错误后重试。'});
    }
    if (event.type === 'command_result' && pendingBackgroundPlan.current) {
      pendingBackgroundPlan.current = false;
      const match = /task_id=(t-[A-Za-z0-9-]+)/.exec(String(event.text ?? ''));
      if (match && backgroundSpawnRef.current) {
        try {
          const spawned = backgroundSpawnRef.current(match[1]!);
          if (spawned) {
            shutdownRef.current();
          } else {
            dispatch({type: 'notice', text: `后台 worker 未启动；task ${match[1]} 已创建，可稍后用 /task 或 --worker 启动。`});
          }
        } catch (error) {
          dispatch({type: 'notice', text: `后台计划启动失败：${String(error)}`});
        }
      } else {
        dispatch({type: 'notice', text: '后台计划未准备成功；查看命令结果。'});
      }
    }
    if (event.type === 'task_snapshot') {
      lastTaskId.current = event.task.task_id;
      activeRun.current = !client.capabilities?.().includes('attach-offline') && event.task.status === 'running' ? event.task.run_id : null;
      if (stopOnConnectRef.current) {
        stopOnConnectRef.current = false;
        stopTarget = client.capabilities?.().includes('cancel') ? activeRun.current : null;
      }
      const worker = event.task.worktree ? ` · worktree ${event.task.worktree.branch}` : '';
      const plan = event.task.plan_id ? ` · plan ${event.task.plan_id}v${event.task.plan_version ?? 1}` : '';
      dispatch({type: 'notice', text: `已 attach：${event.task.task_id} ${event.task.status} · 段 ${event.task.segment}${worker}${plan} · 待审批 ${event.approvals.length}`});
    }
    if (event.type === 'task_state') {
      lastTaskId.current = event.task.task_id;
    }
    if (event.type === 'ready' && event.tasks?.length && !lastTaskId.current) {
      lastTaskId.current = event.tasks[0]?.task_id ?? null;
    }
    if (event.type === 'task_state' && event.id === activeRun.current) {
      const current = goalRef.current;
      if (current && current.taskId !== event.task.task_id) {
        const next = {...current, taskId: event.task.task_id};
        goalRef.current = next;
        setGoal(next);
        dispatch({type: 'notice', text: `任务已记录：${event.task.task_id}`});
      }
    }
    if (event.type === 'status' && event.phase === 'starting' && event.id) activeRun.current = event.id;
    if (event.type === 'tool_result' && event.id === activeRun.current && event.call.args.verification === true &&
        (!event.ok || event.execution?.status !== 'succeeded' || event.execution.exit_code !== 0)) failedFollowupVerification.current = event.id;
    const finished = (event.type === 'done' || event.type === 'command_result') && event.id === activeRun.current;
    if (finished) activeRun.current = null;
    if (event.type === 'status' && event.phase === 'idle' && activeRun.current === 'startup') activeRun.current = null;
    if (event.type === 'closed') {
      handoffRef.current = null; setHandoff(null);
      if ([...controlRequests.current.values()].includes('approval')) setFeedback('连接已断开，审批结果未知；核对后再操作');
      activeRun.current = null; followupsRef.current.disconnected(); controlRequests.current.clear();
    }
    if (event.type === 'error' && event.id === activeRun.current) followupsRef.current.disconnected('运行发生错误，结果待核对；待办保留');
    dispatch({type: 'event', event});
    if (stopTarget !== undefined) {
      if (!stopTarget) dispatch({type: 'notice', text: '任务已不再可停止；未发送停止请求，请核对当前状态'});
      else try {
        controlRequests.current.set(client.cancel(stopTarget), 'cancel');
        dispatch({type: 'cancel'});
        dispatch({type: 'notice', text: '已向所选任务发送停止请求，等待后端收尾；原会话未停止'});
      } catch (error) {dispatch({type: 'notice', text: `停止请求未送达：${String(error)}；未自动重试`});}
    }
    if (finished && event.type === 'done') {
      const verificationFailed = failedFollowupVerification.current === event.id;
      failedFollowupVerification.current = null;
      const canContinue = followupsRef.current.settle(event.result, verificationFailed, event.id ?? undefined);
      const revisedPlan = revisionRef.current.takeCompletedRun(event.id!);
      if (!revisedPlan && event.result.stop_reason === 'plan_submitted' && client.capabilities?.().includes('plan-review')) reviewRef.current.discover();
      if (handoffRef.current?.mode === 'wait') {
        const requested = handoffRef.current; handoffRef.current = null; setHandoff(null);
        void connectRef.current(requested.target, requested.stop); return;
      }
      if (goalDoneRef.current?.(event.result)) {followupsRef.current.pause('目标模式优先，待办保留'); return;}
      if (waitForExit.current) {
        waitForExit.current = false;
        shutdownRef.current();
        return;
      }
      if (event.result.status === 'done' || event.result.status === 'stopped') autoObserveRef.current?.();
      if (event.result.status === 'cancelled') dispatch({type: 'notice', text: '已取消：已发生的修改仍需核对，后续待办保留'});
      if (canContinue) startQueuedFollowup();
    }
  }), [client]);
  usePaste(() => {}, {isActive: surfaceActive && (readerFocused || inspectionVisible || followupsVisible || navigationVisible || sessionNavVisible || permissionPanelVisible || secondaryPanelVisible)});
  // 只读信号文件：侧栏据此即时刷新，并标记本轮 Agent 写/改过的文件。
  const [draftAppendRevision, setDraftAppendRevision] = useState(0);
  useSidebarDraftRequests(state, sidebarOwner, (text, request: DraftRequest) => {
    const token = formatExcerptLabel(request);
    excerptAttachments.current.set(token, text);
    setInput(previous => (previous ? previous.replace(/\n+$/, '') + '\n' : '') + token);
    setDraftAppendRevision(value => value + 1); setFeedback('已附入侧栏引用 · 尚未发送');
  }, quitting, (target, cursor) => sidebarRequestsRef.current.readPage(target, cursor));
  const publishedSignal = useRef('');
  const startupBlocked = useRef(false);
  useEffect(() => {
    if (!state.workspace) return;
    try {
      const reaped = reapStaleSidebars(state.workspace);
      if (reaped.length) dispatch({type: 'notice', text: `已清理上次遗留的侧栏 ${reaped.length} 个`});
    } catch {}
  }, [state.workspace]);
  useEffect(() => {setPlanBarIndex(0); setPlanBarHidden(false);}, [review.key, Boolean(review.card)]);
  useEffect(() => {if (!state.planProgress) setPlanListOpen(false);}, [state.planProgress]);
  useEffect(() => {setInspectionSelection(null); setInspectionFocused(false); setInspectionMounted(false);}, [state.workspace, state.sessionId]);
  useEffect(() => () => {
    if (state.workspace) removeSidebarSignal(state.workspace, sidebarOwner);
  }, [state.workspace, sidebarOwner]);
  useEffect(() => {
    if (!state.workspace) return;
    const last = state.entries.at(-1);
    const retained = inspectionSelection?.snapshot;
    const selected = retained?.workspace === state.workspace && retained.sessionId === state.sessionId
      ? state.entries.find(entry => entry.inspection?.sessionId === retained.sessionId &&
          entry.inspection.scope.requestId === retained.scope.requestId)?.inspection ?? retained
      : null;
    const plan = selected ? selected.plan : state.inspectionPlan;
    const fileSource = selected ? selected.fileSource ?? null : state.fileSource;
    const run = selected ? selected.scope : plan?.relation === 'review' ? undefined : state.inspection?.scope;
    const verification = selected ? selected.verification : run && state.inspection ? verificationInspection(state.inspection, state.entries) : undefined;
    const liveProgress = state.planProgress;
    const progressMatches = Boolean(liveProgress && plan && liveProgress.plan.plan_id === plan.target?.plan_id &&
      liveProgress.plan.version === plan.target?.plan_version);
    const summary = plan ? {target: plan.target, relation: plan.relation, problem: plan.problem,
      version: plan.target.plan_version, status: plan.status, stale: Boolean(plan.problem), objective: plan.objective,
      stepCount: plan.stepCount, doneSteps: progressMatches ? liveProgress!.done : null,
      acceptance: plan.relation === 'execution' ? verification?.verified ?? null : null} : undefined;
    const navigation = inspectionSelection ? {id: inspectionSelection.id, page: inspectionSelection.page, mode: selected ? 'result' as const : 'current' as const} : undefined;
    const signature = createHash('sha256').update(JSON.stringify([state.workspace, state.sessionId, fileSource, run, summary, verification, navigation,
      state.activeId, state.entries.length, last?.id, last?.pending, state.phase, state.lastStatus])).digest('hex');
    if (signature === publishedSignal.current) return;
    publishedSignal.current = signature;
    try {
      publishSidebarSignal(state.workspace, {
        owner: sidebarOwner, sessionId: state.sessionId, fileSource,
        signature, navigation, files: selected?.files ?? agentTouchedFiles(state.entries, run?.requestId ?? null), run, plan: summary, verification,
        active: run?.status === 'running',
      });
    } catch {}
  }, [state.workspace, state.sessionId, state.fileSource, state.inspection, state.inspectionPlan, state.entries, state.planProgress, state.activeId, state.phase, state.lastStatus, sidebarOwner, publishSidebarSignal, inspectionSelection]);

  // 工作台提示只更新记录区与状态行；不把阅读位置拉回末尾，避免打断心流。
  const notice = (text: string) => {dispatch({type: 'notice', text});};
  const cancelSessionOpen = () => {sessionSwitch.current?.abort(); sessionSwitch.current = null;};
  // 旧会话打开：候选后端确认身份后才交给外层替换，失败/取消保留当前会话与草稿。
  const openOldSession = async (row: {session_id: string; identity: string}, focusQuery?: string) => {
    if (sessionNav.opening) return;
    if (!onSessionSwitch || attach) {sessionNav.setProblem('先返回原会话再打开旧会话'); return;}
    if (state.attachOffline) {sessionNav.setProblem('当前离线只读，不能切换会话'); return;}
    if (state.phase !== 'idle' || activeRun.current) {sessionNav.setProblem('当前任务运行中；结束后再打开旧会话'); return;}
    if (goal?.active) {sessionNav.setProblem('目标模式进行中；先 /goal stop 再切换'); return;}
    if (followupDraftDirty.current || followups.items.length && !followups.persisted || followups.writable && Boolean(followups.problem)) {
      sessionNav.setProblem('待办尚未保存；处理后再切换旧会话'); return;
    }
    if (input.trim()) {sessionNav.setProblem('草稿尚未发送；请先发送或清空再切换，草稿归属当前会话'); return;}
    if (!client.openSession || !state.workspace) {sessionNav.setProblem('当前客户端不支持打开旧会话'); return;}
    sessionSwitch.current?.abort();
    const controller = new AbortController(); sessionSwitch.current = controller;
    sessionNav.setOpening(true); sessionNav.setProblem('');
    try {
      const next = await client.openSession({workspace: state.workspace, sessionId: row.session_id, identity: row.identity}, controller.signal);
      if (controller.signal.aborted) {await next.shutdown(); return;}
      await onSessionSwitch(next, focusQuery ? {focusQuery} : undefined);
    } catch (error) {
      if (!controller.signal.aborted) sessionNav.setProblem(`打开失败：${String(error)}；当前会话保留`);
    } finally {
      if (sessionSwitch.current === controller) sessionSwitch.current = null;
      sessionNav.setOpening(false);
    }
  };
  const announce = (text: string) => {dispatch({type: 'notice', text}); setFeedback(text); setFeedbackTone('info');};
  const reportError = (text: string) => {notice(text); setFeedback(text); setFeedbackTone('error');};
  const launchGoalSegment = (nextGoal: GoalMode, display: string) => {
    try {
      const taskEnabled = client.capabilities?.().includes('tasks') ?? false;
      const task = taskEnabled ? {
        kind: 'goal' as const,
        objective: nextGoal.objective,
        ...(nextGoal.taskId ? {task_id: nextGoal.taskId} : {}),
      } : undefined;
      const id = client.run(expandExcerpts(goalPrompt(nextGoal)), task);
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
  const workspaceIsGit = () => {
    if (!state.workspace) return false;
    try {
      const result = spawnSync('git', ['-C', state.workspace, 'rev-parse', '--show-toplevel'], {encoding: 'utf8'});
      return result.status === 0;
    } catch {
      return false;
    }
  };
  const spawnBackgroundWorker = (taskId: string) => {
    if (!workspaceIsGit()) {
      dispatch({type: 'notice', text: '非 git 工作区不能转入后台写任务；请先 git init 或选择停止/等待。'});
      return false;
    }
    try {
      const args = ['--worker', '--workspace', state.workspace, '--session', state.sessionId, '--task-id', taskId];
      if (state.provider === 'fake') args.push('--fake');
      if (state.sessionPurpose === 'test') args.push('--test-session');
      const child = spawn(backendCommand, args, {...backendLaunchOptions(), detached: true, stdio: 'ignore'});
      child.unref();
      dispatch({type: 'notice', text: `已转入后台：task ${taskId}；worker 继续执行，重开 TUI 后用 /task 查看。`});
      return true;
    } catch (error) {
      dispatch({type: 'notice', text: `转入后台失败：${String(error)}`});
      return false;
    }
  };
  backgroundSpawnRef.current = spawnBackgroundWorker;
  const handleGoalDone = (result: RunResult): boolean => {
    const current = goalRef.current;
    if (!current?.active) return false;
    const decision = goalDecision(current, result);
    if (backgroundAfterRun.current) {
      backgroundAfterRun.current = false;
      goalRef.current = {...current, active: false, pausedReason: 'background_worker'};
      setGoal(goalRef.current);
      if (current.taskId) spawnBackgroundWorker(current.taskId);
      shutdownRef.current();
      return true;
    }
    if (waitForExit.current) {
      waitForExit.current = false;
      shutdownRef.current();
      return true;
    }
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
      if (focusSidebar(workspace, sidebarOwner)) {announce('侧栏已在运行 · 已聚焦'); return true;}
      const result = launchSidebar(workspace, mode, columns, sidebarOwner);
      announce(result.message);
      if (result.host && result.windowId) sidebarWindow.current = {host: result.host, windowId: result.windowId};
      if (mode === 'split' && result.outcome === 'started' && result.host === 'ghostty' && usesDefaultLauncher) {
        const totalColumns = columns;
        setTimeout(() => {if (!closing.current) adjustSidebarSplitForTui(workspace, totalColumns, sidebarOwner);}, 1200);
      }
      return ['started', 'focused', 'toggled'].includes(result.outcome);
    } catch (error) {
      reportError(`打开变更侧栏失败：${String(error)}`);
      return false;
    }
  };
  const inspectResult = (entryId: number, page: 'changes' | 'plan' | 'verification') => {
    const snapshot = entryId < 0 && historyVisible ? history.records[-entryId - 1] : latestState.current.entries.find(entry => entry.id === entryId)?.inspection;
    if (!snapshot || snapshot.workspace !== state.workspace || snapshot.sessionId !== state.sessionId) {
      setFeedback('结果来源已失效，未切换检查对象'); return;
    }
    if (entryId < 0) dispatch({type: 'retain_history', snapshot});
    history.close();
    setInspectionSelection({id: randomUUID(), page, snapshot});
    if (!openSidebarPanel()) showFocusedInspection();
  };
  const inspectCurrent = () => setInspectionSelection({id: randomUUID(), page: 'changes', snapshot: null});
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
  const runVibe = (args: string[]) => {
    try {
      const result = spawnSync(backendCommand, args, {...backendLaunchOptions(), encoding: 'utf8', timeout: 120_000});
      return {ok: result.status === 0, text: `${result.stdout ?? ''}${result.stderr ?? ''}`.trim()};
    } catch (error) {
      return {ok: false, text: String(error)};
    }
  };
  const shutdown = () => {
    if (closing.current) return;
    closing.current = true;
    setQuitting(true);
    // 工作台退出时联动收起同一进程启动的侧栏；崩溃场景由侧栏自己的父进程看护兜底。
    if (state.workspace) {
      try {killSidebarForTui(state.workspace, sidebarOwner);} catch {}
    }
    const windowInfo = sidebarWindow.current;
    if (windowInfo && !readSidebarLink(state.workspace, sidebarOwner)?.hasSelection) {
      try {closeSidebarWindowForTui(windowInfo.host, windowInfo.windowId, sidebarOwner);} catch {}
    }
    void client.shutdown().then(() => onDetach ? onDetach() : exit()).catch(error => {
      notice(`退出时发生错误：${String(error)}`);
      exit(error instanceof Error ? error : new Error(String(error)));
    });
  };
  shutdownRef.current = shutdown;
  const requestShutdown = () => {
    if (closing.current) return;
    handoffRef.current = null; setHandoff(null); taskConnection.cancel();
    const goal = goalRef.current;
    const running = Boolean(activeRun.current) || ['running', 'waiting_approval', 'stopping'].includes(state.phase);
    const unsavedFollowups = followupDraftDirty.current || followups.writable && Boolean(followups.problem);
    if ((unsavedFollowups || followups.items.length && !followups.persisted) && (attach || !running && !goal?.active)) {
      followups.pause('准备退出，待办保留');
      setQuitPrompt({index: 0, options: [
        {label: '返回查看后续消息', action: () => followups.show()},
        {label: unsavedFollowups ? '退出并放弃未保存的待办修改' : `${attach ? '断开' : '退出'}并丢弃 ${followups.items.length} 条待办（仅本次窗口）`, action: shutdown},
        {label: '取消关闭', action: () => {}},
      ]});
      return;
    }
    if (attach) {shutdown(); return;}
    if (!running && !goal?.active) {shutdown(); return;}
    const options: {label: string; action: () => void; disabled?: boolean}[] = [];
    if (goal?.active) {
      options.push({
        label: !goal.taskId || !state.workspace || !state.sessionId ? '转入后台（当前任务缺少 task 记录，暂不可用）' : (workspaceIsGit() ? '转入后台（本段结束后）' : '转入后台（非 git 工作区不可用）'),
        disabled: !(goal.taskId && state.workspace && state.sessionId && workspaceIsGit()),
        action: () => {
          if (!goal?.taskId) return;
          const activeNow = Boolean(activeRun.current) || ['running', 'waiting_approval', 'stopping'].includes(latestState.current.phase);
          backgroundAfterRun.current = true;
          if (!activeNow) {
            backgroundAfterRun.current = false;
            spawnBackgroundWorker(goal.taskId);
            shutdown();
          } else {
            notice('当前段结束后转入后台；请保持窗口打开直到该段收尾。');
          }
        },
      });
    }
    options.push({
      label: '等待完成（当前运行结束后退出）',
      action: () => {
        waitForExit.current = true;
        const activeNow = Boolean(activeRun.current) || ['running', 'waiting_approval', 'stopping'].includes(latestState.current.phase);
        if (!activeNow) {waitForExit.current = false; shutdown();}
        else notice('等待当前运行结束后退出…');
      },
    });
    options.push({
      label: '停止任务并退出',
      action: () => {
        if (activeRun.current) {
          try {client.cancel(activeRun.current);} catch {}
        }
        if (goalRef.current?.active) {
          const stopped = {...goalRef.current, active: false, pausedReason: 'user_stopped'};
          goalRef.current = stopped;
          setGoal(stopped);
        }
        shutdown();
      },
    });
    options.push({label: '取消关闭', action: () => setQuitPrompt(null)});
    if (followups.count || unsavedFollowups) {
      followups.pause('准备退出，待办保留');
      for (const option of options.slice(0, -1)) option.label += unsavedFollowups ? ' · 放弃未保存的待办修改' : followups.persisted ? ' · 待办已保存' : ` · 丢弃 ${followups.items.length} 条待办`;
    }
    setQuitPrompt({options, index: 0});
  };
  const connectSelectedTask = (taskId: string, stop = false) => {
    const target = {workspace: state.workspace, sessionId: state.sessionId, taskId};
    if (activeRun.current || goalRef.current?.active) {setHandoff({target, stop, mode: 'choose'}); if (!navigation.open) navigation.show(); return;}
    followups.pause('接入任务期间暂停，待办保留');
    void taskConnection.connect(target, stop);
  };
  const prepareHandoff = (stopCurrent: boolean) => {
    if (!handoff) return;
    followups.pause('等待接入任务，待办保留');
    if (goalRef.current?.active) {
      const paused = {...goalRef.current, active: false, pausedReason: 'user_stopped'};
      goalRef.current = paused; setGoal(paused);
    }
    if (!activeRun.current) {setHandoff(null); void taskConnection.connect(handoff.target, handoff.stop); return;}
    const waiting = {...handoff, mode: 'wait' as const}; handoffRef.current = waiting; setHandoff(waiting);
    if (stopCurrent) try {
      controlRequests.current.set(client.cancel(activeRun.current), 'cancel'); dispatch({type: 'cancel'});
    } catch (error) {handoffRef.current = null; setHandoff(null); notice(`停止当前运行未送达：${String(error)}；未切换任务`);}
  };
  const cancelHandoff = () => {
    handoffRef.current = null; setHandoff(null); taskConnection.cancel();
  };
  const navigate = (target: number) => {
    const current = viewport.current;
    const next = Math.max(0, Math.min(target, current.maximum));
    viewport.current = {...current, top: next};
    if (current.overlayKey) setOverlayScroll({key: current.overlayKey, top: next});
    else setScroll({follow: next === current.maximum, top: next});
  };
  const moveReading = (delta: number) => navigate(viewport.current.top + delta);
  const selectApproval = (action: ApprovalAction) => {
    if (!state.approval) return;
    const expanded = action === 'session' || action === 'workspace' ? true : action === 'once' || action === 'deny' ? false : approvalExpanded;
    setApprovalMore(expanded ? state.approval.id : null);
    setApprovalSelection({id: state.approval.id, decision: action});
    const label = approvalButtons(decisions, state.approval.tool, expanded, columns).find(button => button.action === action)?.label;
    setFeedback(`已选择：${label}；Enter 确认`);
  };
  const activateApproval = (action: ApprovalAction) => {
    const approval = latestState.current.approval;
    if (!approval || approval.id !== state.approval?.id || terminalTooSmall || quitting || quitPrompt || answeredApprovals.current.has(approval.id)) return;
    if (action === 'more' || action === 'back') {
      setApprovalMore(action === 'more' ? approval.id : null);
      setApprovalSelection({id: approval.id, decision: null});
      setOverlayScroll({key: overlayKey, top: 0}); setFeedback('');
      return;
    }
    if (!decisions.includes(action)) return;
    try {
      const id = client.approve(approval.id, action);
      controlRequests.current.set(id, 'approval');
      answeredApprovals.current.add(approval.id);
      setApprovalSelection(null); setFeedback('审批已发送，等待后端确认'); dispatch({type: 'approval_sent'});
    } catch (error) {reportError(`审批响应未送达：${String(error)}`);}
  };
  useTranscriptMouse({
    onPointer: event => {
      if (followupRows && !quitPrompt) {
        const hit = followupTargets.current.find(target => event.row >= target.top && event.row <= target.bottom && event.column >= target.left && event.column <= target.right);
        const key = `${columns}:${rows}:${followups.items.map(item => item.id).join()}:${hit?.action}`;
        if (event.type === 'press' && event.button === 0) followupPress.current = hit ? key : null;
        else if (event.type === 'release') {const pressed = followupPress.current; followupPress.current = null; if (hit && pressed === key) {if (hit.action === 'parent') requestShutdown(); else if (hit.action === 'tasks') navigation.show(); else followups.show(); return true;}}
        else followupPress.current = null;
        if (hit) return true;
      }
      if ((!overlayKey || historyVisible) && !terminalTooSmall && !quitting && !quitPrompt) {
        const cell = event.column - selectionBody.left;
        const line = event.row >= selectionBody.top && event.row <= selectionBody.bottom ? lines[top + event.row - selectionBody.top] : undefined;
        const action = line?.inspectionActions?.find(action => cell >= action.left && cell <= action.right);
        const current = inspectionTargets.current.find(target => event.row >= target.top && event.row <= target.bottom && event.column >= target.left && event.column <= target.right)?.action;
        const hit = current ? {entryId: null, page: current} : action && line?.entryId !== undefined ? {entryId: line.entryId, page: action.page} : null;
        const key = `${columns}:${rows}:${top}:${inspectionSelection?.id ?? ''}:${historyVisible ? history.cursor ?? 'first' : 'closed'}`;
        if (event.type === 'wheel' || event.type === 'drag' || event.button !== 0) inspectionPress.current = null;
        else if (event.type === 'press') {inspectionPress.current = hit ? {...hit, key} : null; if (hit) return true;}
        else if (event.type === 'release') {
          const pressed = inspectionPress.current; inspectionPress.current = null;
          if (hit && pressed?.key === key && pressed.entryId === hit.entryId && pressed.page === hit.page) {
            if (hit.page === 'focus') showFocusedInspection();
            else if (hit.page === 'current') inspectCurrent();
            else if (hit.page === 'history') {history.show(); setOverlayScroll({key: 'inspection-history', top: 0});}
            else if (hit.page === 'back') history.close();
            else if (hit.page === 'retry') history.retry();
            else if (hit.page === 'older' || hit.page === 'newer') {history[hit.page](); setOverlayScroll({key: 'inspection-history', top: 0});}
            else if (hit.entryId !== null) inspectResult(hit.entryId, hit.page);
          }
          if (pressed || hit) return true;
        }
      } else inspectionPress.current = null;
      if (!state.approval && revision.mode && !terminalTooSmall && !quitting && !quitPrompt) {
        const layout = `${overlayKey}:${columns}:${rows}:${revision.buttons.map(button => button.action).join(',')}`;
        const hit = revisionTargets.current.find(target => event.row >= target.top && event.row <= target.bottom && event.column >= target.left && event.column <= target.right);
        if (event.type === 'wheel' || event.type === 'drag' || event.button !== 0) {revisionPress.current = null; return false;}
        if (event.type === 'press') {
          revisionPress.current = hit ? {layout, action: hit.action} : null;
          if (hit) revision.select(hit.action);
          else if (revision.mode === 'edit') revision.select(null);
          return Boolean(hit);
        }
        const pressed = revisionPress.current; revisionPress.current = null;
        if (pressed && pressed.layout === layout && pressed.action === hit?.action) revision.activate(hit.action);
        return Boolean(pressed || hit);
      }
      revisionPress.current = null;
      if (!state.approval || terminalTooSmall || quitting || quitPrompt) {approvalPress.current = null; return false;}
      const hit = approvalTargets.current.find(target => approvalChoices.some(button => button.action === target.action) &&
        event.row >= target.top && event.row <= target.bottom && event.column >= target.left && event.column <= target.right);
      if (event.type === 'wheel' || event.type === 'drag') {approvalPress.current = null; return false;}
      if (event.button !== 0) {approvalPress.current = null; return false;}
      if (event.type === 'press') {
        approvalPress.current = hit ? {layout: approvalLayout, action: hit.action} : null;
        if (hit) selectApproval(hit.action);
        return Boolean(hit);
      }
      const pressed = approvalPress.current;
      approvalPress.current = null;
      if (pressed && pressed.layout === approvalLayout && pressed.action === hit?.action) activateApproval(hit.action);
      return Boolean(pressed || hit);
    },
    onScroll: delta => {if (revision.mode !== 'edit') {setSelection(null); moveReading(delta);}},
    top: headerRows + compactRows + 1, bottom: headerRows + compactRows + paneRows, left: 1, right: readerColumns,
    body: revision.mode === 'edit' ? undefined : selectionBody,
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
    enabled: surfaceActive && !inspectionVisible && !followupsVisible && !navigationVisible && !sessionNavVisible && !permissionPanelVisible && !secondaryPanelVisible && !quitting && rows >= 11 && columns >= 24,
  });
  const openTool = () => {if (chosenTool) {setDetail(chosenTool.id); setOverlayScroll({key: `tool-${chosenTool.id}`, top: 0});}};
  const openContext = () => {setDetail(null); setContextSelection(null); setContextOpen(true); setOverlayScroll({key: 'context', top: 0});};
  const copyTarget = (): [string, string] | null => {
    if (revision.mode === 'history' && !state.approval) {
      const entry = revision.history?.records[revision.history.index];
      if (entry) return [entry.feedback, '修订反馈'];
    }
    if (revision.mode && revision.record && !state.approval) return [revision.record.draft, '修订反馈'];
    if (review.focused && review.body && !state.approval) return [review.bodyText, '当前计划正文页'];
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
  useEffect(() => {if (state.phase === 'running') setHighlightLine(null);}, [state.phase]);

  useInput((keyInput, key) => {
    if (!surfaceActive) return;
    if (key.eventType === 'release') return;
    if (quitPrompt) {
      const options = quitPrompt.options;
      const choose = (index: number) => {
        const option = options[index];
        if (!option || option.disabled) return;
        setQuitPrompt(null);
        option.action();
      };
      if (key.escape) {setQuitPrompt(null); return;}
      if (key.upArrow || key.downArrow) {
        const delta = key.upArrow ? -1 : 1;
        setQuitPrompt(current => {
          if (!current) return current;
          let index = current.index;
          for (let step = 0; step < current.options.length; step += 1) {
            index = (index + delta + current.options.length) % current.options.length;
            if (!current.options[index]?.disabled) break;
          }
          return {...current, index};
        });
        return;
      }
      if (key.return) {choose(quitPrompt.index); return;}
      if (/^[1-9]$/.test(keyInput ?? '')) {choose(Number(keyInput) - 1); return;}
      return;
    }
    if (key.ctrl && keyInput === 'q') {setInspectionFocused(false); requestShutdown(); return;}
    if (key.ctrl && keyInput === 'c') {
      setInspectionFocused(false);
      if (attach) {notice(state.attachOffline ? '离线只读：Ctrl+Q 退出结果查看。' : 'attach 模式：Ctrl+Q 断开连接，worker 会继续运行。'); return;}
      if (activeRun.current && state.phase !== 'stopping') {
        try {
          const id = client.cancel(activeRun.current);
          controlRequests.current.set(id, 'cancel');
          dispatch({type: 'cancel'});
          dispatch({type: 'notice', text: '停止请求已发送，等待后端确认…'});
        } catch (error) {reportError(`取消请求未送达：${String(error)}`);}
      } else if (!state.activeId) requestShutdown();
      return;
    }
    if (inspectionVisible || followupsVisible || navigationVisible || sessionNavVisible || permissionPanelVisible || secondaryPanelVisible || terminalTooSmall) return;
    if (key.ctrl && keyInput === 'l' && !state.approval && !quitting && (planListOpen || (state.planProgress && !overlayKey))) {
      setPlanListOpen(open => !open);
      if (!planListOpen) setOverlayScroll({key: 'plan-list', top: 0});
      return;
    }
    if (key.escape && planListOpen) {setPlanListOpen(false); return;}
    if (planBarShown && review.card) {
      const step = planBarSteps[planBarSelected];
      const prefillStep = (command: string, label: string) => {
        if (!step) return;
        setPlanBarHidden(true);
        setInput(`${command} ${step.step_id} `);
        setFeedback(`补全${label}后 Enter 提交（Esc 取消）`);
      };
      if (key.escape) {setPlanBarHidden(true); setFeedback('计划稍后处理 · Ctrl+R 打开审阅'); return;}
      if (key.ctrl && keyInput === 'r') {review.focus(); return;}
      if (key.upArrow || key.leftArrow || key.tab && key.shift) {setPlanBarIndex(index => Math.max(0, index - 1)); return;}
      if (key.downArrow || key.rightArrow || key.tab && !key.shift) {setPlanBarIndex(index => Math.min(planBarSteps.length - 1, index + 1)); return;}
      if (key.pageUp) {setPlanBarIndex(index => Math.max(0, index - 5)); return;}
      if (key.pageDown) {setPlanBarIndex(index => Math.min(planBarSteps.length - 1, index + 5)); return;}
      if (key.home) {setPlanBarIndex(0); return;}
      if (key.end) {setPlanBarIndex(Math.max(0, planBarSteps.length - 1)); return;}
      if (!key.ctrl && !key.meta && /^[1-9]$/.test(keyInput ?? '')) {
        setPlanBarIndex(Math.min(planBarSteps.length - 1, Number(keyInput) - 1));
        return;
      }
      if (key.return) {
        if (review.pending || review.decisionPending) {setFeedback('计划回执处理中，请稍候'); return;}
        review.act('execute');
        return;
      }
      if (key.ctrl || key.meta) return;
      if (keyInput === 'b') {review.focus(); review.act('body'); return;}
      if (keyInput === 'r' && review.choices.some(choice => choice.value === 'revise')) {review.act('revise'); return;}
      if (keyInput === 'a' && review.choices.some(choice => choice.value === 'approve')) {review.act('approve'); return;}
      if (keyInput === 'd' && review.choices.some(choice => choice.value === 'dismiss')) {review.act('dismiss'); return;}
      if (keyInput === 'm') {prefillStep('/plan edit-step', '新标题'); return;}
      if (keyInput === 'k') {prefillStep('/plan kind', '步骤类型'); return;}
      if (keyInput === 'f') {prefillStep('/plan files', '文件范围'); return;}
      if (keyInput === 'p') {prefillStep('/plan depends', '依赖步骤'); return;}
      return;
    }
    if (key.ctrl && keyInput === 'y') {copyCurrent(); return;}
    if (key.ctrl && keyInput === 'g') {openSidebarPanel(); return;}
    if (state.approval) {
      if (key.ctrl && keyInput === 'r') {
        setApprovalSelection(approvalFocused ? null : {id: state.approval.id, decision: null});
        setFeedback('');
        return;
      }
      if (key.escape) {setApprovalSelection(null); setApprovalMore(null); setFeedback(''); return;}
      // 审批是阻塞态：空草稿时 y/n 直接决定（允许本次/拒绝），有草稿时按键仍属于草稿。
      if (!key.ctrl && !key.meta && !input.trim()) {
        const direct = keyInput === 'y' ? (decisions.includes('once') ? 'once' : null)
          : keyInput === 'n' ? (decisions.includes('deny') ? 'deny' : null) : null;
        if (direct) {
          activateApproval(direct);
          // 输入框可能先收到同一个字母；快捷决定后不要把 y/n 留进草稿。
          if (keyInput === 'y' || keyInput === 'n') setInput(previous => previous.trim() === keyInput ? '' : previous);
          return;
        }
      }
      if (!approvalFocused) {
        // Tab/点击选中按钮后，无需 Ctrl+R 聚焦即可用 Enter 确认；有草稿时保持草稿优先。
        if (key.return && !key.ctrl && !key.meta && approvalSelection?.decision && !input.trim()) {
          activateApproval(approvalSelection.decision);
        }
        return;
      }
      if (key.return && !key.ctrl && !key.meta) {
        const decision = approvalSelection?.decision;
        if (!decision) {setFeedback('先选择授权范围，再按 Enter 确认'); return;}
        activateApproval(decision);
        return;
      }
      const move = key.downArrow || key.rightArrow || key.tab && !key.shift ? 1 : key.upArrow || key.leftArrow || key.tab && key.shift ? -1 : 0;
      const index = approvalChoices.findIndex(button => button.action === approvalSelection?.decision);
      if (!key.ctrl && !key.meta && move) {
        const next = index < 0 ? move > 0 ? 0 : approvalChoices.length - 1 : index + move;
        if (key.tab && (next < 0 || next >= approvalChoices.length)) {setApprovalSelection(null); setFeedback(''); return;}
        const button = approvalChoices[(next + approvalChoices.length) % approvalChoices.length];
        if (button) selectApproval(button.action);
        return;
      }
      const decision = !key.ctrl && !key.meta ? approvalKeys[keyInput.toLowerCase()] : undefined;
      if (decision && decisions.includes(decision)) {
        selectApproval(decision);
        return;
      }
    }
    if (historyVisible) {
      if (key.tab && history.records.length) {
        const index = historyIndex === null ? 0 : (historyIndex + (key.shift ? -1 : 1) + history.records.length) % history.records.length;
        setHistoryIndex(index);
        const line = lines.findIndex(line => line.entryId === -index - 1);
        setOverlayScroll({key: 'inspection-history', top: Math.max(0, Math.min(maximum, line))}); return;
      }
      if (key.return && historyIndex !== null) {inspectResult(-historyIndex - 1, 'verification'); return;}
      if (key.escape) {history.close(); return;}
      if (key.leftArrow || key.rightArrow) {if (key.rightArrow) history.older(); else history.newer(); return;}
      if (key.upArrow || key.downArrow) {moveReading(key.downArrow ? 1 : -1); return;}
      if (key.pageUp || key.pageDown) {moveReading((key.pageDown ? 1 : -1) * Math.max(1, bodyRows - 1)); return;}
      return;
    }
    if (revision.mode && !state.approval) {
      if (revision.mode === 'history') {
        if (key.upArrow || key.downArrow) {revision.historyMove(key.downArrow ? 1 : -1); return;}
        if (key.pageUp || key.pageDown) {revision.historyPage(key.pageDown ? 1 : -1); return;}
      }
      if (key.escape) {if (revision.mode === 'edit' && revision.selected) revision.select(null); else if (revision.mode === 'confirm') revision.activate('edit'); else revision.close(); return;}
      if (key.tab) {if (revision.mode !== 'edit' || revision.selected) revision.move(key.shift ? -1 : 1); return;}
      if (revision.mode === 'edit' && !revision.selected) return;
      if (key.leftArrow || key.rightArrow) {revision.move(key.rightArrow ? 1 : -1); return;}
      if (key.return && !key.ctrl && !key.meta) {if (revision.selected) revision.activate(revision.selected); else setFeedback('Tab 选择动作，再按 Enter 确认'); return;}
      if (key.upArrow || key.downArrow) {moveReading(key.downArrow ? 1 : -1); return;}
      if (key.pageUp || key.pageDown) {moveReading((key.pageDown ? 1 : -1) * Math.max(1, bodyRows - 1)); return;}
      return;
    }
    if (!state.approval && review.card && key.ctrl && keyInput === 'r') {
      review.focus(); setReading(false); setContextOpen(false); setDetail(null); setPlanOpen(false);
      setOverlayScroll({key: 'review', top: 0}); setFeedback('');
      return;
    }
    if (review.focused && !state.approval) {
      if (key.escape) {review.blur(); setFeedback(''); return;}
      if (review.body) {
        if (key.leftArrow || key.rightArrow) {review.page(key.rightArrow ? 1 : -1); return;}
        if (!key.ctrl && !key.meta && keyInput === 'a') {review.actions(); return;}
        if (key.pageUp || key.pageDown) {moveReading((key.pageDown ? 1 : -1) * Math.max(1, bodyRows - 1)); return;}
        if (key.upArrow || key.downArrow) {moveReading(key.downArrow ? 1 : -1); return;}
        if (key.home || key.end) {navigate(key.end ? viewport.current.maximum : 0); return;}
        return;
      }
      if (key.return && !key.ctrl && !key.meta) {review.confirm(); return;}
      if (key.leftArrow || key.rightArrow) {review.cycle(key.rightArrow ? 1 : -1); return;}
      if (!key.ctrl && !key.meta) {
        review.select(keyInput.toLowerCase(), key.downArrow || key.tab && !key.shift ? 1 : key.upArrow || key.tab && key.shift ? -1 : 0);
      }
      return;
    }
    if (planOpen && key.return) {
      setPlanOpen(false);
      submit('/plan execute');
      return;
    }
    if (planOpen && !key.ctrl && !key.meta && (keyInput === 'y' || keyInput === 'n')) {
      if (planStatus !== 'proposed') {notice(`当前计划状态为 ${planStatus || '未知'}；只有 proposed 可审批。`); return;}
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再审批计划。'); return;}
      try {
        const id = client.command('plan', {action: keyInput === 'y' ? 'approve' : 'reject'});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
      } catch (error) {notice(`计划审批未送达：${String(error)}`);}
      return;
    }
    if (planOpen && (key.upArrow || key.downArrow)) {
      const next = Math.max(0, Math.min(planLines.length - 1, planSelection + (key.upArrow ? -1 : 1)));
      setPlanSelection(next);
      const maxTop = Math.max(0, planLines.length - bodyRows);
      const nextTop = Math.max(0, Math.min(maxTop, next - Math.floor(bodyRows / 2)));
      setOverlayScroll({key: 'plan', top: nextTop});
      return;
    }
    if (planOpen && !key.ctrl && !key.meta && keyInput === 'c') {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再查看审批卡。'); return;}
      try {
        const id = client.command('plan', {action: 'card'});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
      } catch (error) {notice(`审批卡查询未送达：${String(error)}`);}
      return;
    }
    if (planOpen && !key.ctrl && !key.meta && keyInput === 'a') {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再查看验收标准。'); return;}
      try {
        const id = client.command('plan', {action: 'acceptance'});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
      } catch (error) {notice(`验收标准查询未送达：${String(error)}`);}
      return;
    }
    if (planOpen && !key.ctrl && !key.meta && keyInput === 'v') {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再查看计划版本。'); return;}
      try {
        const id = client.command('plan', {action: 'list'});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
      } catch (error) {notice(`计划版本查询未送达：${String(error)}`);}
      return;
    }
    if (planOpen && !key.ctrl && !key.meta && keyInput === 'd') {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再比较计划版本。'); return;}
      if (planVersion <= 1) {notice('当前没有更早的计划版本可比较。'); return;}
      try {
        const id = client.command('plan', {action: 'diff', version_a: planVersion - 1, version_b: planVersion});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
      } catch (error) {notice(`计划版本比较未送达：${String(error)}`);}
      return;
    }
    if (planOpen && !key.ctrl && !key.meta && keyInput === 'b') {
      setPlanOpen(false);
      submit('/plan background');
      return;
    }
    if (planOpen && !key.ctrl && !key.meta && keyInput === 'e') {
      setPlanOpen(false);
      setInput('/plan revise ');
      notice('输入新的计划目标后回车提交修订（生成新 draft 版本）。');
      return;
    }
    if (planOpen && !key.ctrl && !key.meta && keyInput === 'r') {
      setPlanOpen(false);
      setInput('/plan request-changes ');
      notice('输入对计划的反馈后回车，请求按反馈修改。');
      return;
    }
    if (planOpen && !key.ctrl && !key.meta && keyInput === 'm') {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再查看完整审阅稿。'); return;}
      try {
        const id = client.command('plan', {action: 'markdown'});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
      } catch (error) {notice(`完整审阅稿查询未送达：${String(error)}`);}
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
    if (key.escape && !state.approval && planOpen) {setPlanOpen(false); return;}
    if (planOpen && !key.ctrl && !key.meta && keyInput === 's') {
      const selectedLine = planLines[planSelection] ?? '';
      const match = /^\s*-\s+(\S+)\s/.exec(selectedLine);
      if (!match) {notice('当前行不是步骤；用 ↑↓ 选择步骤行。'); return;}
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再查看步骤详情。'); return;}
      try {
        const id = client.command('plan', {action: 'step', step_id: match[1]!});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
      } catch (error) {notice(`步骤详情未送达：${String(error)}`);}
      return;
    }
    if (planOpen && !key.ctrl && !key.meta && keyInput === 't') {
      const selectedLine = planLines[planSelection] ?? '';
      const match = /^\s*-\s+(\S+)\s/.exec(selectedLine);
      if (!match) {notice('当前行不是步骤；用 ↑↓ 选择步骤行。'); return;}
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再编辑步骤。'); return;}
      setPlanOpen(false);
      setInput(`/plan edit-step ${match[1]} `);
      notice('输入新的步骤标题后回车提交（生成新 draft 版本）。');
      return;
    }
    if (planOpen && !key.ctrl && !key.meta && keyInput === 'k') {
      const selectedLine = planLines[planSelection] ?? '';
      const match = /^\s*-\s+(\S+)\s/.exec(selectedLine);
      if (!match) {notice('当前行不是步骤；用 ↑↓ 选择步骤行。'); return;}
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再编辑步骤。'); return;}
      setPlanOpen(false);
      setInput(`/plan kind ${match[1]} `);
      notice('输入步骤类型 read|edit|run|verify 后回车（生成新 draft 版本）。');
      return;
    }
    if (key.escape && !state.approval && contextOpen) {setContextOpen(false); return;}
    if (key.escape && !state.approval && detailEntry) {setDetail(null); return;}
    if (key.escape && reading && !state.approval) {setReading(false); return;}
    if (reading && keyInput === ' ' && !state.approval && !detailEntry) {moveReading(Math.max(1, bodyRows - 1)); return;}
    if (state.approval) return;
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

  const fallbackLocalRounds = () => {
    const items = roundItems(state.entries, lines);
    if (!items.length) {notice('本次会话还没有可定位的轮次。'); return;}
    setLocator({title: `按轮定位 · ${items.length} 轮`, items, kind: 'rounds'});
  };
  const requestRoundsLedger = () => {
    if (!roundsLedgerAvailable) {fallbackLocalRounds(); return;}
    if (roundsRequest.current) return;
    try {
      const id = client.command('rounds', {});
      roundsRequest.current = {id, timer: setTimeout(() => {
        roundsRequest.current = null;
        notice('轮次账本读取超时；改用正文轮次。');
        fallbackLocalRounds();
      }, 10_000)};
      notice('正在读取轮次账本…');
    } catch (error) {
      notice(`轮次账本未能读取：${String(error)}；改用正文轮次。`);
      fallbackLocalRounds();
    }
  };
  const ledgerItems = (page: RoundsLedgerPage): LocateItem[] => {
    const firstLine = firstLineByEntry(lines);
    const linesByGoal = new Map<string, number>();
    for (const entry of state.entries) {
      if (entry.kind !== 'task') continue;
      const goal = entry.text.trim();
      const line = firstLine.get(entry.id);
      if (goal && line !== undefined && !linesByGoal.has(goal)) linesByGoal.set(goal, line);
    }
    return page.rounds.map(round => {
      const line = linesByGoal.get(round.goal.trim()) ?? -1;
      const verified = round.verified === true ? ' · 已验证' : round.verified === false ? ' · 验证未通过' : '';
      return {line, label: `第 ${round.index} 轮 · ${round.status}${verified} · ${round.goal}`,
        detail: `${round.saved_at ?? '时间未知'} · steps=${round.steps} · $${round.cost_usd.toFixed(6)} · ${line >= 0 ? `正文第 ${line + 1} 行` : '不在当前正文'}`};
    });
  };
  const requestHistorySearch = (query: string, cursor?: string, append = false) => {
    if (!historySearchAvailable) {notice('后端不支持跨会话检索；可用 /find 搜索当前会话。'); return;}
    if (searchRequest.current) return;
    try {
      const id = client.command('search', cursor ? {query, cursor} : {query});
      searchRequest.current = {id, append, timer: setTimeout(() => {
        searchRequest.current = null;
        setSearchPanel(prev => prev ? {...prev, loading: false, problem: '检索超时；可重试'} : prev);
      }, 15_000)};
      setSearchPanel(prev => ({query, matches: append && prev ? prev.matches : [], next: append && prev ? prev.next : null,
        loading: true, problem: '', truncated: false, skipped: 0}));
    } catch (error) {notice(`跨会话检索未能开始：${String(error)}`);}
  };
  const openSearchMatch = (match: HistorySearchMatch) => {
    if (!onSessionSwitch) {notice('先返回原会话再打开旧会话'); return;}
    const focus = searchPanel?.query;
    setSearchPanel(null);
    void openOldSession({session_id: match.session_id, identity: match.identity}, focus);
  };
  ledgerItemsRef.current = ledgerItems;
  localRoundsRef.current = fallbackLocalRounds;
  const rejectBusyCommand = () => {
    if (state.phase === 'idle' && !activeRun.current) return false;
    notice('未执行：当前任务运行中；草稿保留，结束后重试。');
    return true;
  };
  const submit = (value: string) => {
    const text = value.trim();
    if (!text || quitting) return;
    if (state.attachOffline && !/^\/(?:inspections|sidebar(?: split| quick| window)?|context|copy|help(?: .*)?|theme(?: .*)?|session|quit|exit)$/.test(text)) {
      notice('离线结果只读；可查看历史结果，草稿已保留。'); return;
    }
    setSelection(null);
    const unavailable = commandUnavailable(text, {...commandContext, busy: state.phase !== 'idle' || Boolean(activeRun.current)});
    if (unavailable) {notice(`未执行：${unavailable}；草稿保留。`); return;}
    const definition = commandDefinition(text);
    if (definition?.required) {
      setInput(text + ' ');
      setFeedback(`请补充参数 · 用法：${text} ${definition.usage}`);
      setFeedbackTone('info');
      return;
    }
    if (text === '/quit' || text === '/exit') {setInput(''); requestShutdown(); return;}
    if (text === '/followup') {setInput(''); followups.show(); return;}
    if ((text === '/task' || text === '/tasks') && navigationAvailable) {setInput(''); navigation.show(); return;}
    if (text === '/sessions') {setInput(''); if (!sessionNavAvailable) {notice('后端未连接或不支持旧会话目录。'); return;} sessionNav.show(); return;}
    if (text === '/permissions' && permissionPanelAvailable) {setInput(''); permissionPanel.show(); return;}
    if (text === '/find' || text.startsWith('/find ')) {
      setInput('');
      const query = text.slice('/find'.length).trim();
      if (!query) {notice('用法：/find <关键词>：搜索本次会话正文并定位。'); return;}
      const items = searchTranscript(lines, query).map(item => ({...item, highlight: query}));
      if (!items.length) {notice(`没有找到“${query}”；试试更短的关键词，或用 /rounds、/errors 定位。`); return;}
      setLocator({title: `查找 ${query} · ${items.length} 处`, items, kind: 'find'});
      return;
    }
    if (text === '/rounds') {
      setInput('');
      requestRoundsLedger();
      return;
    }
    if (text === '/history' || text.startsWith('/history ')) {
      setInput('');
      const query = text.slice('/history'.length).trim();
      if (!query) {notice('用法：/history <关键词>：跨会话检索已保存的消息并打开定位。'); return;}
      requestHistorySearch(query);
      return;
    }
    if (text === '/errors') {
      setInput('');
      const items = errorItems(state.entries, lines);
      if (!items.length) {notice('本次会话没有记录到工具错误或失败轮次。'); return;}
      setLocator({title: `错误定位 · ${items.length} 处`, items, kind: 'errors'});
      return;
    }
    if (text === '/context') {setInput(''); openContext(); return;}
    if (text === '/copy') {setInput(''); copyCurrent(); return;}
    if (text === '/sidebar setup') {setInput(''); setupSidebarConfig(); return;}
    if (text === '/sidebar split') {setInput(''); openSidebarPanel('split'); return;}
    if (text === '/sidebar quick') {setInput(''); openSidebarPanel('quick'); return;}
    if (text === '/sidebar window') {setInput(''); openSidebarPanel('window'); return;}
    if (text === '/inspections') {setInput(''); history.show(); setOverlayScroll({key: 'inspection-history', top: 0}); return;}
    if (text === '/logs') {setInput(''); setLogsOpen(true); return;}
    if (text === '/sidebar') {setInput(''); openSidebarPanel(); return;}
    if (text === '/project' || text.startsWith('/project ')) {setInput(''); projectCommand(text.slice('/project'.length)); return;}
    if (text === '/about' || text.startsWith('/about ')) {setInput(''); aboutCommand(text.slice('/about'.length)); return;}
    if (text === '/goal' || text.startsWith('/goal ')) {
      const rest = text.slice('/goal'.length).trim();
      if (rest && rest !== 'stop' && rejectBusyCommand()) return;
      setInput(''); handleGoalCommand(rest); return;
    }
    if (text === '/help' || text.startsWith('/help ')) {
      setInput(''); notice(commandHelp(text.slice('/help'.length))); return;
    }
    if (text.startsWith('/theme')) {
      setInput('');
      const choice = text.split(/\s+/)[1];
      if (choice === 'system' || choice === 'light' || choice === 'dark') {setTheme(choice); announce(`主题已切换：${choice}`);}
      else announce(`当前主题：${theme}；终端颜色：${['无色', '16 色', '256 色', '真彩色'][terminalColorLevel()]}。用法：/theme system|light|dark`);
      return;
    }
    if (text === '/session') {setInput(''); notice(`会话：${state.sessionId || '待连接'}\n${state.sessionPath || '后端尚未提供会话路径。'}`); return;}
    let followupText: string | null = null;
    if (text.split(/\s+/, 1)[0] === '/followup') {
      const rest = text.slice('/followup'.length).trim();
      if (state.phase === 'running' && activeRun.current) {
        if (state.activeCommand && state.activeCommand !== 'resume') {
          notice('命令执行中，结束后再发送');
          return;
        }
        if (!followups.add(rest)) {notice('后续消息未保存，草稿保留；打开 /followup 查看原因'); return;}
        setInput('');
        notice(`已排队为后续消息：${rest}`);
        return;
      }
      followupText = rest;
    }
    if (text.startsWith('/autonomy')) {
      const level = text.slice('/autonomy'.length).trim();
      if (!level) {
        setInput('');
        const current = state.autonomy ? `${state.autonomy} · ${AUTONOMY_LABELS[state.autonomy as AutonomyLevel] ?? state.autonomyLabel}` : '未知';
        notice(`当前授权档位：${current}\n用法：/autonomy ask|edits|workspace|full（ask 每次询问；edits 自动改文件；workspace 工作区自动；full 包含网络）`);
        return;
      }
      if (!AUTONOMY_LEVELS.includes(level as AutonomyLevel)) {notice('用法：/autonomy ask|edits|workspace|full'); return;}
      if (rejectBusyCommand()) return;
      try {
        const id = client.command('autonomy', {level: level as AutonomyLevel});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'autonomy'});
        setInput('');
        setFeedback('');
      } catch (error) {reportError(`授权档位未送达：${String(error)}`);}
      return;
    }
    if (text === '/stop-workers' || text === '/workers stop') {
      setInput('');
      if (!state.workspace) {notice('尚未连接工作区。'); return;}
      const result = runVibe(['--stop', '--workspace', state.workspace]);
      notice(result.ok ? result.text || '已写入停止信号。' : `停止失败：${result.text}`);
      return;
    }
    if (text === '/attach' || text.startsWith('/attach ')) {
      setInput('');
      if (!state.workspace || !state.sessionId) {notice('尚未连接工作区/会话。'); return;}
      const parsed = parseAttachCommand(text, lastTaskId.current ?? goalRef.current?.taskId ?? null);
      const requested = parsed.taskId;
      if (!requested) {notice('当前会话没有可 attach 的 task；先运行 /task 查看。'); return;}
      const fake = state.provider === 'fake' || state.sessionPurpose === 'test' ? ' --fake' : '';
      if (parsed.now) {
        if (attach) {notice('已在任务接入中；请先返回原会话再选择任务'); return;}
        if (!navigation.open) navigation.show();
        connectSelectedTask(requested);
        return;
      }
      notice(`运行以下命令连接后台任务：\n./run_ink.sh --attach-task ${requested} --session ${state.sessionId} --workspace ${state.workspace}${fake}\n或用 /attach now 在当前工作台接入；原草稿保留。`);
      return;
    }
    if (text === '/merge' || text.startsWith('/merge ')) {
      setInput('');
      if (!state.workspace || !state.sessionId) {notice('尚未连接工作区/会话。'); return;}
      const taskId = lastTaskId.current ?? goalRef.current?.taskId ?? null;
      if (!taskId) {notice('当前会话没有可合并的 task；先运行 /task 查看。'); return;}
      const parts = text.split(/\s+/);
      const base = ['--merge-task', taskId, '--session', state.sessionId, '--workspace', state.workspace];
      if (state.provider === 'fake') base.push('--fake');
      if (state.sessionPurpose === 'test') base.push('--test-session');
      if (parts[1] === 'approve') {
        const approvalId = parts[2] ?? mergeApprovalId.current;
        if (!approvalId) {notice('没有待批准的 merge approval id；先 /merge 请求。'); return;}
        const result = runVibe([...base, '--approve-merge', approvalId]);
        notice(result.ok ? result.text : `合并失败：${result.text}`);
        return;
      }
      const result = runVibe(base);
      const match = result.text.match(/merge approval requested: (\S+)/);
      if (match) mergeApprovalId.current = match[1];
      notice(result.ok ? result.text : `${result.text}${match ? `\n用 /merge approve ${match[1]} 批准并合并。` : ''}`);
      return;
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
      if (rejectBusyCommand()) return;
      try {
        const id = client.command(command, skillArgs);
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command});
        setInput(''); setFeedback(''); setScroll({follow: true, top: 0});
      } catch (error) {reportError(`操作未送达：${String(error)}`);}
      return;
    }
    const planModeCommand = /^\/plan\s+(on|off|status)$/.exec(text);
    if (planModeCommand) {
      const action = planModeCommand[1] === 'on' ? 'mode-on' : planModeCommand[1] === 'off' ? 'mode-off' : 'mode-status';
      if (commandContext.planModeControl) {
        if (modeRequest.current) {notice('默认模式请求确认中；草稿保留。'); return;}
        try {
          const id = client.command('plan', {action});
          const timer = setTimeout(() => {
            if (modeRequest.current?.id !== id) return;
            modeRequest.current = null;
            setFeedback('默认模式结果待确认；草稿保留，用 /plan status 查询'); setFeedbackTone('error');
          }, 15_000);
          modeRequest.current = {id, draft: input, timer};
          modeRequestIds.current.add(id);
          setFeedback('默认模式请求已发送，等待确认；当前任务模式不变'); setFeedbackTone('info');
        } catch (error) {notice(`默认模式请求未送达：${String(error)}；草稿保留。`);}
        return;
      }
      if (rejectBusyCommand()) return;
      try {
        const id = client.command('plan', {action});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setInput('');
        setFeedback('');
      } catch (error) {notice(`计划模式操作未送达：${String(error)}`);}
      return;
    }
    if (text === '/plan dismiss' || text === '/plan markdown') {
      if (rejectBusyCommand()) return;
      const action = text === '/plan dismiss' ? 'dismiss' : 'markdown';
      try {
        const id = client.command('plan', {action});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
        setInput('');
        setFeedback('');
      } catch (error) {notice(`计划操作未送达：${String(error)}`);}
      return;
    }
    const planRequestChanges = /^\/plan\s+request-changes(?:\s+([\s\S]+))?$/.exec(text);
    if (planRequestChanges) {
      const feedback = (planRequestChanges[1] ?? '').trim();
      if (!feedback) {notice('用法：/plan request-changes <反馈>；反馈不能为空。'); return;}
      if (rejectBusyCommand()) return;
      try {
        const id = client.command('plan', {action: 'request-changes', feedback});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
        setInput('');
        setFeedback('');
      } catch (error) {notice(`计划反馈未送达：${String(error)}`);}
      return;
    }
    if (text === '/plan' || text === '/plan all') {
      if (client.capabilities?.().includes('plan-review')) {
        if (review.discover()) {setInput(''); setFeedback('正在查询计划…');}
        return;
      }
      setInput('');
      const full = text === '/plan all';
      const rendered = latestPlanText(state.sessionPath, full);
      if (!rendered) {
        notice('当前会话没有计划产物；用 /plan <任务描述> 开始一次只读规划。');
        return;
      }
      setPlanLines(rendered.split('\n'));
      setPlanSelection(0);
      const artifact = latestPlanArtifact(state.sessionPath);
      setPlanStatus(String(artifact?.status ?? ''));
      setPlanVersion(Number(artifact?.version) || 0);
      const approval = artifact && typeof artifact.approval === 'object' ? artifact.approval as Record<string, unknown> : {};
      setPlanStale(approval.stale === true);
      setPlanOpen(true);
      setOverlayScroll({key: 'plan', top: 0});
      return;
    }
    if (text === '/plan card') {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再查看审批卡。'); return;}
      try {
        const id = client.command('plan', {action: 'card'});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
        setInput('');
      } catch (error) {notice(`审批卡查询未送达：${String(error)}`);}
      return;
    }
    if (text === '/plan list') {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再查看计划版本。'); return;}
      try {
        const id = client.command('plan', {action: 'list'});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
        setInput('');
      } catch (error) {notice(`计划版本查询未送达：${String(error)}`);}
      return;
    }
    if (text === '/plan acceptance') {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再查看验收标准。'); return;}
      try {
        const id = client.command('plan', {action: 'acceptance'});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
        setInput('');
      } catch (error) {notice(`验收标准查询未送达：${String(error)}`);}
      return;
    }
    const planDiff = /^\/plan\s+diff\s+(\d+)\s+(\d+)$/.exec(text);
    if (planDiff) {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再比较计划版本。'); return;}
      try {
        const id = client.command('plan', {action: 'diff', version_a: Number(planDiff[1]), version_b: Number(planDiff[2])});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
        setInput('');
      } catch (error) {notice(`计划差异未送达：${String(error)}`);}
      return;
    }
    const planDepends = /^\/plan\s+depends\s+(\S+)\s+(\S+)$/.exec(text);
    if (planDepends) {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再编辑步骤。'); return;}
      const dependsOn = planDepends[2]!.split(',').map(item => item.trim()).filter(Boolean);
      if (dependsOn.length > 20) {notice('用法：/plan depends <step_id> <s1,s2>（最多 20 个）'); return;}
      try {
        const id = client.command('plan', {action: 'edit-step', step_id: planDepends[1]!, depends_on: dependsOn});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
        setInput('');
      } catch (error) {notice(`步骤依赖编辑未送达：${String(error)}`);}
      return;
    }
    const planFiles = /^\/plan\s+files\s+(\S+)\s+(\S+)$/.exec(text);
    if (planFiles) {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再编辑步骤。'); return;}
      const files = planFiles[2]!.split(',').map(item => item.trim()).filter(Boolean);
      if (!files.length || files.length > 20) {notice('用法：/plan files <step_id> <path1,path2>（最多 20 个）'); return;}
      try {
        const id = client.command('plan', {action: 'edit-step', step_id: planFiles[1]!, files});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
        setInput('');
      } catch (error) {notice(`步骤文件编辑未送达：${String(error)}`);}
      return;
    }
    const planKind = /^\/plan\s+kind\s+(\S+)\s+(\S+)$/.exec(text);
    if (planKind) {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再编辑步骤。'); return;}
      if (!['read', 'edit', 'run', 'verify'].includes(planKind[2]!)) {
        notice('步骤类型必须是 read|edit|run|verify。');
        return;
      }
      try {
        const id = client.command('plan', {action: 'edit-step', step_id: planKind[1]!, kind: planKind[2] as 'read' | 'edit' | 'run' | 'verify'});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
        setInput('');
      } catch (error) {notice(`步骤类型编辑未送达：${String(error)}`);}
      return;
    }
    const planEditStep = /^\/plan\s+edit-step\s+(\S+)\s+([\s\S]+)$/.exec(text);
    if (planEditStep) {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再编辑步骤。'); return;}
      try {
        const id = client.command('plan', {action: 'edit-step', step_id: planEditStep[1]!, title: planEditStep[2]!.trim()});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
        setInput('');
      } catch (error) {notice(`步骤编辑未送达：${String(error)}`);}
      return;
    }
    const planStep = /^\/plan\s+step\s+(\S+)$/.exec(text);
    if (planStep) {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再查看步骤。'); return;}
      try {
        const id = client.command('plan', {action: 'step', step_id: planStep[1]!});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
        setInput('');
      } catch (error) {notice(`步骤查询未送达：${String(error)}`);}
      return;
    }
    const planRevise = /^\/plan\s+revise\s+([\s\S]+)$/.exec(text);
    if (planRevise) {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再修订计划。'); return;}
      try {
        const id = client.command('plan', {action: 'revise', objective: planRevise[1]!.trim()});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
        setInput('');
      } catch (error) {notice(`计划修订未送达：${String(error)}`);}
      return;
    }
    if (text === '/plan background') {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再准备后台计划。'); return;}
      if (!(client.capabilities?.().includes('tasks') ?? false)) {notice('后端未启用任务运行时。'); return;}
      try {
        const id = client.command('plan', {action: 'prepare-background'});
        activeRun.current = id;
        pendingBackgroundPlan.current = true;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setPlanOpen(false);
        setInput('');
      } catch (error) {notice(`后台计划未送达：${String(error)}`);}
      return;
    }
    if (text === '/plan execute') {
      const latest = latestPlanArtifact(state.sessionPath);
      if (!latest) {notice('当前会话没有计划产物；先 /plan <任务> 生成。'); return;}
      const executable = latestExecutablePlanArtifact(state.sessionPath);
      if (!executable) {
        const approval = latest.approval && typeof latest.approval === 'object' ? latest.approval as Record<string, unknown> : {};
        notice(approval.stale === true
          ? '计划已因纠偏/修改而 stale；先 /plan revise 并重新批准。'
          : '没有可执行的 approved 计划；先批准或修订计划。');
        return;
      }
      if (executable !== latest) notice(`最新版本不可执行；改用 approved v${String(executable.version ?? 1)}。`);
      const artifact = executable;
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再执行计划。'); return;}
      if (!(client.capabilities?.().includes('tasks') ?? false)) {notice('后端未启用任务运行时。'); return;}
      const planId = String(artifact.plan_id ?? '');
      const planVersion = Number(artifact.version ?? 1);
      const objective = String(artifact.objective ?? '执行计划');
      try {
        const id = client.run(objective, {kind: 'run', mode: 'act', objective, plan_id: planId, plan_version: planVersion});
        activeRun.current = id;
        dispatch({type: 'begin', id, text: objective});
        setInput('');
      } catch (error) {notice(`未能提交计划执行：${String(error)}`);}
      return;
    }
    const planReview = /^\/plan\s+(approve|reject)$/.exec(text);
    if (planReview) {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再处理计划。'); return;}
      try {
        const id = client.command('plan', {action: planReview[1] as 'approve' | 'reject'});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'plan'});
        setInput('');
      } catch (error) {reportError(`计划操作未送达：${String(error)}`);}
      return;
    }
    const planMatch = /^\/plan\s+([\s\S]+)$/.exec(text);
    if (planMatch) {
      const prompt = planMatch[1]!.trim();
      const operation = commandDefinition('/plan ' + prompt.split(/\s+/, 1)[0]);
      if (operation) {
        notice(`用法：${operation.command}${operation.usage ? ' ' + operation.usage : ''}；本次未执行，草稿保留。`);
        return;
      }
      if (state.phase !== 'idle' || activeRun.current || goalRef.current?.active) {
        notice('当前有运行中任务；结束后再开始新的计划。');
        return;
      }
      if (!(client.capabilities?.().includes('tasks') ?? false)) {
        notice('后端未启用任务运行时，暂不能使用 /plan。');
        return;
      }
      try {
        const id = client.run(expandExcerpts(prompt), {kind: 'run', mode: 'plan', objective: prompt});
        activeRun.current = id;
        dispatch({type: 'begin', id, text: prompt});
        setInput(''); setFeedback(''); setScroll({follow: true, top: 0});
      } catch (error) {notice(`未能提交计划任务：${String(error)}`);}
      return;
    }
    if (text === '/priority' || text.startsWith('/priority ')) {
      const parts = text.split(/\s+/);
      const requested = parts.length === 3 ? Number(parts[2]) : Number.NaN;
      if (parts.length !== 3 || !/^\d+$/.test(parts[2] ?? '') || !Number.isSafeInteger(requested) || requested < 0 || requested > 100) {
        notice('用法：/priority <task_id> <0-100>');
        return;
      }
      if (rejectBusyCommand()) return;
      try {
        const id = client.command('priority', {task_id: parts[1]!, priority: requested});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'priority'});
        setInput('');
        setFeedback(''); setScroll({follow: true, top: 0});
      } catch (error) {reportError(`优先级调整未送达：${String(error)}`);}
      return;
    }
    const traceMatch = /^\/trace(?:\s+(\S+))?$/.exec(text);
    if (traceMatch) {
      if (rejectBusyCommand()) return;
      if (state.phase === 'idle' && !activeRun.current) {
        const raw = traceMatch[1];
        const hours = raw === undefined ? undefined : Number(raw);
        if (raw !== undefined && !Number.isFinite(hours)) {
          notice('用法：/trace [最近小时数，0 < H ≤ 8760]');
          return;
        }
        try {
          const id = client.command('trace', raw === undefined ? undefined : {hours});
          activeRun.current = id;
          dispatch({type: 'begin_command', id, command: 'trace'});
          setInput(''); setFeedback(''); setScroll({follow: true, top: 0});
        } catch (error) {reportError(`操作未送达：${String(error)}`);}
      }
      return;
    }
    const notificationsMatch = /^\/notifications(?:\s+(\S+))?$/.exec(text);
    if (notificationsMatch) {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再查看通知。'); return;}
      try {
        const kind = notificationsMatch[1]?.trim();
        const id = client.command('notifications', kind ? {kind} : undefined);
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'notifications'});
        setInput('');
      } catch (error) {notice(`通知查询未送达：${String(error)}`);}
      return;
    }
    const exploreMatch = /^\/explore\s+([\s\S]+)$/.exec(text);
    if (exploreMatch) {
      if (state.phase !== 'idle' || activeRun.current) {notice('当前有运行中任务；结束后再探索。'); return;}
      try {
        const id = client.command('explore', {prompt: exploreMatch[1]!.trim()});
        activeRun.current = id;
        dispatch({type: 'begin_command', id, command: 'explore'});
        setInput('');
      } catch (error) {notice(`探索未送达：${String(error)}`);}
      return;
    }
    const modeMatch = /^\/(chat|act)\s+([\s\S]+)$/.exec(text);
    if (modeMatch) {
      const mode = modeMatch[1] as 'chat' | 'act';
      const prompt = modeMatch[2]!.trim();
      if (!prompt) {notice(`用法：/${mode} <任务描述>`); return;}
      if (state.phase !== 'idle' || activeRun.current || goalRef.current?.active) {
        notice('当前有运行中任务；结束后再切换模式。');
        return;
      }
      if (!(client.capabilities?.().includes('tasks') ?? false)) {
        notice('后端未启用任务运行时，暂不能使用 /chat 或 /act。');
        return;
      }
      try {
        const id = client.run(expandExcerpts(prompt), {kind: 'run', mode, objective: prompt});
        activeRun.current = id;
        dispatch({type: 'begin', id, text: prompt});
        setInput(''); setFeedback(''); setScroll({follow: true, top: 0});
      } catch (error) {notice(`未能提交任务：${String(error)}`);}
      return;
    }
    if (text.startsWith('/') && followupText === null) {setInput(''); notice(`未知命令 ${text.split(/\s/)[0]}；输入 /help 查看可用操作。`); return;}
    if (state.phase === 'running' && activeRun.current) {
      if (state.activeCommand && state.activeCommand !== 'resume') {
        notice('命令执行中，结束后再发送');
        return;
      }
      try {
        const id = client.steer(activeRun.current, expandExcerpts(text));
        controlRequests.current.set(id, 'steer');
        setInput('');
        notice('纠偏已发送，等待后端确认');
      } catch (error) {reportError(`纠偏未送达：${String(error)}`);}
      return;
    }
    if (rejectBusyCommand()) return;
    if (goalRef.current?.active) {notice('目标模式进行中；先 /goal stop 再开始新任务。'); return;}
    const task = followupText ?? text;
    try {
      const id = client.run(expandExcerpts(task));
      activeRun.current = id;
      dispatch({type: 'begin', id, text: task});
      setInput(''); setFeedback(''); setScroll({follow: true, top: 0});
    } catch (error) {notice(`未能提交任务：${String(error)}`);}
  };

  const status = agentStatus(state);
  const phase = quitting ? '正在退出，等待后端清理' : status.label;
  const position = `${Math.min(top + 1, lines.length)}–${Math.min(top + bodyRows, lines.length)}/${lines.length}`;
  const doneText = state.lastVerified === false ? '完成待验证' : state.lastVerified === true ? '已完成 · 已验证' : '任务完成';
  const goalTitle = goal?.active ? `目标模式 · ${goalLabel(goal)} · ` : '';
  const runModeLabel = state.runMode === 'act' ? '执行' : state.runMode === 'chat' ? '仅对话' : state.runMode === 'plan' ? '只读规划' : state.runMode === 'goal' ? '目标执行' : null;
  const modePending = state.planMode.pending && state.planMode.wanted !== null && state.planMode.wanted !== state.planMode.active;
  const nextPlan = modePending ? state.planMode.wanted : state.planMode.active;
  const compactModeLabel = runModeLabel?.replace('仅对话', '对话').replace('只读规划', '规划').replace('目标执行', '目标');
  const nextModeTitle = columns < 60 ? `下次${nextPlan ? '规划' : '执行'}${modePending ? '待生效' : ''}` : `下次：${nextPlan ? '只读规划' : '执行'}${modePending ? '（待生效）' : ''}`;
  const planModeTitle = state.phase === 'closed' ? '' :
    columns < 60 && state.activeId && compactModeLabel && modePending ? `${compactModeLabel}中 · 结束后${nextPlan ? '规划' : '执行'} · ` :
      (state.activeId && runModeLabel ? `${columns < 60 ? '本次' + compactModeLabel : '本次：' + runModeLabel} · ` : !state.activeId && state.planModeKnown && state.planMode.active ? '计划模式 · ' : '') +
      (state.planModeKnown ? nextModeTitle + ' · ' : '');
  const restoredIdle = state.resumed && !state.hasStartedRun && state.phase === 'idle';
  const paneTitle = planListOpen ? `计划执行 · v${planProgress?.plan.version ?? '?'} · ${planProgress?.done ?? 0}/${planProgress?.total ?? 0} 步 · ${position}` : historyVisible ? `历史结果 · 第 ${history.previous.length + 1} 页 · ${position}` : state.approval ? `等待审批 · ${approvalFocused ? '审批焦点' : '草稿焦点'} · ${position}` : revision.mode === 'history' ? `修订记录 · 第 ${(revision.history?.previous.length ?? 0) + 1} 页 · ${revision.history?.records.length ? revision.history.index + 1 : 0}/${revision.history?.records.length ?? 0}` : revision.mode ? `修订 v${revision.record?.target.plan_version} · ${revision.mode === 'confirm' ? '确认反馈' : '修订状态'} · ${position}` : review.focused ? `计划 v${review.card?.target?.plan_version} · ${review.body ? `正文 ${review.pageNumber}${review.lastPage ? '末页' : '页'} · ${position}` : '审阅焦点'}` : planModeTitle + goalTitle + (contextOpen ? `上下文分区 · ${position}` : planOpen ? `计划 · ${position}` : detailEntry ? `工具详情 · ${position}` : isEmpty ? '工作台首页 · 输入任务开始' : `对话 · ${reading ? '阅读焦点 · ' : ''}${scroll.follow ? '跟随最新' : `回看 · 下方 ${maximum-top} 行`} · ${position}`);
  const goalHint = goal ? `${goalStatusText(goal)} · ` : '';
  const planModeHint = state.planMode.active && state.phase === 'idle' ? (columns < 60 ? '/plan off 退出计划 · ' : '下次普通输入只读规划 · /plan off 退出 · ') : '';
  const hint = planListOpen ? '↑↓滚动 PgUp/PgDn翻页 Home/End首尾 Esc返回' : historyVisible ? 'Tab选结果 Enter验证 ←→翻页 Esc返回' : state.approval ? (approvalFocused ? 'Enter确认 Esc草稿 Ctrl+C停止' : !input.trim() ? 'y允许 n拒绝 Tab更多 Ctrl+C' : 'Tab选按钮 · Ctrl+C停止') : revision.mode === 'history' ? '↑↓记录 PgUp/Dn翻页 Tab动作 Esc返回' : revision.mode ? (revision.mode === 'edit' ? 'Enter下一步 Ctrl+J换行 Esc返回' : '点击/Tab选择 Enter确认 Esc返回') : review.focused ? (review.body ? (columns < 60 ? '←前页 →后页 a动作 Esc草稿' : '←前页 →后页 · ↑↓滚动 · Ctrl+Y复制本页 · a动作 Esc草稿') : columns < 60 ? '↑↓选择 Enter确认 Esc草稿' : '↑↓选择 Enter确认 Esc草稿 · ←→切换计划') : review.card ? 'Ctrl+R审阅 · Ctrl+C停止 · Ctrl+Q退出' : planModeHint + goalHint + (contextOpen ? '←/→ 选快照 · l 最新 · ↑↓/滚轮 翻阅 · Esc 返回' : planOpen ? '↑↓ 选择 · s步骤 · t改标题 · k改类型 · c审批卡 · v版本 · d比较 · a验收 · e修订 · r提反馈 · m审阅稿 · b后台 · y批准 · n拒绝 · Enter执行 · PgUp/PgDn · Esc 返回' : detailEntry ? '滚轮/↑↓ 翻阅 · Home/End 首尾 · Esc 返回' :
    reading ? '阅读焦点：↑↓滚动  空格翻页  End最新  Esc输入  Ctrl+Q退出' :
      state.phase === 'running' ? (columns < 60 ? 'Enter 纠偏 · /followup 排队 · Ctrl+C 停止' : 'Enter 提交纠偏  /followup 文本 排队后续  Ctrl+C 停止  Ctrl+Q 退出') :
        state.lastStatus === 'done' ? (columns < 60 ? `${doneText} · /evidence 核对 · /resume 续做` : `${doneText}  /evidence 证据  /resume 续做  /copy 复制回复  Ctrl+Q 退出`) :
        state.lastStatus === 'cancelled' ? (columns < 60 ? '已取消 · 核对修改 · /resume 续做' : '已取消  核对已发生的修改  /resume 续做  /copy 复制回复  Ctrl+Q 退出') :
        state.lastStatus === 'error' ? (columns < 60 ? '运行失败 · /resume 重试' : '运行失败  查看错误与工具记录  /resume 重试  Ctrl+Q 退出') :
        state.lastStatus === 'stopped' ? (state.lastStopReason?.startsWith('acceptance_')
          ? (columns < 60 ? '验收未通过 · /plan acceptance' : '验收未通过  /plan acceptance 核对  /plan revise 修订  Ctrl+Q 退出')
          : state.lastStopReason === 'plan_incomplete'
            ? (columns < 60 ? '计划未完成 · /plan' : '计划未完成  /plan 查看步骤  /resume 续做  Ctrl+Q 退出')
            : (columns < 60 ? '任务已暂停 · /resume 续做' : '任务已暂停  /resume 续做  /context 查看进度  Ctrl+Q 退出')) :
        isEmpty ? (columns < 60 ? '输入任务开始 · Ctrl+Q退出 · /help' : '直接描述任务开始  /help 命令  Ctrl+K 上下文  Ctrl+Q 退出') :
        columns < 60 ? 'Ctrl+K上下文 · Ctrl+Q退出 · /help' : '拖选复制  Ctrl+R阅读  Ctrl+K上下文  Ctrl+Y复制回复  Ctrl+Q退出');
  const lastNotice = state.notices.at(-1);
  const noticeFirstLine = lastNotice ? (lastNotice.split('\n')[0] ?? '').trim() : '';
  const recordHint = noticeFirstLine ? `记录${state.notices.length > 1 ? `(${state.notices.length})` : ''}：${noticeFirstLine}${columns >= 48 ? ' · /logs' : ''}` : '';
  const statusBase = `${phase}${resultReason(state.lastResult) ? ` · ${resultReason(state.lastResult)}` : ''}${metricsText(state) ? `  ·  ${metricsText(state)}` : ''}`;
  const statusLineText = feedback || statusBase;
  const composerDisabled = Boolean(submissionBlock);
  const composerPlaceholder = planListOpen ? '计划执行清单已聚焦 · Esc 返回输入' : state.approval ? (approvalFocused ? '审批已聚焦 · Enter 确认 · Esc 草稿' : '草稿可编辑 · 点击按钮或 Tab 选择') : revision.mode ? '普通草稿已保留 · 返回后继续编辑' : review.focused ? '计划审阅已聚焦 · Esc 返回草稿' : contextOpen ? '上下文详情已聚焦，Esc 返回输入' : detailEntry ? '工具详情已聚焦，Esc 返回输入' : reading ? '正在阅读历史，Esc 返回输入' :
    state.phase === 'closed' ? '可以回看已有记录，退出后重新启动' : state.phase === 'starting' ? '可以先写下任务目标与约束' : state.phase === 'stopping' ? '正在停止 · 收到停止结果后可发送' :
      state.phase === 'running' ? '运行中：Enter 提交纠偏，/followup 文本排队后续' :
        state.lastStatus === 'done' ? `${doneText} · 输入新任务，或 /resume 续做` :
        state.lastStatus === 'cancelled' ? '已取消 · 核对修改后输入新任务，或 /resume 续做' :
        state.lastStatus === 'error' ? '运行失败 · 输入新任务，或 /resume 重试' :
        state.lastStatus === 'stopped' ? '任务已暂停 · 输入新任务，或 /resume 续做' : '描述任务，或输入 / 查看命令';
  const effectivePlaceholder = state.approval ? composerPlaceholder : goal?.active ? `目标模式 · ${goalLabel(goal)} · 输入纠偏，/goal stop` : restoredIdle ? (columns < 60 ? '已恢复，尚未运行' : '会话已恢复，尚未运行 · 请重新输入未发送的草稿') : state.planMode.active && state.phase === 'idle' ? '计划模式：普通输入以只读规划运行 · /plan off 退出' : composerPlaceholder;
  const unavailableHint = state.phase === 'closed' ? '连接已断开 · Ctrl+Q 退出后重启' : state.phase === 'starting' ? '可编辑草稿 · 后端就绪后可发送' :
    state.phase === 'stopping' ? '正在停止 · 收到停止结果后可发送' : undefined;
  // PM/UX: 完成/错误/暂停/取消后给出下一步引导，减少用户困惑。

  if (terminalTooSmall && quitPrompt && !taskConnection.connection) return <Box width={columns} height={rows} backgroundColor={palette.background} flexDirection="column" overflow="hidden">
    <Text color={palette.warning}>{fitText('↑↓选择 Enter确认 Esc返回', columns)}</Text>
    {quitPrompt.options.slice(Math.max(0, quitPrompt.index - rows + 2), Math.max(0, quitPrompt.index - rows + 2) + Math.max(1, rows - 1)).map(option =>
      <Text key={option.label} color={option.disabled ? palette.secondary : palette.foreground}>{fitText(`${option === quitPrompt.options[quitPrompt.index] ? '› ' : '  '}${option.label}`, columns)}</Text>)}
  </Box>;
  if (terminalTooSmall && !taskConnection.connection) return <Box width={columns} height={rows} backgroundColor={palette.background} flexDirection="column" overflow="hidden">
    <Text color={palette.foreground} bold>{fitText('mini-harness', columns)}</Text>
    {rows > 1 ? <Text color={palette.foreground}>{fitText(`至少 ${minimumColumns}×${minimumRows} · 请调大窗口`, columns)}</Text> : null}
    {rows > 2 ? <Text color={palette.secondary}>{fitText(state.approval ? 'Ctrl+C 停止' : 'Ctrl+Q 退出', columns)}</Text> : null}
    {rows > 3 && state.approval ? <Text color={palette.secondary}>{fitText('Ctrl+Q 退出', columns)}</Text> : null}
  </Box>;

  return <><Box display={surfaceActive ? 'flex' : 'none'} flexDirection="column"><Box display={inspectionVisible || followupsVisible || navigationVisible || sessionNavVisible || permissionPanelVisible || secondaryPanelVisible ? 'none' : 'flex'} flexDirection="column" width={columns} height={rows} backgroundColor={palette.background} overflow="hidden">
    <AgentHeader state={state} columns={columns} height={headerRows} palette={palette}/>
    {compactRows ? <StatusPanel state={state} columns={columns} height={compactRows} palette={palette}/> : null}
    <Box display={compactQuit ? 'none' : 'flex'} flexDirection="row" width={columns} height={paneRows} flexShrink={0}>
      {revision.mode === 'edit' && revision.record && !state.approval ? <Box width={readerColumns} height={paneRows} paddingX={2} paddingY={1} flexShrink={0} flexDirection="column">
        <Text color={palette.accent} bold>{fitText(`修订反馈 · v${revision.record.target.plan_version} · ${[...revision.record.draft.trim()].length}/4000 字`, readerColumns - 4)}</Text>
        <Text color={palette.secondary}>{fitText(revision.record.objective.replace(/\s+/g, ' '), readerColumns - 4)}</Text>
        <Composer value={revision.record.draft} onChange={revision.change} onSubmit={revision.preview} history={[]}
          focused={surfaceActive && !inspectionVisible && !followupsVisible && !navigationVisible && !sessionNavVisible && !permissionPanelVisible && !secondaryPanelVisible && !revision.selected && !quitting} placeholder="写下希望修改的地方" columns={bodyColumns} maxRows={Math.max(1, paneRows - 4)}
          onTab={backwards => revision.move(backwards ? -1 : 1)} accentColor={palette.accent} foregroundColor={palette.foreground} secondaryColor={palette.secondary}/>
      </Box> : <ReadingPane title={paneTitle} lines={paneLines} top={top} height={paneRows} columns={readerColumns} palette={palette} overlay={readerFocused} selection={selection}/>}
      {sidebar ? <><Box width={1}/><StatusPanel state={state} columns={32} height={paneRows} palette={palette}/></> : null}
    </Box>
    {!compactQuit && state.approval ? <ApprovalActions buttons={approvalChoices} selected={approvalFocused ? approvalSelection?.decision ?? null : null}
      columns={columns} height={approvalRows} palette={palette} targets={approvalTargets}/> : null}
    {!compactQuit && revisionRows ? <ActionButtons buttons={revision.buttons} selected={revision.selected} columns={columns} height={revisionRows} palette={palette} targets={revisionTargets}/> : null}
    {!compactQuit && inspectionRows ? <ActionButtons buttons={inspectionButtons} selected={null}
      columns={columns} height={inspectionRows} palette={palette} targets={inspectionTargets}/> : null}
    {!compactQuit && followupRows ? <Box height={1} width={columns} flexShrink={0}>
      <ActionButtons buttons={followupButtons} selected={null}
        columns={followupButtonsWidth} height={1} palette={palette} targets={followupTargets}/>
      <Text color={followups.paused || followups.problem ? palette.warning : palette.secondary}>{fitText(followups.problem || followups.paused || (followups.count ? '成功后按序继续' : onDetach ? '原草稿保留' : '查看本会话任务'), Math.max(1, columns - followupButtonsWidth))}</Text>
    </Box> : null}
    {!compactQuit && planProgressVisible && planProgressView ? <PlanProgressPanel rows={planProgressView.rows} lines={planProgressView.lines} palette={palette}/> : null}
    {quitPrompt ? <Box flexDirection="column" backgroundColor={palette.surfaces.warning} height={Math.max(composerRows + 2, quitPrompt.options.length + 3)} width={columns} paddingX={2} paddingY={1} flexShrink={0}>
      <Text color={palette.foreground} bold>退出选项（↑↓ 选择 · Enter 确认 · Esc 取消）</Text>
      {quitPrompt.options.map((option, index) => <Text key={option.label} color={option.disabled ? palette.secondary : index === quitPrompt.index ? palette.warning : palette.foreground}>
        {fitText(`${index === quitPrompt.index ? '› ' : '  '}${index + 1}. ${option.label}`, compactQuit ? columns - 4 : columns)}
      </Text>)}
    </Box> : null}
    {planBarShown && review.card ? <PlanReviewBar card={review.card} steps={planBarSteps} selected={planBarSelected} columns={columns}
      rows={planBarRows} compact={rows < 18} draft={input} palette={palette}/> : <Box display={quitPrompt ? 'none' : 'flex'} backgroundColor={state.approval ? palette.surfaces.warning : readerFocused ? palette.surface : palette.focus} height={composerHeight} width={columns} paddingX={2} paddingY={composerPadding} flexShrink={0}>
      <Composer value={input} initialCursor={initialDraft?.cursor} onDraftChange={onDraftChange} appendRevision={draftAppendRevision} onChange={text => {setMenuIndex(0); setInput(text);}} onSubmit={submit} onSubmitBlocked={() => {
          const reason = submissionBlock ?? '当前不可发送';
          setFeedback(`未发送：${reason}，草稿保留`);
          if (state.phase === 'starting') {
            startupBlocked.current = true;
            dispatch({type: 'notice', text: '后端仍在启动；草稿已保留，显示就绪后再按 Enter 发送'});
          }
        }} history={inputHistory} disabled={composerDisabled} disabledHint={unavailableHint} focused={surfaceActive && !quitPrompt && !inspectionVisible && !followupsVisible && !navigationVisible && !sessionNavVisible && !permissionPanelVisible && !secondaryPanelVisible && !readerFocused && !quitting} placeholder={effectivePlaceholder} columns={Math.max(1, columns - 4)} maxRows={composerRows} accentColor={palette.accent} warningColor={palette.warning} foregroundColor={palette.foreground} secondaryColor={palette.secondary}
        onTab={state.approval ? backwards => {const button = backwards ? approvalChoices.at(-1) : approvalChoices[0]; if (button) selectApproval(button.action);} : undefined}
        menu={menuRows ? {
          count: menuMatches.length,
          index: menuPosition,
          unavailable: menuUnavailable[menuMatches[menuPosition] ?? ''],
          onMove: delta => setMenuIndex(position => (position + delta + menuMatches.length) % menuMatches.length),
          onAccept: execute => {
            const next = menuMatches[menuPosition]; setMenuIndex(0);
            if (execute) {submit(next); return;}
            const expand = commandDefinition(next)?.required || commandMatches(next + ' ').length > 0;
            setInput(next + (expand ? ' ' : ''));
          },
        } : undefined}/>
    </Box>}
    {menuRows ? <CommandMenu matches={menuMatches} index={menuPosition} rows={menuRows} columns={columns} accentColor={palette.accent} foregroundColor={palette.foreground} secondaryColor={palette.secondary} surfaceColor={palette.surface} descriptions={skillMenu?.descriptions} unavailable={menuUnavailable}/> : null}
    {inspectionInline ? <Box height={1} width={columns}>
      <ActionButtons buttons={inspectionButtons} selected={null} columns={columns < 48 ? 10 : 14} height={1} palette={palette} targets={inspectionTargets}/>
      <Text color={feedback && feedbackTone === 'error' ? palette.error : palette.foreground}>{fitText(statusLineText, Math.max(1, columns - (columns < 48 ? 10 : 14)))}</Text>
    </Box> : <Text color={state.error || state.phase === 'closed' || (feedback && feedbackTone === 'error') ? palette.error : state.approval ? palette.warning : palette.foreground}>{fitText(statusLineText, columns)}</Text>}
    <Text color={palette.secondary}>{fitText((recordHint && !state.approval && !review.focused && !review.card && !revision.mode && !contextOpen && !planOpen && !planListOpen && !detailEntry && !historyVisible ? recordHint : '') || (onDetach && !readerFocused ? '任务草稿本窗口保留 · 返回原会话' : restoredIdle && columns < 60 && !readerFocused ? '未发送草稿需重输 · /resume 续做' : hint), columns)}</Text>
  </Box>
    {navigation.open ? <Box display={navigationVisible ? 'flex' : 'none'}><TaskNavigationPanel navigation={navigation} columns={columns} rows={rows} palette={palette}
      active={navigationVisible} onConnect={!attach ? connectSelectedTask : undefined} connecting={taskConnection.pending || handoff?.mode === 'wait'} onCancel={cancelHandoff}
      handoff={handoff?.mode === 'choose'} onHandoff={prepareHandoff}
      message={taskConnection.problem}
      onReturn={() => {if (taskConnection.pending) taskConnection.cancel(); navigation.close();}}
      status={handoff ? handoff.mode === 'choose' ? '当前仍有运行，请选择接入方式' : '等待当前运行收尾，待办已暂停' : taskConnection.pending ? '正在确认所选任务连接…' : taskConnection.problem || (state.approval ? '待审批 · 返回主区处理' : state.activeId ? '当前任务仍运行 · Ctrl+C 停止' : '查看不执行 · Esc 返回草稿')}
      onInspect={taskId => {if (taskConnection.pending) taskConnection.cancel(); navigation.close(); history.show(taskId); setOverlayScroll({key: 'inspection-history', top: 0});}}/></Box> : null}
    {sessionNav.open ? <Box display={sessionNavVisible ? 'flex' : 'none'}><SessionNavigationPanel navigation={sessionNav} columns={columns} rows={rows} palette={palette}
      active={sessionNavVisible} opening={sessionNav.opening}
      onOpen={!attach && onSessionSwitch ? openOldSession : undefined}
      onCancelOpen={cancelSessionOpen}
      message={sessionNav.problem}
      onReturn={() => {if (sessionNav.opening) cancelSessionOpen(); sessionNav.close();}}
      status={state.approval ? '待审批 · 返回主区处理' : state.activeId ? '当前任务仍运行 · 结束后再打开' : attach ? '任务接入中 · 先返回原会话' : '查看不执行 · Esc 返回草稿'}/></Box> : null}
    {permissionPanel.open ? <Box display={permissionPanelVisible ? 'flex' : 'none'}><PermissionPanel panel={permissionPanel} columns={columns} rows={rows} palette={palette}
      active={permissionPanelVisible}
      status={state.approval ? '待审批 · 返回主区处理' : state.activeId ? '当前任务仍运行 · 撤销立即影响后续工具调用' : '查看不改变审批规则 · 撤销立即生效'}
      onReturn={() => permissionPanel.close()}/></Box> : null}
    {locator ? <Box display={locatorVisible ? 'flex' : 'none'}><LocatorPanel title={locator.title} items={locator.items} columns={columns} rows={rows} palette={palette}
      active={locatorVisible} status={locator.kind === 'find' ? '搜索只读 · 定位后返回阅读' : locator.kind === 'rounds' ? '选择轮次定位旧结论' : '选择错误定位失败现场'}
      onJump={line => {
        if (line < 0) {notice('该轮不在当前正文（重开前的历史）；可用 /sessions 打开原会话核验。'); setLocator(null); return;}
        notice(`已定位到第 ${line + 1} 行；Ctrl+End 回到最新。`);
        setHighlightLine(line);
        setScroll({follow: false, top: Math.max(0, line - 2)});
        setLocator(null);
      }}
      onReturn={() => setLocator(null)}/></Box> : null}
    {searchPanel ? <Box display={searchPanelVisible ? 'flex' : 'none'}><HistorySearchPanel query={searchPanel.query} matches={searchPanel.matches} columns={columns} rows={rows} palette={palette}
      active={searchPanelVisible} loading={searchPanel.loading} problem={searchPanel.problem} next={searchPanel.next}
      truncated={searchPanel.truncated} skipped={searchPanel.skipped}
      onOpen={openSearchMatch}
      onMore={() => requestHistorySearch(searchPanel.query, searchPanel.next ?? undefined, true)}
      onReturn={() => setSearchPanel(null)}/></Box> : null}
    {logsOpen ? <Box display={logsVisible ? 'flex' : 'none'}><WorkbenchLogPanel notices={state.notices} columns={columns} rows={rows} palette={palette}
      active={logsVisible} onReturn={() => setLogsOpen(false)}/></Box> : null}
    {followups.open ? <Box display={followupsVisible ? 'flex' : 'none'}><FollowupPanel queue={followups} columns={columns} rows={rows} palette={palette} active={followupsVisible}
      blocked={submissionBlock || (state.attachOffline ? '离线只读，不能继续执行' : goal?.active ? '目标模式进行中' : undefined)}
      status={state.approval ? '待审批 · 返回主区处理' : state.activeId ? '当前任务仍运行 · Ctrl+C 停止' :
        followups.inflight ? '请求已记录 · 请先核对历史结果' : '待办尚未发送 · Esc 返回草稿'}
      onInspect={() => {followups.close(); history.show();}}
      onDirtyChange={dirty => {followupDraftDirty.current = dirty;}}
      onResume={() => {if (followups.resume()) startQueuedFollowup();}}/></Box> : null}
    {inspectionMounted && state.workspace ? <Box display={inspectionVisible ? 'flex' : 'none'}>
      <SidebarApp workspace={state.workspace} owner={sidebarOwner} theme={theme} size={{columns, rows}}
        active={inspectionVisible} copyText={copyText} readPlanPage={(target, cursor) => sidebarRequestsRef.current.readPage(target, cursor)} embedded={{
          status: state.approval ? '待审批 · 返回处理' : state.attachOffline ? '离线只读' : state.phase === 'running' ? '任务运行中 · Ctrl+C 停止' : 'Esc 返回 · 草稿已保留',
          onReturn: () => setInspectionFocused(false),
          onCompanion: () => {openSidebarPanel(); setInspectionFocused(false);},
        }}/>
    </Box> : null}
  </Box>{taskConnection.connection ? <App key={taskConnection.connection.target.taskId} client={taskConnection.connection.client} attach active={active}
    initialDraft={taskConnection.draft} onDraftChange={taskConnection.saveDraft} onDetach={taskConnection.returned} stopOnConnect={taskConnection.connection.stop}
    windowSize={{columns, rows}} initialTheme={theme} copyText={copyText} profilePath={profilePath} userProfilePath={userProfilePath}
    launchSidebar={launchSidebar} setupSidebar={setupSidebar} publishSidebarSignal={publishSidebarSignal} autoObserve={false}/> : null}</>;
}
