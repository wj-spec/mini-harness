export type WheelMouseEvent = {type: 'wheel'; delta: number; column: number; row: number};
export type ButtonMouseEvent = {type: 'press' | 'drag' | 'release'; button: number; column: number; row: number};
export type MouseEvent = WheelMouseEvent | ButtonMouseEvent;

const pasteEnd = '\u001b[201~';
const maximumSequenceLength = 64;
const sgrReport = /^\u001b\[<(\d{1,3});(\d{1,6});(\d{1,6})([Mm])$/;

function decode(raw: number, column: number, row: number, final: 'M' | 'm'): MouseEvent | null {
  if (column < 1 || row < 1) return null;
  if ((raw & ~31) === 64 && (raw & 3) <= 1) return {type: 'wheel', delta: (raw & 1) === 0 ? -3 : 3, column, row};
  if (raw & 64) return null;
  const button = raw & 3;
  if (final === 'm') return {type: 'release', button: button === 3 ? 0 : button, column, row};
  if (button > 2) return null;
  if (raw & 32) return {type: 'drag', button, column, row};
  return {type: 'press', button, column, row};
}

// 只读终端协议；普通文字仍由 Ink 处理。
export class TerminalMouseParser {
  private sequence = '';
  private discarding = false;
  private pasting = false;
  private pasteEndPosition = 0;
  private x10 = false;
  private x10Bytes: number[] = [];

  push(chunk: string | Uint8Array): MouseEvent[] {
    const events: MouseEvent[] = [];
    const text = typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString('utf8');
    for (const char of text) {
      const code = char.charCodeAt(0);
      if (this.x10) {
        this.x10Bytes.push(code);
        if (this.x10Bytes.length === 3) {
          const [buttonByte, columnByte, rowByte] = this.x10Bytes;
          this.x10 = false;
          this.x10Bytes = [];
          const raw = buttonByte! - 32;
          const event = decode(raw, columnByte! - 32, rowByte! - 32, (raw & 3) === 3 ? 'm' : 'M');
          if (event) events.push(event);
        }
        continue;
      }
      if (this.pasting) {
        this.pasteEndPosition = char === pasteEnd[this.pasteEndPosition] ? this.pasteEndPosition + 1 : char === '\u001b' ? 1 : 0;
        if (this.pasteEndPosition === pasteEnd.length) {
          this.pasting = false;
          this.pasteEndPosition = 0;
        }
        continue;
      }
      if (char === '\u001b') {
        this.sequence = char;
        this.discarding = false;
        continue;
      }
      if (!this.sequence && !this.discarding) continue;
      if (this.sequence === '\u001b') {
        this.sequence = char === '[' ? this.sequence + char : '';
        continue;
      }
      if (code >= 0x40 && code <= 0x7e) {
        const sequence = this.discarding ? '' : this.sequence + char;
        this.sequence = '';
        this.discarding = false;
        if (sequence === '\u001b[200~') {
          this.pasting = true;
          continue;
        }
        if (sequence === '\u001b[M') {
          this.x10 = true;
          this.x10Bytes = [];
          continue;
        }
        const match = sgrReport.exec(sequence);
        if (!match) continue;
        const raw = Number(match[1]);
        if (match[4] === 'm' && (raw & 3) === 3) continue;
        const event = decode(raw, Number(match[2]), Number(match[3]), match[4] === 'm' ? 'm' : 'M');
        if (event) events.push(event);
      } else if (code >= 0x20 && code <= 0x3f) {
        if (!this.discarding && this.sequence.length < maximumSequenceLength) this.sequence += char;
        else {
          this.sequence = '';
          this.discarding = true;
        }
      } else {
        this.sequence = '';
        this.discarding = false;
      }
    }
    return events;
  }
}
