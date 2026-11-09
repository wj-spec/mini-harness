import stringWidth from 'string-width';
import {visibleText} from '../markdown.js';

/** 侧栏 diff 渲染：把 git unified diff 转成可着色、可按终端列宽换行的行。 */
export type DiffKind = 'meta' | 'hunk' | 'add' | 'del' | 'context' | 'note';
export type DiffLine = {kind: DiffKind; text: string};
export type WrappedDiffLine = DiffLine & {sourceLine: number; offset: number};

const graphemes: Intl.Segmenter = new Intl.Segmenter(undefined, {granularity: 'grapheme'});
const META_PREFIXES = ['diff --git ', 'index ', '--- ', '+++ ', 'new file mode ', 'deleted file mode ',
  'similarity index ', 'rename from ', 'rename to ', 'old mode ', 'new mode ', 'Binary files '];

export function parseDiff(text: string): DiffLine[] {
  if (!text.trim()) return [];
  return visibleText(text).split('\n').map(raw => {
    if (raw.startsWith('@@')) return {kind: 'hunk', text: raw};
    if (raw.startsWith('\\')) return {kind: 'note', text: raw};
    if (META_PREFIXES.some(prefix => raw.startsWith(prefix))) return {kind: 'meta', text: raw};
    if (raw.startsWith('+')) return {kind: 'add', text: raw};
    if (raw.startsWith('-')) return {kind: 'del', text: raw};
    return {kind: 'context', text: raw};
  });
}

export function diffStats(lines: DiffLine[]): {additions: number; deletions: number} {
  let additions = 0;
  let deletions = 0;
  for (const line of lines) {
    if (line.kind === 'add') additions += 1;
    if (line.kind === 'del') deletions += 1;
  }
  return {additions, deletions};
}

/** 按显示宽度换行；返回的行都不超过 columns 列，供可视窗口切片。 */
export function wrapDiffLines(lines: DiffLine[], columns: number): WrappedDiffLine[] {
  const width = Math.max(1, columns);
  const output: WrappedDiffLine[] = [];
  const widths = new Map<string, number>();
  for (const [sourceLine, line] of lines.entries()) {
    if (!line.text) {output.push({...line, sourceLine, offset: 0}); continue;}
    if (/^[\x20-\x7e]+$/u.test(line.text)) {
      for (let offset = 0; offset < line.text.length; offset += width) output.push({...line, text: line.text.slice(offset, offset + width), sourceLine, offset});
      continue;
    }
    let current = '';
    let currentWidth = 0;
    let offset = 0;
    let consumed = 0;
    for (const {segment} of graphemes.segment(line.text)) {
      let size = widths.get(segment);
      if (size === undefined) {
        size = stringWidth(segment);
        if (widths.size < 1024) widths.set(segment, size);
      }
      if (current && currentWidth + size > width) {
        output.push({kind: line.kind, text: current, sourceLine, offset});
        current = '';
        currentWidth = 0;
        offset = consumed;
      }
      if (size > width) {
        output.push({kind: line.kind, text: segment, sourceLine, offset: consumed});
        consumed += segment.length;
        offset = consumed;
        continue;
      }
      current += segment;
      currentWidth += size;
      consumed += segment.length;
    }
    if (current) output.push({kind: line.kind, text: current, sourceLine, offset});
  }
  return output;
}
