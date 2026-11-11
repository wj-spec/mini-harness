import React from 'react';
import {Box, Text} from 'ink';
import type {PlanReviewResult, PlanStepInfo} from './protocol.js';
import {fitText} from './markdown.js';
import type {Palette} from './theme.js';

function stepLine(step: PlanStepInfo, index: number): string {
  const kind = step.kind + (step.risk !== 'read' ? `/${step.risk}` : '');
  const files = step.files.length ? ` · ${step.files.slice(0, 2).join(', ')}${step.files.length > 2 ? ' …' : ''}` : '';
  return `${index + 1}. ○ ${step.step_id} ${step.title} · ${kind}${files}`;
}

function stepDetail(step: PlanStepInfo): string {
  const parts = [`${step.kind}/${step.risk}`];
  if (step.files.length) parts.push(`文件 ${step.files.join(', ')}`);
  if (step.depends_on.length) parts.push(`依赖 ${step.depends_on.join(', ')}`);
  if (step.acceptance) parts.push(`验收 ${step.acceptance}`);
  return parts.join(' · ');
}

/** 输入框位置的计划审阅栏：Enter 直接执行，↑↓ 选步骤后可修改，b 看正文。 */
export function PlanReviewBar({card, steps, selected, columns, rows, compact, draft, palette}: {
  card: PlanReviewResult; steps: PlanStepInfo[]; selected: number; columns: number; rows: number; compact: boolean; draft: string; palette: Palette;
}) {
  const width = Math.max(4, columns - 2);
  const limit = Math.max(1, rows - (compact ? 2 : 3));
  const start = Math.max(0, Math.min(selected - Math.floor(limit / 2), steps.length - limit));
  const visible = steps.slice(start, start + limit);
  const step = steps[selected];
  const title = `▌ 计划 v${card.target?.plan_version ?? '?'} · ${card.summary?.objective ?? '待审阅计划'} · ${steps.length} 步`;
  const kept = draft.trim() ? `草稿已保留（${[...draft.trim()].length} 字） · ` : '';
  const hint = kept + (compact
    ? 'Enter 执行 · ↑↓ 步骤 · Esc 稍后'
    : 'Enter 执行 · ↑↓ 选步骤 · b 正文 · m 改标题 · k 改类型 · f 文件 · p 依赖 · r 修订 · Esc 稍后');
  return <Box flexDirection="column" width={Math.max(1, columns)} height={rows} paddingX={1} flexShrink={0} backgroundColor={palette.focus}>
    <Text bold color={palette.accent}>{fitText(title, width)}</Text>
    {visible.map((item, offset) => {
      const index = start + offset;
      const active = index === selected;
      return <Text key={item.step_id} bold={active} color={active ? palette.accent : palette.foreground}>
        {fitText(`${active ? '›' : ' '} ${stepLine(item, index)}`, width)}
      </Text>;
    })}
    {!compact && step ? <Text color={palette.secondary}>{fitText(`   ${stepDetail(step)}`, width)}</Text> : null}
    <Text color={palette.secondary}>{fitText(hint, width)}</Text>
  </Box>;
}
