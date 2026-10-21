export function contextSnapshot() {
  return {
    schema_version: 1 as const, snapshot_id: 'snapshot-1', request_id: 'request-1', run_id: 'agent-run-1', model: 'fake',
    ts: 1_792_544_000.125, step: 1, attempt: 0, phase: 'request' as 'request' | 'before_compaction' | 'after_compaction',
    reason: 'model_call', estimated: true as const, count_method: 'heuristic_cjk_v1' as const,
    context_window: 1000, input_capacity: 900, total_tokens: 100,
    sections: {system: 20, memory: 0, tools: 30, history: 50}, caps: {system: 80, memory: 0, tools: 160, history: 710},
    reserves: {output: 80, scratch: 20}, remaining_tokens: 800, over_budget: false,
    blocks: [
      {id: 'system-base', section: 'system' as const, kind: 'base_prompt', label: '基础指令', source: 'system_prompt', tokens: 20, chars: 80, content_hash: 'a'.repeat(64)},
      {id: 'tool-read', section: 'tools' as const, kind: 'tool_schema', label: 'read_file', source: 'tools/read_file', tokens: 30, chars: 120, content_hash: 'b'.repeat(64)},
      {id: 'history-1', section: 'history' as const, kind: 'tool_result', label: 'read_file 返回', source: 'README.md', tokens: 50, chars: 200, content_hash: 'c'.repeat(64)},
    ],
    delta_tokens: 100, section_deltas: {system: 20, memory: 0, tools: 30, history: 50},
  };
}
