import assert from 'node:assert/strict';
import test from 'node:test';
import stringWidth from 'string-width';
import {contextBar, contextLines, contextSummary} from '../src/context-view.js';
import {contextSnapshot} from './context-fixture.js';

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
  assert.equal(contextBar(snapshot, 18).replace(/[·\[\]]/g, '').length, 2);
  assert.equal(contextBar({...snapshot, input_capacity: 0}, 5).length, 7);
  assert.equal(contextBar({...snapshot, input_capacity: 1}, 5).length, 7);
  assert.equal(contextBar({...snapshot, total_tokens: 0, sections: {system: 0, memory: 0, tools: 0, history: 0}, input_capacity: 0}, 5), '[·····]');
});
