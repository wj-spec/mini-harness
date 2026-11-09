import {useEffect, useRef} from 'react';
import type {BackendClient} from '../backend.js';
import {reviewKey, type ReviewRef, type ServerEvent} from '../protocol.js';
import type {ViewState} from '../view-state.js';
import type {SidebarOwner} from './channel.js';
import {readPlanPageRequest, removeSidebarRequests, writePlanPageReply, type PlanPageReply, type PlanPageRequest} from './requests.js';

type Page = {body: string; nextCursor: number | null};
type Complete = (body: string, nextCursor: number | null, error: string) => void;
export function useSidebarPlanRequests(client: BackendClient, state: ViewState, owner: SidebarOwner) {
  const current = useRef(state); current.current = state;
  const pending = useRef<{backendId: string; target: ReviewRef; cursor: number; fileId?: string; complete: Complete; timer: ReturnType<typeof setTimeout>} | null>(null);
  const replies = useRef(new Map<string, PlanPageReply>());
  const backendIds = useRef(new Set<string>());
  const reply = (request: PlanPageRequest, body = '', nextCursor: number | null = null, error = '') => {
    const value: PlanPageReply = {...request, outcome: error ? 'error' : 'page', body, nextCursor, message: error};
    replies.current.set(request.id, value);
    while (replies.current.size > 64) replies.current.delete(replies.current.keys().next().value!);
    try {writePlanPageReply(value);} catch {}
  };
  const finish = (body = '', nextCursor: number | null = null, error = '') => {
    const active = pending.current; if (!active) return;
    clearTimeout(active.timer); pending.current = null; active.complete(body, nextCursor, error);
  };
  const start = (target: ReviewRef, cursor: number, complete: Complete, fileId?: string) => {
    const view = current.current;
    const matches = (known: ReviewRef | null | undefined) => known && reviewKey(known) === reviewKey(target);
    const known = matches(view.inspectionPlan?.target) || view.entries.some(entry => matches(entry.review?.target) || matches(entry.inspection?.plan?.target));
    if (target.session_id !== view.sessionId || !known) {complete('', null, '计划不属于当前会话中已知的审阅对象'); return;}
    if (view.phase === 'closed') {complete('', null, '后端连接已断开'); return;}
    if (!client.capabilities?.().includes('plan-review')) {complete('', null, '当前后端不支持计划正文读取'); return;}
    if (pending.current) {complete('', null, '另一页正在读取，请稍后重试'); return;}
    try {
      const backendId = client.command('plan', {action: 'review', target, cursor});
      backendIds.current.add(backendId);
      while (backendIds.current.size > 64) backendIds.current.delete(backendIds.current.values().next().value!);
      pending.current = {backendId, target, cursor, fileId, complete, timer: setTimeout(() => finish('', null, '读取超时，请重试'), 10_000)};
    } catch {complete('', null, '读取请求未送达，请重试');}
  };
  const readPage = (target: ReviewRef, cursor: number): Promise<Page> => new Promise((resolve, reject) =>
    start(target, cursor, (body, nextCursor, error) => error ? reject(new Error(error)) : resolve({body, nextCursor})));
  useEffect(() => {
    if (!state.workspace || !state.sessionId) return;
    const tick = () => {
      const request = readPlanPageRequest(state.workspace, owner);
      if (!request || replies.current.has(request.id) || pending.current?.fileId === request.id) return;
      start(request.target, request.cursor, (body, next, error) => reply(request, body, next, error), request.id);
    };
    const timer = setInterval(tick, 100);
    return () => {
      clearInterval(timer); finish('', null, '主会话已切换或关闭');
      replies.current.clear(); backendIds.current.clear(); removeSidebarRequests(state.workspace, owner);
    };
  }, [client, state.workspace, state.sessionId, owner]);
  const receive = (event: ServerEvent): boolean => {
    if (event.type === 'closed') {finish('', null, '后端连接已断开'); return false;}
    if (!event.id || !backendIds.current.has(event.id)) return false;
    const active = pending.current; if (!active || active.backendId !== event.id) return true;
    if (event.type === 'error') finish('', null, event.message);
    else if (event.type === 'plan_review') {
      if (!event.target || reviewKey(event.target) !== reviewKey(active.target)) finish('', null, '正文回执的计划身份不匹配');
      else if (event.code || event.outcome === 'unavailable' || !event.summary) finish('', null, event.message || '计划暂不可读取');
      else if (Array.from(event.body_page).length > 16000 || event.next_cursor !== null &&
        (event.next_cursor <= active.cursor || event.next_cursor !== active.cursor + Array.from(event.body_page).length)) finish('', null, '正文分页偏移不匹配');
      else finish(event.body_page, event.next_cursor);
    }
    return true;
  };
  return {receive, readPage};
}
