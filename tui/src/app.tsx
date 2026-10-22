import React, {useEffect, useMemo, useReducer, useRef, useState} from 'react';
import {Box, Text, useApp, useInput, usePaste, useWindowSize} from 'ink';
import type {BackendClient} from './backend.js';
import type {ApprovalDecision, BackendCommand, SkillsCommandArgs} from './protocol.js';
import {initialState as emptyState, reduceView, type ViewState} from './view-state.js';
import {Composer} from './composer.js';
import {fitText, textLines} from './markdown.js';
import {detailLines, errorLines, liveLines, metricsText, ReadingPane, transcriptLines, welcomeLines} from './workbench.js';
import {AgentHeader, agentStatus, StatusPanel} from './agent-status.js';
import {getPalette, type ThemeName} from './theme.js';
import {terminalColorLevel} from './terminal-colors.js';
import {useTranscriptMouse} from './use-transcript-scroll.js';
import {contextLines} from './context-view.js';

type Scroll = {follow: boolean; top: number};
const backendCommands: Record<string, BackendCommand> = {
  '/resume': 'resume', '/continue': 'resume', '/permissions': 'permissions', '/permissions reset': 'permissions_reset',
  '/cost': 'cost', '/costs': 'cost', '/tools': 'tools', '/diff': 'diff', '/mcp': 'mcp',
};
const approvalKeys: Record<string, ApprovalDecision> = {y: 'once', s: 'session', p: 'workspace', n: 'deny'};

export function App({client, initialState = emptyState, windowSize, initialTheme = 'system'}: {
  client: BackendClient; initialState?: ViewState; windowSize?: {columns: number; rows: number}; initialTheme?: ThemeName;
}) {
  const [state, dispatch] = useReducer(reduceView, initialState);
  const [input, setInput] = useState('');
  const [history, setHistory] = useState<string[]>(() => initialState.entries.filter(entry => entry.kind === 'task').map(entry => entry.text));
  const [theme, setTheme] = useState<ThemeName>(initialTheme);
  const [quitting, setQuitting] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [scroll, setScroll] = useState<Scroll>({follow: true, top: 0});
  const [selectedTool, setSelectedTool] = useState<number | null>(null);
  const [detail, setDetail] = useState<number | null>(null);
  const [contextOpen, setContextOpen] = useState(false);
  const [contextSelection, setContextSelection] = useState<string | null>(null);
  const [overlayScroll, setOverlayScroll] = useState({key: '', top: 0});
  const [reading, setReading] = useState(false);
  const closing = useRef(false);
  const activeRun = useRef(state.activeId);
  const answeredApproval = useRef<string | null>(null);
  activeRun.current = state.activeId;
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
  const headerRows = rows >= 18 ? 3 : 2;
  const sidebar = columns >= 118 && rows >= 24;
  const readerColumns = sidebar ? columns - 33 : columns;
  const compactRows = !sidebar && rows >= 24 && !overlayKey ? 3 : 0;
  const paneRows = Math.max(4, rows - headerRows - compactRows - composerRows - 4);
  const bodyRows = Math.max(1, paneRows - 3);
  const bodyColumns = Math.max(1, readerColumns - 5);
  const decisions = state.approval?.decisions ?? ['once', 'deny'];
  const fullApprovalHint = [decisions.includes('once') ? 'y 允许一次' : '', decisions.includes('session') ? 's 精确规则' : '',
    decisions.includes('workspace') ? 'p 工作区' : '', decisions.includes('deny') ? 'n 拒绝' : ''].filter(Boolean).join(' / ');
  const approvalHint = fitText(fullApprovalHint, columns) === fullApprovalHint ? fullApprovalHint :
    [decisions.includes('once') ? 'y一次' : '', decisions.includes('session') ? 's规则' : '',
      decisions.includes('workspace') ? 'p工作区' : '', decisions.includes('deny') ? 'n拒绝' : ''].filter(Boolean).join(' ');
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
    return isEmpty ? welcomeLines(bodyColumns) : transcript;
  }, [state.approval, contextOpen, state.contextSnapshots, contextSelection, detailEntry, bodyColumns, isEmpty, transcript]);
  const maximum = Math.max(0, lines.length - bodyRows);
  const top = overlayKey ? Math.min(overlayScroll.key === overlayKey ? overlayScroll.top : 0, maximum) :
    isEmpty ? Math.min(scroll.top, maximum) : scroll.follow ? maximum : Math.min(scroll.top, maximum);
  const viewport = useRef({top, maximum, overlayKey});
  viewport.current = {top, maximum, overlayKey};
  const readerFocused = reading || Boolean(overlayKey);

  useEffect(() => client.subscribe(event => {
    if (event.type === 'approval_request') {setDetail(null); setContextOpen(false); answeredApproval.current = null;}
    if (event.type === 'history_message' && event.message.role === 'user') setHistory(previous => [...previous, event.message.content]);
    if (event.type === 'status' && event.phase === 'starting' && event.id) activeRun.current = event.id;
    if ((event.type === 'done' || event.type === 'command_result') && event.id === activeRun.current) activeRun.current = null;
    if (event.type === 'status' && event.phase === 'idle' && activeRun.current === 'startup') activeRun.current = null;
    if (event.type === 'closed') activeRun.current = null;
    dispatch({type: 'event', event});
  }), [client]);
  usePaste(() => {}, {isActive: readerFocused});

  const notice = (text: string) => {dispatch({type: 'notice', text}); setScroll({follow: true, top: 0});};
  const reportError = (text: string) => {notice(text); setFeedback(text);};
  const shutdown = () => {
    if (closing.current) return;
    closing.current = true;
    setQuitting(true);
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
  useTranscriptMouse({onScroll: moveReading, top: headerRows + compactRows + 1, bottom: headerRows + compactRows + paneRows,
    left: 1, right: readerColumns, enabled: !quitting && rows >= 11 && columns >= 24});
  const openTool = () => {if (chosenTool) {setDetail(chosenTool.id); setOverlayScroll({key: `tool-${chosenTool.id}`, top: 0});}};
  const openContext = () => {setDetail(null); setContextSelection(null); setContextOpen(true); setOverlayScroll({key: 'context', top: 0});};

  useInput((keyInput, key) => {
    if (key.eventType === 'release') return;
    if (key.ctrl && keyInput === 'q') {shutdown(); return;}
    if (key.ctrl && keyInput === 'c') {
      if (activeRun.current && state.phase !== 'stopping') {
        try {client.cancel(activeRun.current); dispatch({type: 'cancel'});}
        catch (error) {reportError(`取消请求未送达：${String(error)}`);}
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
    if (text === '/quit' || text === '/exit') {setInput(''); shutdown(); return;}
    if (text === '/context') {setInput(''); openContext(); return;}
    if (text === '/help') {
      setInput('');
      notice('Enter 发送；Ctrl+J 换行；输入焦点下 ↑↓ 编辑或浏览输入历史。滚轮 / 触控板在对话区回看；PgUp/PgDn 翻页，Ctrl+P/N 或 Ctrl+↑/↓ 逐行。Ctrl+R 切换阅读 / 输入焦点，阅读时 ↑↓ 滚动、空格下一页，Esc 回输入。Ctrl+Home/End 到首尾，End 恢复跟随最新；终端支持时按住 Shift 拖选文字。Tab 选择工具，Ctrl+O 查看全文。Ctrl+K 或 /context 查看上下文分区，←/→ 回放本轮快照，l 跟随最新，Esc 返回。Ctrl+C 取消当前操作，空闲时退出；Ctrl+Q 退出。审批时 y 单次、s 精确规则、p 工作区、n 拒绝，以面板选项为准。\n/resume 显式续做已保存任务；/permissions 查看授权，/permissions reset 撤销；/cost 会话花费；/tools 可用工具；/diff 工作区差异；/mcp 服务状态；/skills 技能目录，refresh 重扫，activate NAME 激活，unload NAME 卸载；/session 会话路径；/theme system|light|dark 主题。');
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
    if (text.startsWith('/')) {setInput(''); notice(`未知命令 ${text.split(/\s/)[0]}；输入 /help 查看可用操作。`); return;}
    if (state.phase !== 'idle' || activeRun.current) return;
    try {
      const id = client.run(text);
      activeRun.current = id;
      dispatch({type: 'begin', id, text});
      setInput(''); setFeedback(''); setHistory(previous => [...previous, text]); setScroll({follow: true, top: 0});
    } catch (error) {notice(`未能提交任务：${String(error)}`);}
  };

  const status = agentStatus(state);
  const phase = quitting ? '正在退出，等待后端清理' : status.label;
  const position = `${Math.min(top + 1, lines.length)}–${Math.min(top + bodyRows, lines.length)}/${lines.length}`;
  const paneTitle = state.approval ? `等待审批 · ${position}` : contextOpen ? `上下文分区 · ${position}` : detailEntry ? `工具详情 · ${position}` : `对话 · ${reading ? '阅读焦点 · ' : ''}${scroll.follow || isEmpty ? '跟随最新' : `回看 · 下方 ${maximum-top} 行`} · ${position}`;
  const hint = state.approval ? `${approvalHint} · 滚轮/PgUp/PgDn 翻页 · Ctrl+C 取消` : contextOpen ? '←/→ 选快照 · l 最新 · ↑↓/滚轮 翻阅 · Esc 返回' : detailEntry ? '滚轮/↑↓ 翻阅 · Home/End 首尾 · Esc 返回' :
    reading ? '阅读焦点：↑↓滚动  空格翻页  End最新  Esc输入  Ctrl+Q退出' :
      columns < 60 ? 'Ctrl+K上下文 · Ctrl+Q退出 · /help' : '滚轮回看  Ctrl+R阅读  Ctrl+K上下文  Ctrl+J换行  Ctrl+Q退出';
  const busy = state.phase !== 'idle';
  const unavailableHint = state.phase === 'closed' ? '连接已断开 · Ctrl+Q 退出后重启' : state.phase === 'starting' ? '可编辑草稿 · 后端就绪后可发送' :
    state.phase === 'stopping' ? '正在停止 · 收到停止结果后可发送' : undefined;

  if (rows < 11 || columns < 24) return <Box width={columns} height={rows} backgroundColor={palette.background} flexDirection="column" overflow="hidden">
    <Text color={palette.foreground} bold>{fitText('mini-harness', columns)}</Text>
    {rows > 1 ? <Text color={palette.foreground}>{fitText('请将终端调整到至少 24×11', columns)}</Text> : null}
    {rows > 2 ? <Text color={palette.secondary}>{fitText(state.approval ? `${approvalHint} Ctrl+C取消` : 'Ctrl+Q 退出', columns)}</Text> : null}
  </Box>;

  return <Box flexDirection="column" width={columns} height={rows} backgroundColor={palette.background} overflow="hidden">
    <AgentHeader state={state} columns={columns} height={headerRows} palette={palette}/>
    {compactRows ? <StatusPanel state={state} columns={columns} height={compactRows} palette={palette}/> : null}
    <Box flexDirection="row" width={columns} height={paneRows} flexShrink={0}>
      <ReadingPane title={paneTitle} lines={lines} top={top} height={paneRows} columns={readerColumns} palette={palette} overlay={readerFocused}/>
      {sidebar ? <><Box width={1}/><StatusPanel state={state} columns={32} height={paneRows} palette={palette}/></> : null}
    </Box>
    <Box backgroundColor={state.approval ? palette.surfaces.warning : readerFocused ? palette.surface : palette.focus} height={composerRows + 2} width={columns} paddingX={2} paddingY={1} flexShrink={0}>
      <Composer value={input} onChange={setInput} onSubmit={submit} history={history} disabled={busy || quitting} disabledHint={unavailableHint} focused={!readerFocused && !quitting} placeholder={state.approval ? '审批面板已聚焦，请选择授权范围' : contextOpen ? '上下文详情已聚焦，Esc 返回输入' : detailEntry ? '工具详情已聚焦，Esc 返回输入' : reading ? '正在阅读历史，Esc 返回输入' : state.phase === 'closed' ? '可以回看已有记录，退出后重新启动' : state.phase === 'starting' ? '可以先写下任务目标与约束' : busy ? '编辑下一条任务草稿' : '描述任务，或输入 / 查看命令'} columns={Math.max(1, columns - 4)} maxRows={composerRows} accentColor={palette.accent} warningColor={palette.warning} foregroundColor={palette.foreground} secondaryColor={palette.secondary}/>
    </Box>
    <Text color={feedback || state.error || state.phase === 'closed' ? palette.error : state.approval ? palette.warning : palette.foreground}>{fitText(feedback || `${phase}${state.lastResult ? ` · ${state.lastResult}` : ''}${metricsText(state) ? `  ·  ${metricsText(state)}` : ''}`, columns)}</Text>
    <Text color={palette.secondary}>{fitText(hint, columns)}</Text>
  </Box>;
}
