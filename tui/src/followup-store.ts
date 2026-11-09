import {createHash} from 'node:crypto';
import {lstatSync, readFileSync, realpathSync} from 'node:fs';
import path from 'node:path';
import {writeChannel} from './sidebar/channel.js';
import {claimSidebarLease} from './sidebar/lease.js';

export type Followup = {id: string; text: string};
export type FollowupScope = {workspace: string; sessionId: string; sessionPath: string; taskId?: string};
export type FollowupData = {items: Followup[]; paused: string | null; inflight: (Followup & {requestId: string}) | null};
const limit = 256 * 1024;
const uuid = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const validItem = (item: Followup) => item && uuid.test(item.id) && typeof item.text === 'string' &&
  Boolean(item.text.trim()) && Buffer.byteLength(item.text) <= 64 * 1024;
function validData(value: unknown): value is FollowupData {
  if (!value || typeof value !== 'object') return false;
  const data = value as FollowupData;
  return (data.paused === null || typeof data.paused === 'string' && data.paused.length <= 2048) &&
    (data.inflight === null || validItem(data.inflight) && uuid.test(data.inflight.requestId)) &&
    Array.isArray(data.items) && data.items.length <= 100 && data.items.every(validItem) &&
    !data.items.some(item => item.id === data.inflight?.id) &&
    new Set(data.items.map(item => item.id)).size === data.items.length;
}

export function openFollowupStore(scope: FollowupScope) {
  const directory = realpathSync(scope.sessionPath);
  const originalDirectory = lstatSync(directory);
  const identity = {workspace: realpathSync(scope.workspace), sessionId: scope.sessionId, taskId: scope.taskId ?? null};
  const suffix = scope.taskId ? `-${createHash('sha256').update(scope.taskId).digest('hex')}` : '';
  const file = path.join(directory, `followups${suffix}.json`);
  const lease = claimSidebarLease(JSON.stringify(['followups', file]));
  const read = (): FollowupData => {
    const currentDirectory = lstatSync(directory);
    if (!currentDirectory.isDirectory() || currentDirectory.dev !== originalDirectory.dev || currentDirectory.ino !== originalDirectory.ino)
      throw new Error('会话目录已被替换，请重开会话后核对');
    let stat;
    try {stat = lstatSync(file);} catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {items: [], paused: null, inflight: null};
      throw error;
    }
    if (!stat.isFile() || stat.size > limit) throw new Error('待办文件不是普通文件或超过 256 KiB');
    const text = readFileSync(file, 'utf8');
    if (Buffer.byteLength(text) > limit) throw new Error('待办文件超过 256 KiB');
    const value = JSON.parse(text);
    if (value?.version !== 1 || JSON.stringify(value.scope) !== JSON.stringify(identity) || !validData(value.data))
      throw new Error('待办文件损坏或不属于当前会话');
    return value.data;
  };
  try {
    const data = read();
    return {data, writable: Boolean(lease), close: () => lease?.release(), write: (next: FollowupData) => {
      if (!lease) throw new Error('另一窗口正在管理待办');
      if (!validData(next)) throw new Error('待办最多 100 条，每条最多 64 KiB');
      read();
      const value = {version: 1, scope: identity, data: next};
      if (Buffer.byteLength(JSON.stringify(value)) > limit) throw new Error('待办总量超过 256 KiB');
      writeChannel(file, value);
    }};
  } catch (error) {lease?.release(); throw error;}
}
