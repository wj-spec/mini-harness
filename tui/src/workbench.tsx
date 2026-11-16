import React from 'react';
import {outcomePresentation} from './outcome.js';
import {Box, Text} from 'ink';
import stringWidth from 'string-width';
import type {Entry, ViewState} from './view-state.js';
import {fitText, markdownLines, ReadingText, textLines, type ReadingLine, type Span} from './markdown.js';
import type {Palette, SemanticTone} from './theme.js';
import type {PlanStepInfo} from './protocol.js';
import {selectionCells, type Selection} from './selection.js';
import {HOME_EXAMPLES} from './guidance.js';

export function toolState(entry: Entry): string {
  if (entry.uncertain) return '结果未知';
  if (entry.pending) return '等待返回';
  if (entry.execution) {
    const label = {succeeded: '零退出', failed: '非零退出', timed_out: '已超时', cancelled: '已取消', unknown: '执行结果未知'}[entry.execution.status];
    return `${label}${entry.execution.exit_code !== null ? ` · exit=${entry.execution.exit_code}` : ''}`;
  }
  if (entry.call?.name === 'bash' && entry.ok !== false) return '执行状态未记录';
  return entry.ok === false ? '调用错误' : entry.ok === true ? '调用已返回' : '工具记录';
}

export function toolTone(entry: Entry): SemanticTone | undefined {
  if (entry.uncertain || entry.execution?.status === 'unknown') return 'warning';
  if (entry.pending) return 'accent';
  if (entry.execution?.status === 'cancelled' || entry.execution?.status === 'timed_out') return 'warning';
  if (entry.ok === false || entry.execution?.status === 'failed') return 'error';
  if (entry.execution?.status === 'succeeded') return 'success';
  return undefined;
}

function cardLines(title: string, body: ReadingLine[], columns: number, tone?: SemanticTone, selected = false,
  surface: ReadingLine['surface'] = 'agent'): ReadingLine[] {
  const width = Math.max(1, columns);
  const padding = width >= 8 ? 2 : 0;
  const fill = (spans: ReadingLine['spans']) => {
    const size = spans.reduce((total, span) => total + stringWidth(span.text), 0);
    return [{text: ' '.repeat(padding)}, ...spans, {text: ' '.repeat(Math.max(0, width - padding - size))}];
  };
  const bar = tone === 'error' ? '▌' : tone === 'warning' ? '▌' : tone === 'accent' ? '▌' : '▌';
  const titleSpans: Span[] = [{text: bar, bold: true}, {text: ` ${fitText(`${selected ? '› ' : ''}${title}`, Math.max(1, width - padding * 2 - 2))}`, bold: true}];
  return [
    {spans: fill(titleSpans),
      kind: tone === 'error' ? 'error' : 'label', tone, selected, surface},
    ...body.map(line => ({...line, surface: line.surface ?? surface, spans: fill(line.spans)})),
  ];
}

function toolSummary(entry: Entry, columns: number, selected: boolean): ReadingLine[] {
  const name = entry.call?.name ?? 'tool';
  const inside = Math.max(1, columns - 4);
  const args = entry.call?.args ?? {};
  const summary = Object.entries(args).map(([key, value]) => `${key}: ${typeof value === 'string' && value.length > 80 ? `${value.length} 字符` : Array.isArray(value) ? `[${value.length} 项]` : value && typeof value === 'object' ? `{${Object.keys(value).length} 字段}` : JSON.stringify(value)}`).join(' · ');
  const lines: ReadingLine[] = [];
  if (summary) lines.push({spans: [{text: fitText(summary, inside)}], kind: 'tool'});
  if (entry.output !== undefined) {
    const first = entry.output.trim().split('\n').find(Boolean) || '(空结果)';
    lines.push({spans: [{text: fitText(first, inside)}], kind: entry.ok === false ? 'error' : 'tool'});
  }
  if (selected) lines.push({spans: [{text: fitText('Ctrl+O 查看完整参数 / 输出', inside)}], kind: 'tool'});
  const tone = toolTone(entry);
  const surface = tone === 'error' ? 'error' : tone === 'warning' ? 'warning' : selected ? 'selected' : 'tool';
  return cardLines(`工具 · ${toolState(entry)} · ${name}`, lines, columns, tone, selected, surface);
}

function noticePresentation(text: string): {title: string; text: string; tone: SemanticTone} {
  if (text.startsWith('后端提示：')) return {title: '后端诊断', text: text.slice('后端提示：'.length), tone: 'warning'};
  if (text.startsWith('完成守卫：')) return {title: '完成守卫', text, tone: 'warning'};
  if (text.startsWith('[运行结束]')) return {title: '历史运行记录', text: '原始记录请在验证详情中查看', tone: 'accent'};
  const first = text.split('\n')[0] ?? '';
  const title = /未知命令|失败|错误|未能|未送达/.test(first) ? '操作异常' : /预算|压缩/.test(first) ? '上下文与预算' :
    /^(会话|恢复)/.test(first) ? '会话信息' : /^(主题|当前主题)/.test(first) ? '外观设置' :
      /^MCP|^mcp/i.test(first) ? 'MCP 服务' : /权限|授权/.test(first) ? '权限规则' : '工作台记录';
  return {title, text, tone: title === '操作异常' ? 'error' : title === '上下文与预算' ? 'warning' : 'accent'};
}

export function liveLines(text: string, columns: number): ReadingLine[] {
  return text ? cardLines('Agent · 正在回复', markdownLines(text, Math.max(1, columns - 4)), columns, 'accent') : [];
}

export function errorLines(error: string, columns: number): ReadingLine[] {
  return cardLines('运行错误', textLines(error, Math.max(1, columns - 4), 'error'), columns, 'error', false, 'error');
}

function collapsibleReadOnly(entry: Entry, selectedTool: number | null): boolean {
  return entry.kind === 'tool' && Boolean(entry.call) && entry.tier === 'read' && entry.ok === true &&
    !entry.pending && !entry.uncertain && !entry.execution && !entry.inspection && entry.id !== selectedTool;
}

function collapsedToolLines(group: Entry[], columns: number): ReadingLine[] {
  const counts = new Map<string, number>();
  for (const entry of group) counts.set(entry.call!.name, (counts.get(entry.call!.name) ?? 0) + 1);
  const summary = [...counts].map(([name, count]) => `${name}×${count}`).join(' · ');
  const inside = Math.max(1, columns - 4);
  return [
    {spans: [{text: fitText(`▌ 连续 ${group.length} 个只读调用 · 已折叠`, columns)}], kind: 'label', surface: 'tool'},
    {spans: [{text: fitText(`  ${summary} · 全部成功 · Tab 选择或 Ctrl+O 查看原始调用与输出`, inside)}], kind: 'tool', surface: 'tool'},
  ];
}

export function planStepLine(step: PlanStepInfo, index?: number): string {
  const kind = step.kind + (step.risk !== 'read' ? `/${step.risk}` : '');
  const files = step.files.length ? ` · ${step.files.join(', ')}` : '';
  const deps = step.depends_on.length ? ` ← ${step.depends_on.join(',')}` : '';
  const acceptance = step.acceptance ? ' · 有验收' : '';
  return `${index ? `${index}. ` : ''}○ ${step.step_id} ${step.title} · ${kind}${files}${deps}${acceptance}`;
}

export function transcriptLines(state: ViewState, columns: number, selectedTool: number | null): ReadingLine[] {
  const lines: ReadingLine[] = [];
  const latestTask = state.entries.findLast(entry => entry.kind === 'task')?.id;
  const pushEntry = (entry: Entry, entryLines: ReadingLine[]) => {
    if (entry.inspection) {
      const actions = [{page: 'changes' as const, label: '变更'}, {page: 'plan' as const, label: '计划'}, {page: 'verification' as const, label: '验证'}];
      const groups = columns >= 26 ? [actions] : actions.map(action => [action]);
      for (const group of groups) {
        let left = 0;
        const inspectionActions = group.map(({page}) => {const action = {page, left, right: left + 7}; left += 9; return action;});
        entryLines.push({spans: [{text: group.map(action => `[ ${action.label} ]`).join(' '), bold: true}], inspectionActions, kind: 'label'});
      }
    }
    lines.push(...entryLines.map(line => ({...line, entryId: entry.id})));
  };
  for (let position = 0; position < state.entries.length; position += 1) {
    const entry = state.entries[position]!;
    // 工作台提示不再插入对话主区；运行回执、计划审阅与历史结果卡片仍留在正文。
    if (entry.continuing) continue;
    if (entry.kind === 'notice' && !entry.result && !entry.text.startsWith('[运行结束]') && !entry.review && !entry.inspection) continue;
    let end = position + 1;
    while (end < state.entries.length && collapsibleReadOnly(state.entries[end]!, selectedTool)) end += 1;
    const collapseGroup = collapsibleReadOnly(entry, selectedTool) && end - position >= 2 ? state.entries.slice(position, end) : null;
    if (lines.length) lines.push({spans: [{text: ''}]});
    if (collapseGroup) {
      pushEntry(entry, collapsedToolLines(collapseGroup, columns));
      position = end - 1;
      continue;
    }
    let entryLines: ReadingLine[];
    if (entry.result) {
      const presentation = outcomePresentation(entry.result);
      entryLines = textLines(presentation.label, columns).map(line => ({...line, tone: presentation.tone}));
      if (presentation.tone === 'warning' || presentation.tone === 'error') entryLines.push(...textLines(presentation.detail, columns));
    } else if (entry.review) {
      const {target, summary, message, launch} = entry.review;
      const steps = entry.review.steps ?? [];
      const shown = steps.slice(0, 8);
      entryLines = [
        `计划 v${target?.plan_version ?? '?'} · ${summary?.objective ?? '结果待核对'}`,
        `步骤 ${summary?.step_count ?? '?'} · 验收 ${summary?.criterion_count ?? '?'} · 待决定 ${summary?.question_count ?? '?'}`,
        ...shown.map((step, index) => planStepLine(step, index + 1)),
        ...(steps.length > shown.length ? [`…另有 ${steps.length - shown.length} 步，Ctrl+R 查看完整正文`] : []),
        entry.reviewProblem ?? (launch ? `${message} · ${launch.task_id} · ${launch.status}` : message),
        'Ctrl+R审阅 · 正文与动作',
      ].map(text => ({spans: [{text: fitText(text.replace(/\s+/g, ' '), columns)}]}));
    } else if (entry.kind === 'tool' && entry.call) entryLines = toolSummary(entry, columns, entry.id === selectedTool);
    else {
      const inside = Math.max(1, columns - 4);
      if (entry.kind === 'notice') {
        const presentation = noticePresentation(entry.text);
        entryLines = cardLines(presentation.title, textLines(presentation.text, inside), columns, presentation.tone, false,
          presentation.tone === 'error' ? 'error' : presentation.tone === 'warning' ? 'warning' : 'notice');
      } else {
        const label = {task: entry.id === latestTask ? '你 · 当前任务' : '你 · 任务', assistant: 'Agent · 回复', tool: '工具记录', notice: '工作台记录', approval: '审批记录'}[entry.kind];
        entryLines = cardLines(label, entry.kind === 'assistant' ? markdownLines(entry.text, inside) : textLines(entry.text, inside), columns,
          entry.kind === 'approval' ? 'warning' : entry.kind === 'task' ? 'accent' : undefined, false,
          entry.kind === 'task' ? 'task' : entry.kind === 'approval' ? 'warning' : 'agent');
      }
    }
    pushEntry(entry, entryLines);
  }
  return lines;
}

export function modelStatusText(state: ViewState): string {
  return state.demo ? 'Demo · 不调用真实模型'
    : state.provider === 'fake' ? '测试模型 · 不调用真实服务'
      : state.modelCheck === 'passed' ? '连接检测通过'
        : state.modelCheck === 'failed' ? '检测未通过 · /model 修复'
          : '已配置 · 尚未检测';
}

export function welcomeLines(state: ViewState, columns: number, options: {omitSummary?: boolean} = {}): ReadingLine[] {
  const compact = columns < 72;
  const blank: ReadingLine = {spans: [{text: ''}]};
  const lines: ReadingLine[] = [];
  const workspace = state.workspace || '工作区待连接';
  const name = workspace.split(/[\\/]/).filter(Boolean).at(-1) ?? workspace;
  const model = state.model || '模型待连接';
  const status = state.demo ? 'Demo · 不调用真实模型'
    : state.provider === 'fake' ? '测试模型 · 不调用真实服务'
      : state.modelCheck === 'passed' ? '连接检测通过'
      : state.modelCheck === 'failed' ? '检测未通过 · /model 修复'
        : '已配置 · 尚未检测';
  lines.push(...textLines(compact ? 'Mini · 今天准备做点什么？' : 'Mini · 今天准备做点什么？', columns, 'heading'));
  lines.push(blank);
  if (!options.omitSummary) {
    lines.push(...textLines(`项目  ${name}${compact ? '' : '    /project 查看'}`, columns));
    lines.push(...textLines(`模型  ${model} · ${status}${compact ? '' : '    /model 更改'}`, columns));
  }
  if (state.mcpConfigured) {
    const pending = Math.max(0, state.mcpConfigured - state.mcpConnected);
    lines.push(...textLines(compact
      ? `工具  ${pending ? `${pending} 个未连接` : '已连接'} · /mcp`
      : `外部工具  ${state.mcpConnected}/${state.mcpConfigured} 已连接${pending ? '    /mcp 选择服务后连接' : ''}`, columns));
  }
  lines.push(blank);
  lines.push(...textLines('▌ 从这些开始（按 1 / 2 / 3 填入草稿，不自动发送）', columns, 'label'));
  for (const key of ['1', '2', '3'] as const) {
    const example = HOME_EXAMPLES[key];
    lines.push(...textLines(compact ? `  ${key}  ${example.title}` : `  ${key}  ${example.title}  ${example.detail}`, columns));
  }
  lines.push(blank);
  lines.push(...textLines(compact
    ? '输入框描述任务，Enter 发送；已有草稿不会被示例覆盖。'
    : '也可以直接描述任务：Enter 发送，Ctrl+J 换行，Ctrl+T 功能区指引。已有草稿不会被示例覆盖。', columns, 'notice'));
  return lines;
}

export function detailLines(entry: Entry, columns: number): ReadingLine[] {
  return [
    ...textLines(`${toolState(entry)} · ${entry.call?.name ?? '工具'}`, columns, 'heading'),
    ...textLines('参数', columns, 'label'),
    ...textLines(JSON.stringify(entry.call?.args ?? {}, null, 2), columns, 'code'),
    {spans: [{text: ''}]},
    ...textLines('完整输出', columns, 'label'),
    ...textLines(entry.output ?? (entry.pending ? '等待工具返回。' : entry.uncertain ? '运行已停止，尚未确认执行结果。请先核对实际变化。' : entry.text), columns),
  ];
}

export function ReadingPane({title, lines, top, height, columns, palette, overlay = false, scrollbar = true, selection = null}: {
  title: string; lines: ReadingLine[]; top: number; height: number; columns: number; palette: Palette; overlay?: boolean; scrollbar?: boolean; selection?: Selection | null;
}) {
  const bodyRows = Math.max(1, height - 3);
  const bodyColumns = Math.max(1, columns - 4 - Number(scrollbar));
  const maximum = Math.max(0, lines.length - bodyRows);
  const clampedTop = Math.min(Math.max(0, top), maximum);
  const thumbSize = Math.min(bodyRows, Math.max(1, Math.floor(bodyRows * bodyRows / Math.max(bodyRows, lines.length))));
  const thumbTop = maximum ? Math.round(clampedTop / maximum * (bodyRows - thumbSize)) : 0;
  return <Box width={columns} height={height} flexDirection="column" paddingX={2} paddingTop={1} paddingBottom={1} flexShrink={0} overflow="hidden">
    <Text color={overlay ? palette.accent : palette.secondary} bold={overlay}>{fitText(title, columns - 4)}</Text>
    <Box flexDirection="row" height={bodyRows} flexShrink={0} overflow="hidden">
      <Box flexDirection="column" width={bodyColumns} height={bodyRows} flexShrink={0} overflow="hidden">
        {lines.slice(clampedTop, clampedTop + bodyRows).map((line, index) => <ReadingText key={`${clampedTop + index}`} line={line} palette={palette} columns={bodyColumns} select={selectionCells(selection, clampedTop + index) ?? undefined}/>)}
      </Box>
      {scrollbar ? <Box flexDirection="column" width={1} height={bodyRows} flexShrink={0}>
        {Array.from({length: bodyRows}, (_, index) => <Text key={index} color={index >= thumbTop && index < thumbTop + thumbSize ? palette.accent : palette.secondary}>{lines.length > bodyRows ? index >= thumbTop && index < thumbTop + thumbSize ? '┃' : ' ' : ' '}</Text>)}
      </Box> : null}
    </Box>
  </Box>;
}

export function compactNumber(value: number): string {
  if (value >= 1_000_000) return `${Number((value / 1_000_000).toFixed(1))}M`;
  if (value >= 1000) return `${Number((value / 1000).toFixed(1))}k`;
  return String(value);
}

export function metricsText(state: ViewState): string {
  const metrics = state.metrics;
  const snapshot = state.contextSnapshots.at(-1);
  const parts: string[] = [];
  if (metrics) parts.push(`任务累计 ${metrics.estimated ? '≈' : ''}${compactNumber(metrics.used_tokens)}/${metrics.limit_tokens ? compactNumber(metrics.limit_tokens) : '不限'}`);
  if (snapshot) parts.push(`上下文 ≈${compactNumber(snapshot.total_tokens)}/${compactNumber(snapshot.context_window)}`);
  else if (metrics?.context_tokens !== null && metrics?.context_tokens !== undefined) parts.push(`上下文 ${compactNumber(metrics.context_tokens)}/${compactNumber(metrics.context_window)}`);
  if (metrics?.planned) parts.push(`计划 ${metrics.done}/${metrics.planned}`);
  if (metrics?.cost_usd) parts.push(`$${metrics.cost_usd.toFixed(3)}`);
  return parts.join(' · ');
}
