import {useEffect, useRef} from 'react';
import {useStdin, useStdout} from 'ink';
import {TerminalMouseParser} from './mouse.js';

export type ScreenPoint = {row: number; column: number};
export type BodyBounds = {top: number; bottom: number; left: number; right: number};

type TranscriptMouseOptions = {
  onScroll: (delta: number) => void;
  top: number;
  bottom: number;
  left: number;
  right: number;
  body?: BodyBounds;
  onDragStart?: (point: ScreenPoint) => void;
  onDragMove?: (point: ScreenPoint) => void;
  onDragEnd?: (point: ScreenPoint) => void;
  enabled?: boolean;
};

// 1002：按钮按下时上报拖动，才能做应用内拖选。
const enableMouse = '\u001b[?1002h\u001b[?1006h';
const disableMouse = '\u001b[?1006l\u001b[?1002l\u001b[?1000l';

function inside(point: ScreenPoint, bounds: {top: number; bottom: number; left: number; right: number}): boolean {
  return point.row >= bounds.top && point.row <= bounds.bottom && point.column >= bounds.left && point.column <= bounds.right;
}

function clamp(point: ScreenPoint, bounds: BodyBounds): ScreenPoint {
  return {
    row: Math.min(Math.max(point.row, bounds.top), bounds.bottom),
    column: Math.min(Math.max(point.column, bounds.left), bounds.right),
  };
}

export function useTranscriptMouse(options: TranscriptMouseOptions): void {
  const {stdin, setRawMode, isRawModeSupported} = useStdin();
  const {stdout} = useStdout();
  const current = useRef(options);
  current.current = options;
  const selecting = useRef(false);
  const enabled = options.enabled !== false;

  useEffect(() => {
    if (!enabled || !isRawModeSupported || !('isTTY' in stdin) || stdin.isTTY !== true) return;
    const outputIsTTY = 'isTTY' in stdout && stdout.isTTY === true;
    const parser = new TerminalMouseParser();
    const handleData = (chunk: string | Uint8Array) => {
      for (const event of parser.push(chunk)) {
        const {top, bottom, left, right, body, onScroll, onDragStart, onDragMove, onDragEnd} = current.current;
        const point = {row: event.row, column: event.column};
        if (event.type === 'wheel') {
          if (selecting.current) continue;
          if (inside(point, {top, bottom, left, right})) onScroll(event.delta);
          continue;
        }
        if (!body || event.button !== 0) continue;
        if (event.type === 'press') {
          selecting.current = inside(point, body);
          if (selecting.current) onDragStart?.(clamp(point, body));
        } else if (event.type === 'drag' && selecting.current) {
          onDragMove?.(clamp(point, body));
        } else if (event.type === 'release' && selecting.current) {
          selecting.current = false;
          onDragEnd?.(clamp(point, body));
        }
      }
    };
    setRawMode(true);
    stdin.on('data', handleData);
    if (outputIsTTY) stdout.write(enableMouse);
    return () => {
      stdin.removeListener('data', handleData);
      if (outputIsTTY) stdout.write(disableMouse);
      setRawMode(false);
    };
  }, [enabled, stdin, stdout, isRawModeSupported, setRawMode]);
}
