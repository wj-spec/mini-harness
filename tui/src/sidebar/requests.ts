import {rmSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
import path from 'node:path';
import {reviewKey, reviewRef, type ReviewRef} from '../protocol.js';
import {readChannel, sameOwner, sidebarChannelPath, writeChannel, type SidebarOwner} from './channel.js';
import {readSidebarSignal} from './signal.js';

export type PlanPageRequest = {
  kind: 'plan-page'; id: string; workspace: string; owner: SidebarOwner; sessionId: string;
  target: ReviewRef; cursor: number; createdAt: number;
};
export type PlanPageReply = PlanPageRequest & {
  outcome: 'page' | 'error'; body: string; nextCursor: number | null; message: string;
};

const requestKeys = ['kind', 'id', 'workspace', 'owner', 'sessionId', 'target', 'cursor', 'createdAt'];
export function readPlanPageRequest(workspace: string, owner: SidebarOwner): PlanPageRequest | null {
  const value = readChannel(sidebarChannelPath(workspace, 'request', owner));
  if (!value || Object.keys(value).length !== requestKeys.length || Object.keys(value).some(key => !requestKeys.includes(key)) ||
      value.kind !== 'plan-page' || typeof value.id !== 'string' || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value.id) ||
      value.workspace !== path.resolve(workspace) || !sameOwner(value.owner, owner) || !reviewRef(value.target) ||
      value.sessionId !== value.target.session_id || !Number.isSafeInteger(value.cursor) || Number(value.cursor) < 0 ||
      typeof value.createdAt !== 'number' || !Number.isFinite(value.createdAt) || Date.now() - value.createdAt > 30_000 ||
      value.createdAt - Date.now() > 5_000) return null;
  return value as PlanPageRequest;
}

export function writePlanPageReply(reply: PlanPageReply): void {
  writeChannel(sidebarChannelPath(reply.workspace, 'reply', reply.owner), reply);
}

export function removeSidebarRequests(workspace: string, owner: SidebarOwner): void {
  for (const kind of ['request', 'reply'] as const) rmSync(sidebarChannelPath(workspace, kind, owner), {force: true});
}

export async function requestPlanPage(workspace: string, owner: SidebarOwner, sessionId: string, target: ReviewRef,
  cursor: number, abort: AbortSignal): Promise<PlanPageReply> {
  const live = readSidebarSignal(workspace, owner);
  if (!reviewRef(target) || target.session_id !== sessionId || !Number.isSafeInteger(cursor) || cursor < 0 ||
      live?.scope?.sessionId !== sessionId) throw new Error('主工作台未连接，或计划身份已失效');
  if (abort.aborted) throw new Error('读取已取消');
  const request: PlanPageRequest = {kind: 'plan-page', id: randomUUID(), workspace: path.resolve(workspace), owner, sessionId, target, cursor, createdAt: Date.now()};
  const file = sidebarChannelPath(workspace, 'request', owner);
  writeChannel(file, request);
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearInterval(timer); clearTimeout(timeout); abort.removeEventListener('abort', cancel);
      if (readChannel(file)?.id === request.id) rmSync(file, {force: true});
    };
    const fail = (message: string) => {cleanup(); reject(new Error(message));};
    const cancel = () => fail('读取已取消');
    const timer = setInterval(() => {
      const value = readChannel(sidebarChannelPath(workspace, 'reply', owner));
      if (!value || value.id !== request.id) return;
      if (Object.keys(value).length !== requestKeys.length + 4 || Object.keys(value).some(key => ![...requestKeys, 'outcome', 'body', 'nextCursor', 'message'].includes(key)) ||
          value.kind !== request.kind || value.workspace !== request.workspace || !sameOwner(value.owner, owner) || value.sessionId !== sessionId ||
          !reviewRef(value.target) || reviewKey(value.target) !== reviewKey(target) || value.cursor !== cursor || value.createdAt !== request.createdAt ||
          !['page', 'error'].includes(String(value.outcome)) || typeof value.body !== 'string' || Array.from(value.body).length > 16000 ||
          typeof value.message !== 'string' || value.message.length > 4000 ||
          !(value.nextCursor === null || Number.isSafeInteger(value.nextCursor) && Number(value.nextCursor) > cursor && Number(value.nextCursor) === cursor + Array.from(value.body).length) ||
          value.outcome === 'error' && (value.body !== '' || value.nextCursor !== null)) {fail('正文回执不匹配，已保留原内容'); return;}
      cleanup(); resolve(value as PlanPageReply);
    }, 50);
    const timeout = setTimeout(() => fail('主工作台未返回正文，请重试'), 15_000);
    abort.addEventListener('abort', cancel, {once: true});
  });
}
