import React from 'react';
import {Text} from 'ink';
import stringWidth from 'string-width';
import type {ContextColor, Palette, SemanticTone, SurfaceName} from './theme.js';
import {splitCells, type SelectionCells} from './selection.js';

export type Span = {text: string; bold?: boolean; code?: boolean; contextSection?: ContextColor};
export type ReadingLine = {spans: Span[]; kind?: 'heading' | 'code' | 'label' | 'tool' | 'error' | 'notice'; tone?: SemanticTone; contextSection?: ContextColor; surface?: SurfaceName; selected?: boolean; entryId?: number};
const graphemes = new Intl.Segmenter(undefined, {granularity: 'grapheme'});

export function visibleText(text: string): string {
  return text.replace(/\x1b/g, '␛').replace(/\r/g, '').replace(/\t/g, '    ')
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, character => `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`);
}

export function fitText(text: string, columns: number, middle = false): string {
  const safe = visibleText(text).replace(/\n/g, ' ');
  if (stringWidth(safe) <= columns) return safe;
  if (columns < 2) return columns === 1 ? '…' : '';
  const symbols = [...graphemes.segment(safe)].map(item => item.segment);
  let start = '', end = '';
  const leftWidth = middle ? Math.ceil((columns - 1) / 2) : columns - 1;
  while (symbols.length && stringWidth(start + symbols[0]) <= leftWidth) start += symbols.shift();
  if (middle) while (symbols.length && stringWidth(start + end + symbols.at(-1)) <= columns - 1) end = symbols.pop() + end;
  return `${start}…${end}`;
}

export function inlineSpans(text: string): Span[] {
  const spans: Span[] = [];
  const pattern = /\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;
  let from = 0;
  for (const match of text.matchAll(pattern)) {
    if ((match.index ?? 0) > from) spans.push({text: text.slice(from, match.index)});
    if (match[1]) spans.push({text: match[1], bold: true});
    else if (match[2]) spans.push({text: match[2], code: true});
    else spans.push({text: `${match[3]} (${match[4]})`});
    from = (match.index ?? 0) + match[0].length;
  }
  if (from < text.length) spans.push({text: text.slice(from)});
  return spans.length ? spans : [{text: ''}];
}

export function wrapSpans(spans: Span[], columns: number): Span[][] {
  const maximum = Math.max(1, columns);
  const lines: Span[][] = [[]];
  let width = 0;
  for (const span of spans) for (const {segment} of graphemes.segment(visibleText(span.text))) {
    const size = stringWidth(segment);
    if (width + size > maximum && width) {lines.push([]); width = 0;}
    if (size > maximum) continue;
    const current = lines.at(-1)!;
    const last = current.at(-1);
    if (last && last.bold === span.bold && last.code === span.code && last.contextSection === span.contextSection) last.text += segment;
    else current.push({...span, text: segment});
    width += size;
  }
  return lines;
}

export function textLines(text: string, columns: number, kind?: ReadingLine['kind']): ReadingLine[] {
  return visibleText(text).split('\n').flatMap(line => wrapSpans([{text: line}], columns).map(spans => ({spans, kind})));
}

export function markdownLines(markdown: string, columns: number): ReadingLine[] {
  const lines: ReadingLine[] = [];
  let code = false;
  for (const raw of visibleText(markdown).split('\n')) {
    const fence = raw.match(/^\s*(```+|~~~+)\s*(.*)$/);
    if (fence) {
      lines.push(...textLines(code ? '' : `code · ${fence[2] || 'text'}`, columns, 'code').map(line => ({...line, surface: 'code' as const})));
      code = !code;
      continue;
    }
    if (code) {
      for (const spans of wrapSpans([{text: raw}], columns)) lines.push({spans, kind: 'code', surface: 'code'});
      continue;
    }
    const heading = raw.match(/^\s{0,3}#{1,6}\s+(.+?)(?:\s+#+)?$/);
    const text = heading ? heading[1]! : raw.replace(/^\s*[-*+]\s+/, '• ').replace(/^>\s?/, '│ ');
    const spans = inlineSpans(text);
    for (const wrapped of wrapSpans(spans, columns)) lines.push({spans: wrapped, kind: heading ? 'heading' : undefined});
  }
  return lines;
}

export function ReadingText({line, palette, columns, select}: {line: ReadingLine; palette: Palette; columns?: number; select?: SelectionCells}) {
  const color = line.contextSection ? palette.contextColors[line.contextSection] : line.tone ? palette[line.tone] : line.kind === 'error' ? palette.error : line.kind === 'notice' ? palette.warning :
    line.kind === 'heading' || line.kind === 'label' || line.selected ? palette.accent : palette.foreground;
  const width = line.spans.reduce((total, span) => total + stringWidth(span.text), 0);
  const padding = Math.max(0, (columns ?? Math.max(1, width)) - width);
  const surface = line.kind === 'code' ? 'code' : line.surface;
  return <Text color={color} backgroundColor={surface ? palette.surfaces[surface] : palette.background} bold={line.kind === 'heading' || line.kind === 'label' || line.selected}>
    <ReadingSpans spans={line.spans} palette={palette} select={select}/>
    {' '.repeat(padding)}
  </Text>;
}

export function ReadingSpans({spans, palette, select}: {spans: readonly Span[]; palette: Palette; select?: SelectionCells}) {
  let cell = 0;
  return <>{spans.map((span, index) => {
    const width = stringWidth(span.text);
    const spanStart = cell;
    cell += width;
    const bold = span.bold;
    const color = span.contextSection ? palette.contextColors[span.contextSection] : span.code ? palette.accent : undefined;
    if (!select || spanStart >= select.end || spanStart + width <= select.start) {
      return <Text key={index} bold={bold} color={color}>{span.text}</Text>;
    }
    const {before, middle, after} = splitCells(span.text, select.start - spanStart, select.end - spanStart);
    return <Text key={index} bold={bold} color={color}>{before}{middle ? <Text inverse>{middle}</Text> : null}{after}</Text>;
  })}</>;
}
