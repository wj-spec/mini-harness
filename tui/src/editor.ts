import stringWidth from 'string-width';

const segmenter = new Intl.Segmenter(undefined, {granularity: 'grapheme'});

export function graphemes(text: string): string[] {
  return Array.from(segmenter.segment(text), part => part.segment);
}

export function normalizeInput(text: string): string {
  return text.replace(/\r\n?/g, '\n').replace(/\t/g, '    ').replace(/[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/g, '');
}

export function lineBoundary(text: string, cursor: number, edge: 'start' | 'end'): number {
  const chars = graphemes(text);
  let position = Math.min(cursor, chars.length);
  if (edge === 'start') {
    while (position > 0 && chars[position - 1] !== '\n') position -= 1;
  } else {
    while (position < chars.length && chars[position] !== '\n') position += 1;
  }
  return position;
}

export function editText(text: string, cursor: number, action: 'insert' | 'backspace' | 'delete' | 'clearLine', input = ''): {text: string; cursor: number} {
  const chars = graphemes(text);
  let position = Math.max(0, Math.min(cursor, chars.length));
  if (action === 'insert') {
    const inserted = graphemes(normalizeInput(input));
    chars.splice(position, 0, ...inserted);
    position += inserted.length;
  } else if (action === 'backspace' && position > 0) {
    chars.splice(--position, 1);
  } else if (action === 'delete') {
    chars.splice(position, 1);
  } else if (action === 'clearLine') {
    const start = lineBoundary(text, position, 'start');
    chars.splice(start, position - start);
    position = start;
  }
  const result = chars.join('');
  const prefixLength = graphemes(chars.slice(0, position).join('')).length;
  return {text: result, cursor: Math.min(prefixLength, graphemes(result).length)};
}

export function moveVertical(text: string, cursor: number, direction: -1 | 1): number {
  const chars = graphemes(text);
  const start = lineBoundary(text, cursor, 'start');
  const width = stringWidth(chars.slice(start, cursor).join(''));
  const end = lineBoundary(text, cursor, 'end');
  const nextStart = direction === -1 ? lineBoundary(text, Math.max(0, start - 1), 'start') : Math.min(chars.length, end + 1);
  if ((direction === -1 && start === 0) || (direction === 1 && end === chars.length)) return cursor;
  let next = nextStart;
  let column = 0;
  while (next < chars.length && chars[next] !== '\n' && column + stringWidth(chars[next]) <= width) {
    column += stringWidth(chars[next]);
    next += 1;
  }
  return next;
}

export type EditorRow = {text: string; start: number; end: number};

export function wrapEditor(text: string, cursor: number, columns: number): {rows: EditorRow[]; cursorRow: number; cursorColumn: number} {
  const chars = graphemes(text);
  const width = Math.max(2, columns);
  const position = Math.max(0, Math.min(cursor, chars.length));
  const rows: EditorRow[] = [];
  let line = '';
  let lineWidth = 0;
  let start = 0;
  let cursorRow = 0;
  let cursorColumn = 0;
  for (let index = 0; index < chars.length; index += 1) {
    const char = chars[index];
    const cells = stringWidth(char);
    if (char !== '\n' && lineWidth + cells > width) {
      rows.push({text: line, start, end: index});
      line = '';
      lineWidth = 0;
      start = index;
    }
    if (index === position) {
      cursorRow = rows.length;
      cursorColumn = lineWidth;
    }
    if (char === '\n') {
      rows.push({text: line, start, end: index});
      line = '';
      lineWidth = 0;
      start = index + 1;
    } else {
      line += char;
      lineWidth += cells;
    }
  }
  if (position === chars.length && lineWidth >= width) {
    rows.push({text: line, start, end: chars.length});
    line = '';
    lineWidth = 0;
    start = chars.length;
  }
  if (position === chars.length) {
    cursorRow = rows.length;
    cursorColumn = lineWidth;
  }
  rows.push({text: line, start, end: chars.length});
  return {rows, cursorRow, cursorColumn};
}

export function clipCells(text: string, columns: number): string {
  let result = '';
  let width = 0;
  for (const char of graphemes(text)) {
    const cells = stringWidth(char);
    if (width + cells > columns) break;
    result += char;
    width += cells;
  }
  return result;
}
