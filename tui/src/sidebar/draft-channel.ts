import {reviewRef, type ReviewRef} from '../protocol.js';
import {validFileSource} from '../inspection.js';
import {rmSync} from 'node:fs';
import path from 'node:path';
import {readChannel, sameOwner, sidebarChannelPath, writeChannel, type SidebarOwner} from './channel.js';
import {parseDiff} from './diff.js';

export type ExcerptPoint = {line: number; offset: number};
export type EvidenceExcerpt = {source: {kind: 'diff'; path: string; revision: string; fileSource?: import('../inspection.js').FileSource} | {kind: 'verification'; revision: string} | {kind: 'plan'; target: ReviewRef; cursor: number; length: number; revision: string}; start: ExcerptPoint; end: ExcerptPoint; text: string};
export type DraftRequest = EvidenceExcerpt & {kind: 'append-excerpt'; id: string; workspace: string; owner: SidebarOwner;
  sessionId: string; requestId: string | null; createdAt: number};
export type DraftReply = {id: string; workspace: string; owner: SidebarOwner; sessionId: string; outcome: 'appended' | 'error'; message: string};
export const draftPath = (workspace: string, owner: SidebarOwner, reply = false) =>
  sidebarChannelPath(workspace, 'signal', owner).replace('.signal.json', reply ? '.draft-reply.json' : '.draft-request.json');
const exact = (value: unknown, keys: string[]): value is Record<string, unknown> => Boolean(value && typeof value === 'object' &&
  !Array.isArray(value) && Object.keys(value).length === keys.length && Object.keys(value).every(key => keys.includes(key)));
const point = (value: unknown): value is ExcerptPoint => exact(value, ['line', 'offset']) &&
  Number.isSafeInteger(value.line) && Number(value.line) >= 0 && Number.isSafeInteger(value.offset) && Number(value.offset) >= 0;
function validSource(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false;
  const source = value as Record<string, unknown>;
  if (typeof source.revision !== 'string' || !/^[a-f0-9]{64}$/.test(source.revision)) return false;
  if (source.kind === 'plan') return exact(source, ['kind', 'target', 'cursor', 'length', 'revision']) && reviewRef(source.target) &&
    source.revision === source.target.plan_hash && Number.isSafeInteger(source.cursor) && Number(source.cursor) >= 0 && Number.isSafeInteger(source.length) && Number(source.length) > 0 && Number(source.length) <= 32_000;
  if (source.kind === 'verification') return exact(source, ['kind', 'revision']);
  return exact(source, ['kind', 'path', 'revision', ...(source.fileSource !== undefined ? ['fileSource'] : [])]) &&
    (source.fileSource === undefined || validFileSource(source.fileSource)) && source.kind === 'diff' && typeof source.path === 'string' && Boolean(source.path) &&
    !path.isAbsolute(source.path) && !source.path.split('/').some(part => !part || part === '.' || part === '..') && !/[\x00-\x1f\x7f]/.test(source.path);
}
export function readDraftRequest(workspace: string, owner: SidebarOwner): DraftRequest | null {
  const value = readChannel(draftPath(workspace, owner));
  if (!exact(value, ['kind', 'id', 'workspace', 'owner', 'sessionId', 'requestId', 'createdAt', 'source', 'start', 'end', 'text']) ||
      value.kind !== 'append-excerpt' || typeof value.id !== 'string' || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value.id) ||
      value.workspace !== path.resolve(workspace) || !sameOwner(value.owner, owner) || typeof value.sessionId !== 'string' || !value.sessionId ||
      !(value.requestId === null || typeof value.requestId === 'string' && value.requestId.length <= 200) ||
      typeof value.createdAt !== 'number' || !Number.isFinite(value.createdAt) || Date.now() - value.createdAt > 30_000 || value.createdAt - Date.now() > 5_000 ||
      !point(value.start) || !point(value.end) || typeof value.text !== 'string' || !value.text.trim() || Buffer.byteLength(value.text) > 32_000 ||
      !validSource(value.source)) return null;
  return value as DraftRequest;
}
export function excerptText(body: string, start: ExcerptPoint, end: ExcerptPoint): string | null {
  const lines = parseDiff(body).map(line => line.text);
  if (start.line > end.line || start.line === end.line && start.offset >= end.offset || end.line >= lines.length ||
      start.offset > lines[start.line]!.length || end.offset > lines[end.line]!.length) return null;
  return lines.slice(start.line, end.line + 1).map((line, index, range) =>
    line.slice(index === 0 ? start.offset : 0, index === range.length - 1 ? end.offset : undefined)).join('\n');
}
export function formatExcerpt(request: EvidenceExcerpt & {requestId: string | null}): string {
  return `[侧栏引用 · ${request.source.kind === 'diff' ? '工作区 vs HEAD · ' + request.source.path : request.source.kind === 'plan' ? `计划 ${request.source.target.plan_id} v${request.source.target.plan_version} · 计划任务 ${request.source.target.plan_task_id} · 字符 ${request.source.cursor}+${request.source.length}` : '验证记录'} · 版本 ${request.source.revision} · 请求 ${request.requestId ?? '未绑定'}]\n` +
    (request.source.kind === 'diff' && request.source.fileSource ? `来源目录：${request.source.fileSource.root}\n` : '') +
    request.text.split('\n').map(line => '> ' + line).join('\n') + '\n[引用结束]\n';
}
export function writeDraftReply(reply: DraftReply): void {writeChannel(draftPath(reply.workspace, reply.owner, true), reply);}
export function removeDraftChannel(workspace: string, owner: SidebarOwner): void {
  for (const reply of [false, true]) rmSync(draftPath(workspace, owner, reply), {force: true});
}
