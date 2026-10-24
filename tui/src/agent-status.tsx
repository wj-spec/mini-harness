import React, {useEffect, useState} from 'react';
import {Box, Text} from 'ink';
import stringWidth from 'string-width';
import type {ViewState} from './view-state.js';
import {fitText, ReadingSpans, wrapSpans, type Span} from './markdown.js';
import {compactNumber, toolState} from './workbench.js';
import type {Palette, SemanticTone} from './theme.js';
import {contextLine, contextSummarySpans, needsContextComposition} from './context-view.js';
import type {ContextSnapshot} from './protocol.js';

export type AgentStatus = {label: string; detail: string; tone: SemanticTone};

export function agentStatus(state: ViewState): AgentStatus {
  if (state.phase === 'closed') return {label: '后端已断开', detail: '重新启动入口以恢复会话', tone: 'error'};
  if (state.phase === 'stopping') return {label: '正在停止', detail: '等待后端确认停止结果', tone: 'warning'};
  if (state.phase === 'waiting_approval') return {label: '等待审批', detail: `${state.approval?.tool ?? '工具动作'} 等待你确认`, tone: 'warning'};
  if (state.error) return {label: state.phase === 'starting' ? '启动失败' : '运行异常', detail: state.error, tone: 'error'};
  if (state.phase === 'starting') return state.activeId === 'startup' && state.activity === 'initializing' ?
    {label: '初始化服务', detail: state.resumed ? '正在恢复会话与准备 MCP 服务' : '正在准备 MCP 与运行环境', tone: 'accent'} :
    {label: '正在启动后端', detail: state.resumed ? '正在恢复历史与服务' : '正在连接工作区与服务', tone: 'accent'};
  if (state.phase === 'running') {
    if (state.activeCommand && state.activeCommand !== 'resume') return {label: '处理工作台命令', detail: `/${state.activeCommand.replaceAll('_', ' ')}`, tone: 'accent'};
    if (state.activity === 'initializing') return {label: '初始化服务', detail: '正在准备运行环境', tone: 'accent'};
    if (state.activity === 'using_tool') {
      const pending = state.entries.filter(entry => entry.kind === 'tool' && entry.pending);
      return {label: '运行中 · 调用工具', detail: pending.length ? `${pending.at(-1)?.call?.name ?? '工具'} · 等待返回 ${pending.length} 项` : '等待工具执行结果', tone: 'accent'};
    }
    if (state.activity === 'responding' || state.live) return {label: '运行中 · 正在回复', detail: '正在接收模型输出', tone: 'accent'};
    return {label: '运行中 · 等待模型', detail: '模型请求已发出，等待输出', tone: 'accent'};
  }
  if (state.lastStatus === 'stopped') return {label: '任务已暂停', detail: state.lastResult || '核对进度后可 /resume 续做', tone: 'warning'};
  if (state.lastStatus === 'truncated') return {label: '本轮未完成', detail: state.lastResult || '查看输出并核对剩余工作', tone: 'warning'};
  if (state.lastStatus === 'cancelled') return {label: '已取消', detail: '已发生的修改仍需核对', tone: 'warning'};
  if (state.lastStatus === 'error') return {label: '运行失败', detail: state.lastResult || '查看错误与工具记录', tone: 'error'};
  if (state.lastStatus === 'done') return {label: '本轮结束', detail: '请结合输出与验证记录核对结果', tone: 'success'};
  return {label: '就绪', detail: state.resumed ? '历史已恢复；发送任务或 /resume' : '输入任务开始工作', tone: 'success'};
}

const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

function useSpinner(active: boolean): string {
  const [frame, setFrame] = useState(0);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setFrame(current => (current + 1) % SPINNER_FRAMES.length), 100);
    return () => clearInterval(timer);
  }, [active]);
  return active ? SPINNER_FRAMES[frame]! : '';
}

export function budgetBar(used: number, limit: number, width = 12): string {
  if (!limit) return '未设总上限';
  const ratio = used / limit;
  const filled = Math.max(0, Math.min(width, Math.round(ratio * width)));
  return `[${'█'.repeat(filled)}${'░'.repeat(width - filled)}] ${Math.floor(ratio * 100)}%`;
}

export function AgentHeader({state, columns, height = 3, palette}: {
  state: ViewState; columns: number; height?: number; palette: Palette;
}) {
  const status = agentStatus(state);
  const spinning = state.phase === 'running' || state.phase === 'starting' || state.phase === 'stopping';
  const spinner = useSpinner(spinning);
  const statusText = spinning ? ` ${spinner} ${status.label} ` : ` ${status.label} `;
  const showStatus = columns >= 50;
  const statusWidth = showStatus ? Math.min(stringWidth(statusText), Math.max(14, columns - 24)) : 0;
  const titleWidth = Math.max(1, columns - (showStatus ? statusWidth + 1 : 0));
  const title = state.sessionPurpose === 'test' ? '测试会话 · mini-harness' : columns >= 65 ? 'mini-harness / 个人编码工作台' : 'mini-harness';
  const badgeBackground = status.tone === 'warning' || status.tone === 'error' ? palette.surfaces[status.tone] : palette.raised;
  return <Box width={columns} height={height} backgroundColor={palette.background} flexDirection="column" flexShrink={0} overflow="hidden">
    <Box height={1} width={columns} flexShrink={0}>
      <Box width={titleWidth}><Text bold color={palette.accent}>{fitText(title, titleWidth)}</Text></Box>
      {showStatus ? <Box width={statusWidth + 1} justifyContent="flex-end"><Text bold color={palette[status.tone]} backgroundColor={badgeBackground}>{fitText(statusText, statusWidth)}</Text></Box> : null}
    </Box>
    {height > 1 ? <Text color={palette.secondary}>{fitText(`${state.workspace || '工作区待连接'}  ·  ${state.model || '模型待连接'}`, columns, true)}</Text> : null}
    {height > 2 ? <Text color={palette.secondary}>{fitText(`会话 ${state.sessionId || '待连接'}${state.resumed ? ' · 已恢复' : ''}  |  ${state.provider || 'provider 待连接'}`, columns)}</Text> : null}
  </Box>;
}

type StatusLine = {text: string; spans?: Span[]; tone?: SemanticTone; bold?: boolean; muted?: boolean; highlighted?: boolean};

const richStatusLine = (spans: Span[]): StatusLine => ({text: spans.map(span => span.text).join(''), spans});
const inputPercentage = (snapshot: ContextSnapshot) => snapshot.input_capacity ? `${(snapshot.total_tokens / snapshot.input_capacity * 100).toFixed(1)}%` : '容量0';
function occupancyStatusLines(snapshot: ContextSnapshot, columns: number): StatusLine[] {
  const prefix = `输入${inputPercentage(snapshot)} `;
  const main = contextLine(snapshot, columns, false, prefix);
  return [
    {text: `上下文估算 ${compactNumber(snapshot.total_tokens)}/${compactNumber(snapshot.input_capacity)}`, tone: snapshot.over_budget ? 'warning' : undefined},
    richStatusLine(contextSummarySpans(snapshot)),
    richStatusLine(main.spans),
    ...(needsContextComposition(snapshot, Math.max(1, columns - stringWidth(prefix) - 2)) ? [richStatusLine(contextLine(snapshot, columns, true, '已用构成 ').spans)] : []),
  ];
}

function statusLines(state: ViewState, columns: number, contextFirst = false): StatusLine[] {
  const status = agentStatus(state);
  const task = state.entries.findLast(entry => entry.kind === 'task');
  const tools = state.entries.filter(entry => entry.kind === 'tool');
  const failures = tools.filter(entry => entry.ok === false || ['failed', 'timed_out', 'cancelled'].includes(entry.execution?.status ?? ''));
  const latest = tools.at(-1);
  const separator = {text: ' '};
  const snapshot = state.contextSnapshots.at(-1);
  const metrics = state.metrics;
  const context: StatusLine[] = snapshot ? [
    ...occupancyStatusLines(snapshot, columns),
    {text: `预留 输出${compactNumber(snapshot.reserves.output)} 安全${compactNumber(snapshot.reserves.scratch)}`},
    {text: `输入剩余 ${compactNumber(snapshot.remaining_tokens)}`},
    {text: 'Ctrl+K 分区与本轮回放'},
  ] : [];
  if (snapshot?.phase === 'loaded') return [
    {text: status.label, tone: status.tone, bold: true, highlighted: true},
    {text: '基础上下文已装载', bold: true},
    {text: '尚未请求模型', muted: true},
    separator,
    ...occupancyStatusLines(snapshot, columns),
    ...(metrics ? [{text: `任务 Tokens ${metrics.used_tokens}（尚未开始）`}] : []),
    {text: task ? '历史任务已恢复' : '尚未提交任务', muted: true},
    {text: 'Ctrl+K 装载详情'},
    separator,
    {text: `预留 输出${compactNumber(snapshot.reserves.output)} 安全${compactNumber(snapshot.reserves.scratch)}`},
    {text: `输入剩余 ${compactNumber(snapshot.remaining_tokens)}`},
  ];
  const lines: StatusLine[] = [
    {text: status.label, tone: status.tone, bold: true, highlighted: true},
    {text: status.detail, muted: true},
    separator,
    ...(contextFirst ? [...context, separator] : []),
    {text: '当前任务', bold: true, muted: true},
    {text: task?.text.split('\n').find(Boolean) || '尚未提交任务'},
    separator,
    {text: '工具实况', bold: true, muted: true},
    {text: `调用 ${tools.length} · 待返回 ${tools.filter(entry => entry.pending).length} · 异常 ${failures.length}`},
    ...(latest ? [{text: `${toolState(latest)} · ${latest.call?.name ?? '工具'}`, tone: latest.uncertain ? 'warning' as const : latest.ok === false ? 'error' as const : undefined}] : []),
    separator,
    {text: '资源预算', bold: true, muted: true},
  ];
  if (!metrics && !snapshot) return [...lines, {text: '尚无用量记录'}];
  if (metrics) {
    lines.push({text: `任务 Tokens ${metrics.estimated ? '≈' : ''}${compactNumber(metrics.used_tokens)}/${metrics.limit_tokens ? compactNumber(metrics.limit_tokens) : '不限'}`});
    lines.push({text: budgetBar(metrics.used_tokens, metrics.limit_tokens, Math.min(12, Math.max(2, columns - 10))),
      tone: metrics.limit_tokens && metrics.used_tokens / metrics.limit_tokens >= 0.8 ? 'warning' : undefined});
  }
  if (snapshot && !contextFirst) lines.push(...context);
  else if (!snapshot && metrics?.context_tokens !== null && metrics?.context_tokens !== undefined) lines.push({text: `上下文 ${compactNumber(metrics.context_tokens)}/${compactNumber(metrics.context_window)}`});
  if (metrics?.planned) lines.push({text: `已记计划 ${metrics.done}/${metrics.planned}`});
  if (metrics?.cost_usd) lines.push({text: `成本 $${metrics.cost_usd.toFixed(3)}`});
  return lines;
}

export function StatusPanel({state, columns, height, palette}: {
  state: ViewState; columns: number; height: number; palette: Palette;
}) {
  const available = Math.max(1, columns - 4);
  const status = agentStatus(state);
  const task = state.entries.findLast(entry => entry.kind === 'task');
  const compact = height < 8;
  const snapshot = state.contextSnapshots.at(-1);
  const compactContext = compact && snapshot !== undefined;
  const compactInputPrefix = '输入 ';
  const compactComposition = snapshot ? needsContextComposition(snapshot, Math.max(1, available - stringWidth(compactInputPrefix) - 2)) : false;
  const lines: StatusLine[] = compact ? [
    {text: `${status.label}${snapshot?.phase === 'loaded' ? '  |  基础上下文已装载 · 尚未请求模型' : task ? `  |  ${task.text}` : `  |  ${status.detail}`}`, tone: status.tone, bold: true, highlighted: true},
    ...(snapshot ? [
      {...richStatusLine([{text: `上下文估算 ${compactNumber(snapshot.total_tokens)}/${compactNumber(snapshot.input_capacity)} 输入${inputPercentage(snapshot)} · `}, ...contextSummarySpans(snapshot)]), tone: snapshot.over_budget ? 'warning' as const : undefined},
      richStatusLine(contextLine(snapshot, available, compactComposition, compactComposition ? '已用构成 ' : compactInputPrefix).spans),
    ] : []),
  ] : statusLines(state, available, height < 24 && snapshot !== undefined);
  const statusBackground = status.tone === 'warning' || status.tone === 'error' ? palette.surfaces[status.tone] : palette.raised;
  return <Box width={columns} height={height} backgroundColor={palette.surface} flexShrink={0} flexDirection="column" paddingX={2} paddingY={compactContext ? 0 : 1} overflow="hidden">
    {lines.slice(0, Math.max(0, height - (compactContext ? 0 : 2))).map((line, index) => {
      const text = fitText(line.text, available);
      return <Text key={index} color={line.tone ? palette[line.tone] : line.muted ? palette.secondary : palette.foreground}
        backgroundColor={line.highlighted ? statusBackground : undefined} bold={line.bold}>
        {line.spans ? <ReadingSpans spans={wrapSpans(line.spans, available)[0] ?? []} palette={palette}/> :
          line.highlighted ? text + ' '.repeat(Math.max(0, available - stringWidth(text))) : text}
      </Text>;
    })}
  </Box>;
}
