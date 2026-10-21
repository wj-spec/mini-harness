export type WheelEvent = {delta: number; column: number; row: number};

const pasteEnd = '\u001b[201~';
const maximumSequenceLength = 64;

// 只读终端协议；普通文字仍由 Ink 处理。
export class TerminalMouseParser {
  private sequence = '';
  private discarding = false;
  private pasting = false;
  private pasteEndPosition = 0;

  push(chunk: string | Uint8Array): WheelEvent[] {
    const events: WheelEvent[] = [];
    const text = typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString('utf8');
    for (const char of text) {
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
      const code = char.charCodeAt(0);
      if (code >= 0x40 && code <= 0x7e) {
        const sequence = this.discarding ? '' : this.sequence + char;
        this.sequence = '';
        this.discarding = false;
        if (sequence === '\u001b[200~') {
          this.pasting = true;
          continue;
        }
        const match = /^\u001b\[<(\d{1,3});(\d{1,6});(\d{1,6})M$/.exec(sequence);
        if (!match) continue;
        const button = Number(match[1]);
        const column = Number(match[2]);
        const row = Number(match[3]);
        if ((button & ~31) !== 64 || (button & 3) > 1 || column < 1 || row < 1) continue;
        events.push({delta: (button & 1) === 0 ? -3 : 3, column, row});
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
