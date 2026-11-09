import React, {useRef, useState} from 'react';
import {Text} from 'ink';
import {selectionCells, splitCells, sliceCells, normalizeSelection, type Selection} from '../selection.js';
import type {MouseEvent} from '../mouse.js';
import type {WrappedDiffLine} from './diff.js';
import {excerptText, type ExcerptPoint} from './draft-channel.js';

export type SelectedExcerpt = {start: ExcerptPoint; end: ExcerptPoint; text: string};
export function useExcerptSelection({body, lines, scroll, top, bottom, columns, identity, onSelect, onStart}: {
  body: string; lines: WrappedDiffLine[]; scroll: number; top: number; bottom: number; columns: number; identity: string;
  onSelect: (excerpt: SelectedExcerpt) => void; onStart?: () => void;
}) {
  const [range, setRange] = useState<{identity: string; columns: number; selection: Selection} | null>(null);
  const drag = useRef<{identity: string; columns: number; selection: Selection} | null>(null);
  const pointer = (event: MouseEvent): boolean => {
    if (event.type === 'wheel' || event.button !== 0) return false;
    const point = {line: Math.max(0, Math.min(lines.length - 1, scroll + Math.max(top, Math.min(bottom, event.row)) - top)),
      cell: Math.max(0, Math.min(columns - 2, event.column - 2))};
    if (event.type === 'press') {
      if (event.row < top || event.row > bottom || !lines.length) return false;
      onStart?.(); drag.current = {identity, columns, selection: {anchor: point, head: point}}; setRange(drag.current); return true;
    }
    const saved = drag.current;
    if (!saved) return false;
    if (saved.identity !== identity || saved.columns !== columns) {drag.current = null; setRange(null); return true;}
    saved.selection = {...saved.selection, head: point}; setRange({...saved});
    if (event.type === 'release') {
      drag.current = null;
      const normalized = normalizeSelection(saved.selection); const first = lines[normalized.start.line]; const last = lines[normalized.end.line];
      if (first && last) {
        const start = {line: first.sourceLine, offset: first.offset + splitCells(first.text, normalized.start.cell, Infinity).before.length};
        const end = {line: last.sourceLine, offset: last.offset + sliceCells(last.text, 0, normalized.end.cell).length};
        const text = excerptText(body, start, end);
        if (text?.trim()) onSelect({start, end, text});
      }
    }
    return true;
  };
  const renderLine = (line: WrappedDiffLine, index: number, color?: string) => {
    const text = line.text || ' ';
    const cells = selectionCells(range?.identity === identity && range.columns === columns ? range.selection : null, index);
    if (!cells) return <Text color={color}>{text}</Text>;
    const parts = splitCells(text, cells.start, cells.end);
    return <Text color={color}>{parts.before}<Text inverse>{parts.middle}</Text>{parts.after}</Text>;
  };
  return {pointer, renderLine};
}
