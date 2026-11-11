import React from 'react';
import {Box, Text} from 'ink';
import type {PlanProgress, PlanProgressStep} from './protocol.js';
import {fitText} from './markdown.js';
import type {Palette} from './theme.js';

const ICONS: Record<PlanProgressStep['status'], string> = {
  pending: '○', in_progress: '◐', blocked: '!', done: '✓', deviated: '!', skipped: '–',
};

function stepTone(step: PlanProgressStep, palette: Palette): string {
  if (step.status === 'done') return palette.success;
  if (step.status === 'in_progress') return palette.accent;
  if (step.status === 'blocked' || step.status === 'deviated') return palette.warning;
  return palette.secondary;
}

function currentIndex(steps: PlanProgressStep[]): number {
  const active = steps.findIndex(step => step.status === 'in_progress');
  if (active >= 0) return active;
  const pending = steps.findIndex(step => step.status === 'pending' || step.status === 'blocked');
  return pending >= 0 ? pending : Math.max(0, steps.length - 1);
}

function stepLabel(step: PlanProgressStep): string {
  const kind = step.kind + (step.risk !== 'read' ? `/${step.risk}` : '');
  const files = step.files.length ? ` · ${step.files.slice(0, 2).join(', ')}${step.files.length > 2 ? ' …' : ''}` : '';
  const evidence = step.status === 'done' && step.evidence_count > 0 ? ` · 证据 ${step.evidence_count}` : '';
  return `${ICONS[step.status]} ${step.step_id} ${step.title} · ${kind}${files}${evidence}`;
}

/** 只读计划进度：显示当前窗口内的步骤与状态，不抢输入、不改变执行。 */
export function PlanProgressPanel({progress, columns, rows, palette}: {
  progress: PlanProgress; columns: number; rows: number; palette: Palette;
}) {
  const limit = Math.max(1, rows - 1);
  const steps = progress.steps;
  const index = currentIndex(steps);
  const start = Math.max(0, Math.min(index - Math.floor(limit / 2), steps.length - limit));
  const visible = steps.slice(start, start + limit);
  const current = steps[index];
  const header = progress.done === progress.total && progress.total > 0
    ? `▌ 计划 v${progress.plan.version} · ${progress.done}/${progress.total} 步 · 已完成`
    : `▌ 计划 v${progress.plan.version} · ${progress.done}/${progress.total} 步 · 当前 ${current?.step_id ?? '—'}`;
  return <Box flexDirection="column" width={columns} height={rows} flexShrink={0}>
    <Text color={palette.accent} bold>{fitText(header, columns)}</Text>
    {visible.map(step => <Text key={step.step_id} color={stepTone(step, palette)}>{fitText(`  ${stepLabel(step)}`, columns)}</Text>)}
    {steps.length > visible.length && start + visible.length < steps.length
      ? <Text color={palette.secondary}>{fitText(`  ↓ 另有 ${steps.length - start - visible.length} 步`, columns)}</Text> : null}
  </Box>;
}
