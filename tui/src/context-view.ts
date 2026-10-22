import {CONTEXT_SECTIONS, type ContextSection, type ContextSnapshot} from './protocol.js';
import stringWidth from 'string-width';
import {fitText, textLines, wrapSpans, type ReadingLine, type Span} from './markdown.js';
import {compactNumber} from './workbench.js';

const sectionLabels: Record<ContextSection, string> = {system: 'system 系统', memory: 'memory 记忆', tools: 'tools 工具定义', history: 'history 历史'};
const sectionSymbols: Record<ContextSection, string> = {system: 'S', memory: 'M', tools: 'T', history: 'H'};
const phases = {loaded: '基础装载', request: '请求前', before_compaction: '压缩前', after_compaction: '压缩后'};

function signed(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toLocaleString('en-US')}`;
}

export function contextBar(snapshot: ContextSnapshot, width: number): string {
  return contextBarSpans(snapshot, width).map(span => span.text).join('');
}

export function contextBarSpans(snapshot: ContextSnapshot, width: number, composition = false): Span[] {
  const size = Math.max(1, Math.floor(width));
  const scale = Math.max(1, snapshot.total_tokens, composition ? 0 : snapshot.input_capacity);
  let total = 0, filled = 0;
  const spans: Span[] = [{text: '[', contextSection: 'remaining'}];
  for (const section of CONTEXT_SECTIONS) {
    total += Math.max(0, snapshot.sections[section]);
    const next = Math.min(size, Math.round(total / scale * size));
    if (next > filled) spans.push({text: '━'.repeat(next - filled), contextSection: section});
    filled = next;
  }
  if (filled < size) spans.push({text: '━'.repeat(size - filled), contextSection: 'remaining'});
  spans.push({text: ']', contextSection: 'remaining'});
  return spans;
}

export function needsContextComposition(snapshot: ContextSnapshot, width: number): boolean {
  const visible = new Set(contextBarSpans(snapshot, width).filter(span => span.text.includes('━')).map(span => span.contextSection));
  return CONTEXT_SECTIONS.some(section => snapshot.sections[section] > 0 && !visible.has(section));
}

export function contextSummary(snapshot: ContextSnapshot): string {
  return contextSummarySpans(snapshot).map(span => span.text).join('');
}

export function contextSummarySpans(snapshot: ContextSnapshot): Span[] {
  return CONTEXT_SECTIONS.flatMap((section, index) => [
    ...(index ? [{text: ' '}] : []),
    {text: `${sectionSymbols[section]}${compactNumber(snapshot.sections[section])}`, contextSection: section},
  ]);
}

export function contextLine(snapshot: ContextSnapshot, columns: number, composition = false, prefix = ''): ReadingLine {
  if (columns < 3) return {spans: [{text: '━'.repeat(Math.max(0, Math.floor(columns))), contextSection: 'remaining'}]};
  const label = fitText(prefix, columns - 3);
  return {spans: [...(label ? [{text: label}] : []), ...contextBarSpans(snapshot, Math.max(1, columns - stringWidth(label) - 2), composition)]};
}

function richLines(spans: Span[], columns: number): ReadingLine[] {
  return wrapSpans(spans, columns).map(spans => ({spans}));
}

export function contextLines(snapshots: readonly ContextSnapshot[], selectedId: string | null, columns: number): ReadingLine[] {
  const index = selectedId ? snapshots.findIndex(snapshot => snapshot.snapshot_id === selectedId) : -1;
  const chosen = index >= 0 ? index : snapshots.length - 1;
  const snapshot = snapshots[chosen];
  if (!snapshot) return [
    ...textLines('尚无分区快照', columns, 'heading'),
    ...textLines('模型请求发送前会记录估算分区；旧后端未提供快照时仍可正常使用。', columns),
    ...textLines('Ctrl+K 随时查看 · Esc 返回输入', columns),
  ];
  const loaded = snapshot.phase === 'loaded';
  const percentage = snapshot.input_capacity ? `${(snapshot.total_tokens / snapshot.input_capacity * 100).toFixed(1)}%` : '输入容量为 0';
  const lines = [
    ...textLines(loaded ? '基础上下文已装载 · 尚未请求模型' :
      `快照 ${chosen + 1}/${snapshots.length} · ${index < 0 ? '跟随最新' : '回看'} · 步骤 ${snapshot.step} · 尝试 ${snapshot.attempt} · ${phases[snapshot.phase]}`, columns, 'heading'),
    ...textLines(`估算输入 ${snapshot.total_tokens.toLocaleString('en-US')} / ${snapshot.input_capacity.toLocaleString('en-US')} tokens · ${percentage}${snapshot.over_budget ? ' · 超过输入容量' : ''}`, columns, snapshot.over_budget ? 'error' : 'label'),
    contextLine(snapshot, columns),
    ...(needsContextComposition(snapshot, columns - 2) ? [contextLine(snapshot, columns, true, '已用构成 ')] : []),
    ...richLines([...CONTEXT_SECTIONS.flatMap((section, index) => [
      ...(index ? [{text: ' · '}] : []), {text: `${sectionSymbols[section]}${compactNumber(snapshot.sections[section])} ${sectionLabels[section].split(' ').slice(1).join(' ')}`, contextSection: section},
    ]), {text: ' · 灰线 输入余量', contextSection: 'remaining'}], columns),
    ...textLines(`预留：输出 ${snapshot.reserves.output.toLocaleString('en-US')} · 安全 ${snapshot.reserves.scratch.toLocaleString('en-US')} tokens`, columns),
    ...textLines(`输入剩余 ${snapshot.remaining_tokens.toLocaleString('en-US')} · 总窗口 ${snapshot.context_window.toLocaleString('en-US')} tokens`, columns),
    ...textLines('分区用量 · 全部为估算 tokens', columns, 'heading'),
  ];
  for (const section of CONTEXT_SECTIONS) {
    const cap = section === 'history' ? snapshot.caps.history_nominal ?? snapshot.caps.history : snapshot.caps[section];
    lines.push(...textLines(`${sectionLabels[section]}  ${snapshot.sections[section].toLocaleString('en-US')}  Δ${signed(snapshot.section_deltas[section])}${cap === undefined ? '' : `  名义额度 ${cap.toLocaleString('en-US')}`}`, columns).map(line => ({...line, contextSection: section})));
  }
  const effective = snapshot.caps.history_nominal === undefined ? snapshot.caps.history_effective : snapshot.caps.history;
  if (effective !== undefined) lines.push(...textLines(`history 有效额度 ${effective.toLocaleString('en-US')}（可借用空闲预算）`, columns));
  if (!loaded) {
    lines.push(...textLines(`较上一快照 ${signed(snapshot.delta_tokens)} tokens · ←/→ 选快照 · l 跟随最新`, columns));
    lines.push(...textLines('本轮趋势 · 最多保留最近 100 个快照', columns, 'heading'));
    const trendWidth = Math.max(1, Math.min(28, columns - 31));
    snapshots.forEach((item, itemIndex) => {
      lines.push(...richLines([{text: `${itemIndex === chosen ? '›' : ' '} ${itemIndex + 1} 步${item.step} ${phases[item.phase]} `},
        ...contextBarSpans(item, trendWidth), {text: ` ${compactNumber(item.total_tokens)} Δ${signed(item.delta_tokens)}`}], columns));
      if (needsContextComposition(item, trendWidth)) lines.push(contextLine(item, columns, true, '  已用构成 '));
    });
  }
  lines.push(
    ...textLines(loaded ? '装载与计数' : '计数与请求', columns, 'heading'),
    ...textLines(`方法 ${snapshot.count_method} · 供应商 usage 单独记录，不用于精确分摊分区。`, columns),
    ...textLines(`模型 ${snapshot.model} · 原因 ${snapshot.reason}`, columns),
    ...textLines(loaded ? `装载快照 ${snapshot.snapshot_id}` : `run ${snapshot.run_id}\nrequest ${snapshot.request_id}\nsnapshot ${snapshot.snapshot_id}`, columns, 'code'),
    ...textLines('块来源明细 · 当前快照', columns, 'heading'),
  );
  for (const section of CONTEXT_SECTIONS) {
    lines.push(...textLines(sectionLabels[section], columns, 'label').map(line => ({...line, contextSection: section})));
    const blocks = snapshot.blocks.filter(block => block.section === section);
    if (!blocks.length) lines.push(...textLines('（无内容块）', columns));
    for (const block of blocks) lines.push(
      ...textLines(`${block.label} · ${block.tokens.toLocaleString('en-US')} tokens · ${block.chars.toLocaleString('en-US')} 字符`, columns),
      ...textLines(`来源 ${block.source}\n类型 ${block.kind} · ID ${block.id}\nhash ${block.content_hash}`, columns, 'code'),
    );
  }
  return lines;
}
