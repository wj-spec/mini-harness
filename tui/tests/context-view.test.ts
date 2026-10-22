import assert from 'node:assert/strict';
import test from 'node:test';
import stringWidth from 'string-width';
import {contextBar, contextBarSpans, contextLines, contextSummary, needsContextComposition} from '../src/context-view.js';
import {contextSnapshot, loadedContextSnapshot} from './context-fixture.js';

test('loaded context identifies a session load without inventing a model request', () => {
  const text = contextLines([loadedContextSnapshot()], null, 75).map(line => line.spans.map(span => span.text).join('')).join('\n');
  assert.match(text, /基础上下文已装载/);
  assert.match(text, /尚未请求模型/);
  assert.match(text, /system 系统/);
  assert.match(text, /基础指令/);
  assert.doesNotMatch(text, /请求前|步骤 0|尝试 0|本轮趋势|request loaded/);
});

test('partition replay exposes budgets, compaction trends, and sources at narrow terminal widths', () => {
  const first = contextSnapshot();
  const second = {...first, snapshot_id: 'snapshot-2', phase: 'after_compaction' as const,
    caps: {...first.caps, history: 850, history_nominal: 710}, delta_tokens: -40,
    sections: {...first.sections, history: 10}, section_deltas: {system: 0, memory: 0, tools: 0, history: -40},
    blocks: first.blocks.map(block => block.section === 'history' ? {...block, tokens: 10} : block), total_tokens: 60, remaining_tokens: 840};
  for (const columns of [19, 27, 75, 82]) {
    const lines = contextLines([first, second], null, columns).map(line => line.spans.map(span => span.text).join(''));
    assert.ok(lines.every(line => stringWidth(line) <= columns));
    const text = lines.join('');
    assert.match(text, /压缩后/);
    assert.match(text, /Δ-40/);
    assert.match(text, /README.md/);
    assert.match(text, /名义额度 710/);
    assert.match(text, /有效额度 850/);
    assert.match(text, /输出 80/);
    assert.match(text, /安全 20/);
    assert.match(text, /输入剩余 840/);
    assert.match(text, /history-1/);
    assert.match(text, /hash/);
    assert.doesNotMatch(text, /任务 Tokens/);
  }
  assert.match(contextLines([first, second], first.snapshot_id, 75).map(line => line.spans.map(span => span.text).join('')).join('\n'), /快照 1\/2 · 回看/);
  assert.equal(contextSummary(first), 'S20 M0 T30 H50');
});

test('occupancy bar stays bounded for empty and exceeded input capacities', () => {
  const snapshot = contextSnapshot();
  assert.equal(contextBarSpans(snapshot, 18).filter(span => span.contextSection !== 'remaining').reduce((total, span) => total + span.text.length, 0), 2);
  assert.equal(contextBar({...snapshot, input_capacity: 0}, 5).length, 7);
  assert.equal(contextBar({...snapshot, input_capacity: 1}, 5).length, 7);
  assert.equal(contextBar({...snapshot, total_tokens: 0, sections: {system: 0, memory: 0, tools: 0, history: 0}, input_capacity: 0}, 5), '[━━━━━]');
});

test('partition lines preserve proportional semantic color spans and skip zero sections', () => {
  const snapshot = {...contextSnapshot(), input_capacity: 100, sections: {system: 20, memory: 10, tools: 30, history: 40}};
  const spans = contextBarSpans(snapshot, 10);
  assert.deepEqual(spans.filter(span => span.contextSection && span.contextSection !== 'remaining').map(span => [span.contextSection, span.text]),
    [['system', '━━'], ['memory', '━'], ['tools', '━━━'], ['history', '━━━━']]);
  assert.equal(spans.map(span => span.text).join(''), '[━━━━━━━━━━]');
  assert.ok(!contextBarSpans(contextSnapshot(), 90).some(span => span.contextSection === 'memory'));
  const lines = contextLines([snapshot], null, 75);
  assert.ok(lines.some(line => line.spans.filter(span => span.contextSection && span.text.includes('━')).length === 4));
  assert.ok(lines.some(line => line.spans.some(span => span.contextSection === 'tools' && /T.*30/.test(span.text))));
});

test('tiny loaded occupancy adds a clearly labeled composition without inflating the input bar', () => {
  const snapshot = {...loadedContextSnapshot(), context_window: 1_000_000, input_capacity: 960_000,
    total_tokens: 1500, sections: {system: 500, memory: 0, tools: 1000, history: 0}};
  assert.ok(needsContextComposition(snapshot, 28));
  assert.ok(!contextBarSpans(snapshot, 28).some(span => span.contextSection !== 'remaining' && span.text.includes('━')));
  for (const width of [1, 2, 7, 28, 75]) {
    const spans = contextBarSpans(snapshot, width, true);
    assert.equal(stringWidth(spans.map(span => span.text).join('')), width + 2);
  }
  const lines = contextLines([snapshot], null, 75);
  const composition = lines.find(line => line.spans.some(span => span.text.includes('已用构成')));
  assert.ok(composition?.spans.some(span => span.contextSection === 'system' && span.text.includes('━')));
  assert.ok(composition?.spans.some(span => span.contextSection === 'tools' && span.text.includes('━')));
  assert.match(lines.flatMap(line => line.spans.map(span => span.text)).join(''), /0.2%/);
  assert.ok(!needsContextComposition({...snapshot, total_tokens: 0, sections: {system: 0, memory: 0, tools: 0, history: 0}}, 28));
});

test('narrow and exceeded bars clamp width without giving each tiny section a fake cell', () => {
  const snapshot = {...contextSnapshot(), total_tokens: 1500,
    sections: {system: 1, memory: 1, tools: 1, history: 1497}, input_capacity: 900};
  for (const width of [1, 2, 3, 5]) {
    const spans = contextBarSpans(snapshot, width);
    assert.equal(spans.map(span => span.text).join(''), `[${'━'.repeat(width)}]`);
    assert.deepEqual(spans.filter(span => span.text.includes('━')).map(span => span.contextSection), ['history']);
  }
});
