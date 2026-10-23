import stringWidth from 'string-width';
import type {ReadingLine} from './markdown.js';

export type SelectionPoint = {line: number; cell: number};
export type Selection = {anchor: SelectionPoint; head: SelectionPoint};
export type SelectionCells = {start: number; end: number};

export function normalizeSelection(selection: Selection): {start: SelectionPoint; end: SelectionPoint} {
  const {anchor, head} = selection;
  const ordered = anchor.line < head.line || (anchor.line === head.line && anchor.cell <= head.cell);
  return ordered ? {start: anchor, end: head} : {start: head, end: anchor};
}

export function lineText(line: ReadingLine): string {
  return line.spans.map(span => span.text).join('');
}

/** 按终端单元格切分文本；宽字符被边界切到时保留整字。 */
export function sliceCells(text: string, from: number, to: number): string {
  let cell = 0;
  let result = '';
  for (const char of Array.from(text)) {
    const width = stringWidth(char);
    const start = cell;
    cell += width;
    if (start >= to) break;
    if (start + width <= from) continue;
    result += char;
  }
  return result;
}

/** 把一段文本按选区切成 前 / 选中 / 后 三段，宽字符只归入一段。 */
export function splitCells(text: string, start: number, end: number): {before: string; middle: string; after: string} {
  let cell = 0;
  let before = '';
  let middle = '';
  let after = '';
  for (const char of Array.from(text)) {
    const width = stringWidth(char);
    const charStart = cell;
    const charEnd = cell + width;
    cell = charEnd;
    if (charEnd <= start) before += char;
    else if (charStart < end) middle += char;
    else after += char;
  }
  return {before, middle, after};
}

/** 拖选出的纯文本：跨行拼接，行间保留换行。 */
export function selectedText(lines: readonly ReadingLine[], selection: Selection): string {
  const {start, end} = normalizeSelection(selection);
  if (start.line === end.line && start.cell === end.cell) return '';
  const parts: string[] = [];
  for (let index = start.line; index <= Math.min(end.line, lines.length - 1); index += 1) {
    const line = lines[index];
    if (!line) continue;
    const from = index === start.line ? start.cell : 0;
    const to = index === end.line ? end.cell : Number.POSITIVE_INFINITY;
    parts.push(sliceCells(lineText(line), from, to));
  }
  return parts.join('\n').replace(/[ \t]+$/, '');
}

/** 某个绝对行需要高亮的单元格范围；不在选区内返回 null。 */
export function selectionCells(selection: Selection | null | undefined, line: number): SelectionCells | null {
  if (!selection) return null;
  const {start, end} = normalizeSelection(selection);
  if (line < start.line || line > end.line) return null;
  return {
    start: line === start.line ? start.cell : 0,
    end: line === end.line ? end.cell : Number.POSITIVE_INFINITY,
  };
}
