import {useEffect, useRef} from 'react';
import {useStdin, useStdout} from 'ink';
import {TerminalMouseParser} from './mouse.js';

type TranscriptMouseOptions = {
  onScroll: (delta: number) => void;
  top: number;
  bottom: number;
  left: number;
  right: number;
  enabled?: boolean;
};

const enableMouse = '\u001b[?1000h\u001b[?1006h';
const disableMouse = '\u001b[?1006l\u001b[?1000l';

export function useTranscriptMouse(options: TranscriptMouseOptions): void {
  const {stdin, setRawMode, isRawModeSupported} = useStdin();
  const {stdout} = useStdout();
  const current = useRef(options);
  current.current = options;
  const enabled = options.enabled !== false;

  useEffect(() => {
    if (!enabled || !isRawModeSupported || !('isTTY' in stdin) || stdin.isTTY !== true) return;
    const outputIsTTY = 'isTTY' in stdout && stdout.isTTY === true;
    const parser = new TerminalMouseParser();
    const handleData = (chunk: string | Uint8Array) => {
      for (const event of parser.push(chunk)) {
        const {top, bottom, left, right, onScroll} = current.current;
        if (event.row >= top && event.row <= bottom && event.column >= left && event.column <= right) onScroll(event.delta);
      }
    };
    setRawMode(true);
    stdin.on('data', handleData);
    // 1000 不订阅移动事件，保留 Shift + 拖选的终端行为。
    if (outputIsTTY) stdout.write(enableMouse);
    return () => {
      stdin.removeListener('data', handleData);
      if (outputIsTTY) stdout.write(disableMouse);
      setRawMode(false);
    };
  }, [enabled, stdin, stdout, isRawModeSupported, setRawMode]);
}
