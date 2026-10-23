import React from 'react';
import {Box, Text} from 'ink';
import stringWidth from 'string-width';
import type {Entry, ViewState} from './view-state.js';
import {fitText, markdownLines, ReadingText, textLines, type ReadingLine} from './markdown.js';
import type {Palette, SemanticTone} from './theme.js';

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
  return [
    {spans: fill([{text: fitText(`${selected ? '› ' : ''}${title}`, Math.max(1, width - padding * 2)), bold: true}]),
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

export function transcriptLines(state: ViewState, columns: number, selectedTool: number | null): ReadingLine[] {
  const lines: ReadingLine[] = [];
  const latestTask = state.entries.findLast(entry => entry.kind === 'task')?.id;
  for (const entry of state.entries) {
    if (lines.length) lines.push({spans: [{text: ''}]});
    let entryLines: ReadingLine[];
    if (entry.kind === 'tool' && entry.call) entryLines = toolSummary(entry, columns, entry.id === selectedTool);
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
    lines.push(...entryLines.map(line => ({...line, entryId: entry.id})));
  }
  return lines;
}

export function welcomeLines(columns: number): ReadingLine[] {
  return [
    ...textLines('开始一个真实编码任务', columns, 'heading'),
    ...textLines('描述目标、项目范围与完成条件。工具动作会按权限审批。', columns),
    {spans: [{text: ''}]},
    ...textLines('可以从这里开始', columns, 'label'),
    ...textLines('• 读 README，解释这个项目的入口和运行流程', columns),
    ...textLines('• 定位一个可复现的小 bug，先说明判断，再修改和验证', columns),
    ...textLines('• 检查当前改动，列出风险与尚未验证的部分', columns),
    {spans: [{text: ''}]},
    ...textLines('Enter 发送 · Ctrl+J 换行 · ↑↓ 浏览输入历史', columns),
    ...textLines('滚轮 / PgUp / PgDn 回看 · Ctrl+R 阅读模式', columns),
    ...textLines('阅读模式 ↑↓ 滚动 · Esc 回输入 · Ctrl+O 工具详情', columns),
    ...textLines('/help 操作帮助 · /theme system|light|dark 主题', columns),
    ...textLines('/resume 续做 · /permissions 权限 · /mcp 服务状态', columns),
    ...textLines('/context 或 Ctrl+K 上下文分区与本轮回放', columns),
  ];
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

export function ReadingPane({title, lines, top, height, columns, palette, overlay = false, scrollbar = true}: {
  title: string; lines: ReadingLine[]; top: number; height: number; columns: number; palette: Palette; overlay?: boolean; scrollbar?: boolean;
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
        {lines.slice(clampedTop, clampedTop + bodyRows).map((line, index) => <ReadingText key={`${clampedTop + index}`} line={line} palette={palette} columns={bodyColumns}/>)}
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
  if (metrics) parts.push(`任务 Tokens ${metrics.estimated ? '≈' : ''}${compactNumber(metrics.used_tokens)}/${metrics.limit_tokens ? compactNumber(metrics.limit_tokens) : '不限'}`);
  if (snapshot) parts.push(`上下文 ≈${compactNumber(snapshot.total_tokens)}/${compactNumber(snapshot.context_window)}`);
  else if (metrics?.context_tokens !== null && metrics?.context_tokens !== undefined) parts.push(`上下文 ${compactNumber(metrics.context_tokens)}/${compactNumber(metrics.context_window)}`);
  if (metrics?.planned) parts.push(`计划 ${metrics.done}/${metrics.planned}`);
  if (metrics?.cost_usd) parts.push(`$${metrics.cost_usd.toFixed(3)}`);
  return parts.join(' · ');
}
