import type {ReadingLine} from './markdown.js';
import type {Entry} from './view-state.js';

export type LocateItem = {line: number; label: string; detail: string; highlight?: string};

export function lineText(line: ReadingLine): string {
  return line.spans.map(span => span.text).join('').trimEnd();
}

export function firstLineByEntry(lines: readonly ReadingLine[]): Map<number, number> {
  const result = new Map<number, number>();
  lines.forEach((line, index) => {
    if (line.entryId === undefined || result.has(line.entryId)) return;
    result.set(line.entryId, index);
  });
  return result;
}

const clip = (text: string, limit: number): string => {
  const compact = text.replace(/\s+/g, ' ').trim();
  return compact.length > limit ? `${compact.slice(0, limit)}…` : compact;
};

export function searchTranscript(lines: readonly ReadingLine[], query: string, limit = 200): LocateItem[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  const items: LocateItem[] = [];
  for (let index = 0; index < lines.length && items.length < limit; index += 1) {
    const text = lineText(lines[index]!);
    if (!text) continue;
    const at = text.toLowerCase().indexOf(needle);
    if (at < 0) continue;
    const before = [...text.slice(0, at)].length;
    const start = Math.max(0, before - 24);
    items.push({line: index, label: clip(text.slice(start, start + 140), 140), detail: `第 ${index + 1} 行 · 关键词位置 ${at + 1}`});
  }
  return items;
}

export function roundItems(entries: readonly Entry[], lines: readonly ReadingLine[]): LocateItem[] {
  const first = firstLineByEntry(lines);
  const rounds = entries.filter(entry => entry.kind === 'task');
  return rounds.flatMap((entry, index) => {
    const line = first.get(entry.id);
    if (line === undefined) return [];
    const status = entry.runStatus ?? '进行中';
    return [{line, label: `第 ${index + 1} 轮 · ${status} · ${clip(entry.text, 90)}`, detail: `第 ${line + 1} 行`}];
  });
}

export function errorItems(entries: readonly Entry[], lines: readonly ReadingLine[]): LocateItem[] {
  const first = firstLineByEntry(lines);
  const items: LocateItem[] = [];
  for (const entry of entries) {
    if (entry.kind === 'tool' && entry.ok === false) {
      const line = first.get(entry.id);
      if (line === undefined) continue;
      const output = (entry.output ?? '').trim().split('\n').find(Boolean) ?? '';
      const summary = output || (entry.call ? JSON.stringify(entry.call.args) : '');
      items.push({line, label: `工具错误 · ${entry.call?.name ?? 'tool'} · ${clip(summary, 90)}`, detail: `第 ${line + 1} 行`});
    } else if (entry.kind === 'task' && entry.runStatus === 'error') {
      const line = first.get(entry.id);
      if (line === undefined) continue;
      items.push({line, label: `运行 error · ${clip(entry.text, 90)}`, detail: `第 ${line + 1} 行`});
    }
  }
  return items.sort((a, b) => a.line - b.line);
}
