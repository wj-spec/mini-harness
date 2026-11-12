import React from 'react';
import {Box, Text} from 'ink';
import type {PlanProgress, PlanProgressStep} from './protocol.js';
import {fitText, textLines, type ReadingLine} from './markdown.js';
import type {Palette} from './theme.js';

type Tone = 'accent' | 'success' | 'warning' | 'secondary' | 'foreground';
type PanelLine = {text: string; tone: Tone};

const ICONS: Record<PlanProgressStep['status'], string> = {
  pending: '○', in_progress: '◐', blocked: '!', done: '✓', deviated: '!', skipped: '–',
};

function statusIcon(step: PlanProgressStep): string {
  return ICONS[step.status];
}

function isClosed(step: PlanProgressStep): boolean {
  return step.status === 'done' || step.status === 'skipped';
}

function stepKind(step: PlanProgressStep): string {
  return step.kind + (step.risk !== 'read' ? `/${step.risk}` : '');
}

function stepFiles(step: PlanProgressStep): string {
  return step.files.length ? ` · ${step.files.slice(0, 2).join(', ')}${step.files.length > 2 ? ' …' : ''}` : '';
}

function stepTone(step: PlanProgressStep): Tone {
  if (step.status === 'done') return 'success';
  if (step.status === 'in_progress') return 'accent';
  if (step.status === 'blocked' || step.status === 'deviated') return 'warning';
  return 'foreground';
}

function currentStep(steps: PlanProgressStep[]): PlanProgressStep | undefined {
  return steps.find(step => step.status === 'in_progress') ??
    steps.find(step => step.status === 'blocked' || step.status === 'deviated') ??
    steps.find(step => step.status === 'pending') ??
    steps[steps.length - 1];
}

function detailLines(step: PlanProgressStep, width: number): string[] {
  const meta = [stepKind(step)];
  if (step.files.length) meta.push(`文件 ${step.files.join(', ')}`);
  if (step.depends_on?.length) meta.push(`依赖 ${step.depends_on.join(', ')}`);
  if (step.acceptance) meta.push(`验收 ${step.acceptance}`);
  if (step.verification) meta.push('含验证命令');
  return [step.title, meta.join(' · ')].flatMap(text => textLines(text, width).map(line => line.spans.map(span => span.text).join(''))).slice(0, 2);
}

/** 常驻计划进度：折叠已完成、只展示当前与未完成步骤；Ctrl+L 由 App 打开全屏清单。 */
export function buildPlanProgressView(progress: PlanProgress, columns: number, terminalRows: number): {rows: number; lines: PanelLine[]} {
  const width = Math.max(1, columns);
  const maxPanel = terminalRows >= 32 ? 9 : terminalRows >= 24 ? 7 : 5;
  const steps = progress.steps;
  const open = steps.filter(step => !isClosed(step));
  const closed = steps.length - open.length;
  const skipped = steps.filter(step => step.status === 'skipped').length;
  const current = currentStep(steps);
  const allDone = open.length === 0;
  const header = progress.done === progress.total && progress.total > 0
    ? `▌ 计划 v${progress.plan.version} · ${progress.done}/${progress.total} 步 · 已完成`
    : `▌ 计划 v${progress.plan.version} · ${progress.done}/${progress.total} 步 · 当前 ${current?.step_id ?? '—'}`;
  const lines: PanelLine[] = [{text: fitText(`${header}${width >= 50 ? ' · Ctrl+L 全部' : ''}`, width), tone: 'accent'}];
  if (closed > 0) {
    lines.push({text: fitText(`✓ 已完成 ${closed} 步${skipped ? `（含跳过 ${skipped} 步）` : ''}`, width), tone: 'success'});
  }
  if (allDone) {
    lines.push({text: fitText('✓ 全部步骤已完成 · Ctrl+L 查看全部', width), tone: 'secondary'});
    return {rows: lines.length, lines};
  }
  if (current) for (const text of detailLines(current, Math.max(1, width - 2))) {
    lines.push({text: fitText(`  ${text}`, width), tone: current.status === 'in_progress' ? 'accent' : 'foreground'});
  }
  const remainingForSteps = Math.max(1, maxPanel - lines.length);
  const shownCount = open.length <= remainingForSteps ? open.length : remainingForSteps - 1;
  const shown = open.slice(0, Math.max(0, shownCount));
  for (const step of shown) {
    lines.push({text: fitText(` ${statusIcon(step)} ${step.step_id} ${step.title} · ${stepKind(step)}${stepFiles(step)}`, width), tone: stepTone(step)});
  }
  const hidden = open.length - shown.length;
  if (hidden > 0) lines.push({text: fitText(`… 还有 ${hidden} 步 · Ctrl+L 查看全部`, width), tone: 'secondary'});
  return {rows: lines.length, lines};
}

function renderLine(text: string, tone: Tone, palette: Palette): string {
  if (tone === 'accent') return palette.accent;
  if (tone === 'success') return palette.success;
  if (tone === 'warning') return palette.warning;
  if (tone === 'secondary') return palette.secondary;
  return palette.foreground;
}

/** 只读计划进度面板：显示折叠后的当前窗口，不接收键盘输入。 */
export function PlanProgressPanel({rows, lines, palette}: {rows: number; lines: PanelLine[]; palette: Palette}) {
  return <Box flexDirection="column" height={rows} flexShrink={0}>
    {lines.map((line, index) => <Text key={index} color={renderLine(line.text, line.tone, palette)}>{line.text}</Text>)}
  </Box>;
}

/** 全屏只读清单：所有步骤与完整元数据，按文本行换行展示。 */
export function planListLines(progress: PlanProgress, columns: number): ReadingLine[] {
  const width = Math.max(1, columns);
  const lines: ReadingLine[] = [];
  lines.push(...textLines(`计划执行 · v${progress.plan.version} · ${progress.done}/${progress.total} 步`, width, 'heading'));
  lines.push(...textLines(progress.plan.objective, width));
  lines.push(...textLines('↑↓ 滚动 · PgUp/PgDn 翻页 · Home/End 首尾 · Esc 返回', width, 'notice'));
  for (const step of progress.steps) {
    const current = step.status === 'in_progress';
    const label = {pending: '待开始', in_progress: '进行中', blocked: '受阻', done: '已完成', deviated: '偏离', skipped: '跳过'}[step.status];
    lines.push(...textLines(`${current ? '› ' : '  '}${statusIcon(step)} ${step.step_id} ${step.title} · ${label} · ${stepKind(step)}`, width, current ? 'label' : undefined));
    const meta = [];
    if (step.files.length) meta.push(`文件 ${step.files.join(', ')}`);
    if (step.depends_on?.length) meta.push(`依赖 ${step.depends_on.join(', ')}`);
    if (step.acceptance) meta.push(`验收 ${step.acceptance}`);
    if (step.verification) meta.push('含验证命令');
    if (step.evidence_count) meta.push(`证据 ${step.evidence_count}`);
    if (meta.length) lines.push(...textLines(`    ${meta.join(' · ')}`, width, 'notice'));
  }
  return lines;
}
