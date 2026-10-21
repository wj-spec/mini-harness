import {Transform, type TransformCallback} from 'node:stream';

type TerminalSource = NodeJS.ReadableStream & {isTTY?: boolean; setRawMode?: (enabled: boolean) => unknown; ref?: () => unknown; unref?: () => unknown};

const sgrStart = [0x1b, 0x5b, 0x3c];
const pasteStart = [0x1b, 0x5b, 0x32, 0x30, 0x30, 0x7e];
const pasteEnd = [0x1b, 0x5b, 0x32, 0x30, 0x31, 0x7e];
const maximumSequenceLength = 64;
const prefixDelayMs = 10;

const isPrefixOf = (prefix: number[], sequence: number[]) => prefix.length <= sequence.length && prefix.every((byte, index) => byte === sequence[index]);

// 鼠标协议完整后再交给 Ink，避免它的 Esc 超时把分片当文字。
class TerminalInput extends Transform {
  readonly isTTY: boolean;
  private prefix: number[] = [];
  private mouse: number[] | null = null;
  private discarding = false;
  private pasting = false;
  private pasteEndPosition = 0;
  private prefixTimer: NodeJS.Timeout | undefined;
  private readonly onSourceError = (error: Error) => this.destroy(error);

  constructor(private readonly source: TerminalSource) {
    super();
    this.isTTY = source.isTTY === true;
    source.on('error', this.onSourceError);
    source.pipe(this);
  }

  setRawMode(enabled: boolean): this {this.source.setRawMode?.(enabled); return this;}
  ref(): this {this.source.ref?.(); return this;}
  unref(): this {this.source.unref?.(); return this;}
  dispose(): void {this.destroy();}

  private clearPrefixTimer(): void {
    if (this.prefixTimer) clearTimeout(this.prefixTimer);
    this.prefixTimer = undefined;
  }

  override _transform(chunk: Buffer, _encoding: BufferEncoding, callback: TransformCallback): void {
    this.clearPrefixTimer();
    const output = Buffer.allocUnsafe(chunk.length + maximumSequenceLength);
    let length = 0;
    for (const byte of chunk) {
      if (this.pasting) {
        output[length++] = byte;
        this.pasteEndPosition = byte === pasteEnd[this.pasteEndPosition] ? this.pasteEndPosition + 1 : byte === 0x1b ? 1 : 0;
        if (this.pasteEndPosition === pasteEnd.length) {
          this.pasting = false;
          this.pasteEndPosition = 0;
        }
        continue;
      }
      if (this.mouse || this.discarding) {
        if (byte === 0x1b) {
          this.mouse = null;
          this.discarding = false;
          this.prefix = [byte];
        } else if (byte >= 0x40 && byte <= 0x7e) {
          if (this.mouse) {
            for (const held of this.mouse) output[length++] = held;
            output[length++] = byte;
          }
          this.mouse = null;
          this.discarding = false;
        } else if (byte >= 0x20 && byte <= 0x3f) {
          if (this.mouse && this.mouse.length < maximumSequenceLength) this.mouse.push(byte);
          else {
            this.mouse = null;
            this.discarding = true;
          }
        } else {
          this.mouse = null;
          this.discarding = false;
          output[length++] = byte;
        }
        continue;
      }
      if (this.prefix.length === 0) {
        if (byte === 0x1b) this.prefix.push(byte);
        else output[length++] = byte;
        continue;
      }
      this.prefix.push(byte);
      if (isPrefixOf(this.prefix, sgrStart)) {
        if (this.prefix.length === sgrStart.length) {
          this.mouse = this.prefix;
          this.prefix = [];
        }
      } else if (isPrefixOf(this.prefix, pasteStart)) {
        if (this.prefix.length === pasteStart.length) {
          for (const held of this.prefix) output[length++] = held;
          this.prefix = [];
          this.pasting = true;
        }
      } else {
        const trailingEscape = byte === 0x1b;
        for (const held of trailingEscape ? this.prefix.slice(0, -1) : this.prefix) output[length++] = held;
        this.prefix = trailingEscape ? [0x1b] : [];
      }
    }
    if (length > 0) this.push(output.subarray(0, length));
    if (this.prefix.length > 0) {
      this.prefixTimer = setTimeout(() => {
        this.prefixTimer = undefined;
        this.push(Buffer.from(this.prefix));
        this.prefix = [];
      }, prefixDelayMs);
      this.prefixTimer.unref();
    }
    callback();
  }

  override _flush(callback: TransformCallback): void {
    this.clearPrefixTimer();
    if (this.prefix.length > 0) this.push(Buffer.from(this.prefix));
    this.prefix = [];
    this.mouse = null;
    this.discarding = false;
    callback();
  }

  override _destroy(error: Error | null, callback: (error: Error | null) => void): void {
    this.clearPrefixTimer();
    this.source.unpipe(this);
    this.source.removeListener('error', this.onSourceError);
    this.prefix = [];
    this.mouse = null;
    callback(error);
  }
}

export function createTerminalInput(source: TerminalSource): TerminalInput {
  return new TerminalInput(source);
}
