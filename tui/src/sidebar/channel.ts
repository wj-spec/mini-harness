import {createHash, randomUUID} from 'node:crypto';
import {mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';

export type SidebarOwner = {instanceId: string; parentPid: number};

export function validOwner(value: unknown): value is SidebarOwner {
  if (!value || typeof value !== 'object') return false;
  const owner = value as Partial<SidebarOwner>;
  return typeof owner.instanceId === 'string' && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/u.test(owner.instanceId) &&
    Number.isSafeInteger(owner.parentPid) && (owner.parentPid ?? 0) > 0;
}

export function sameOwner(first: unknown, second: SidebarOwner): boolean {
  return validOwner(first) && first.instanceId === second.instanceId && first.parentPid === second.parentPid;
}

export function createSidebarOwner(): SidebarOwner {
  return {instanceId: randomUUID(), parentPid: process.pid};
}

export function sidebarChannelPath(workspace: string, kind: 'signal' | 'link' | 'request' | 'reply', owner?: SidebarOwner): string {
  if (owner && !validOwner(owner)) throw new Error('Invalid sidebar owner');
  const name = createHash('sha1').update(path.resolve(workspace)).digest('hex').slice(0, 16);
  return path.join(tmpdir(), 'mini-harness-sidebar', `${name}${owner ? '-' + owner.instanceId : ''}${kind === 'link' ? '' : '.' + kind}.json`);
}

export function readChannel(file: string): Record<string, unknown> | null {
  try {
    if (statSync(file).size > 256 * 1024) return null;
    const text = readFileSync(file, 'utf8');
    if (Buffer.byteLength(text) > 256 * 1024) return null;
    const value: unknown = JSON.parse(text);
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
  } catch {return null;}
}

export function writeChannel(file: string, value: unknown): void {
  const text = JSON.stringify(value);
  if (Buffer.byteLength(text) > 256 * 1024) throw new Error('Sidebar message exceeds 256 KiB');
  mkdirSync(path.dirname(file), {recursive: true});
  const temporary = `${file}.${randomUUID()}.tmp`;
  try {
    writeFileSync(temporary, text, {mode: 0o600, flag: 'wx'});
    renameSync(temporary, file);
  } finally {rmSync(temporary, {force: true});}
}
