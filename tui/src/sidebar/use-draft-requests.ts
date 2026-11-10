import {visibleText} from '../markdown.js';
import {reviewKey, type ReviewRef} from '../protocol.js';
import {createHash} from 'node:crypto';
import {verificationContent} from './verification-content.js';
import {verificationInspection} from '../inspection.js';
import {checkFileSource} from './file-source.js';
import {useEffect, useRef} from 'react';
import type {ViewState} from '../view-state.js';
import {readChannel, type SidebarOwner} from './channel.js';
import {readWorkspaceStatusAsync, readFileDiffAsync} from './git.js';
import {draftPath, readDraftRequest, writeDraftReply, removeDraftChannel, excerptText, formatExcerpt, type DraftReply, type DraftRequest} from './draft-channel.js';

export function useSidebarDraftRequests(state: ViewState, owner: SidebarOwner, append: (text: string, request: DraftRequest) => void, disabled: boolean, readPlanPage: (target: ReviewRef, cursor: number) => Promise<{body: string; nextCursor: number | null}>) {
  const current = useRef({state, append, disabled, readPlanPage}); current.current = {state, append, disabled, readPlanPage};
  useEffect(() => {
    if (!state.workspace || !state.sessionId) return;
    let alive = true; let pending = false;
    const replies = new Map<string, {request: string; reply: DraftReply; expires: number}>();
    const fileOrigin = (request: DraftRequest) => {
      const view = current.current.state;
      const saved = request.requestId ? view.entries.find(entry => entry.inspection?.scope.requestId === request.requestId &&
        entry.inspection.sessionId === request.sessionId)?.inspection : undefined;
      return saved ? saved.fileSource ?? null : view.fileSource;
    };
    const validScope = (request: DraftRequest) => {
      const {state: view, disabled} = current.current;
      if (request.source.kind === 'diff') {
        const expected = fileOrigin(request); const source = request.source.fileSource;
        if (expected === null || expected?.root !== source?.root || expected?.identity !== source?.identity) return false;
      }
      if (request.source.kind === 'plan' && request.requestId) {
        const bound = view.entries.find(entry => entry.inspection?.scope.requestId === request.requestId)?.inspection?.plan ??
          (view.inspection?.scope.requestId === request.requestId && view.inspectionPlan?.relation !== 'review' ? view.inspectionPlan : null);
        if (!bound || reviewKey(bound.target) !== reviewKey(request.source.target)) return false;
      }
      return !disabled && view.phase !== 'closed' && view.phase !== 'starting' && view.workspace === request.workspace && view.sessionId === request.sessionId &&
        (request.requestId === null ? request.source.kind === 'plan' || !view.inspection?.scope.requestId : view.inspection?.scope.requestId === request.requestId ||
          view.entries.some(entry => entry.inspection?.sessionId === request.sessionId && entry.inspection.scope.requestId === request.requestId));
    };
    const publish = (reply: DraftReply) => {
      try {if (JSON.stringify(readChannel(draftPath(reply.workspace, owner, true))) !== JSON.stringify(reply)) writeDraftReply(reply);} catch {}
    };
    const tick = async () => {
      if (pending) return;
      const request = readDraftRequest(state.workspace, owner); if (!request) return;
      const key = JSON.stringify(request); const cached = replies.get(request.id);
      if (cached) {
        publish(cached.request === key ? cached.reply : {...cached.reply, outcome: 'error', message: '请求 ID 已用于其他内容；未重复附回'}); return;
      }
      for (const [id, value] of replies) if (value.expires < Date.now()) replies.delete(id);
      const reply: DraftReply = {id: request.id, workspace: request.workspace, owner, sessionId: request.sessionId,
        outcome: 'error', message: '来源会话或请求已失效；选区保留，可复制'};
      pending = true;
      try {
        if (!validScope(request)) return;
        if (replies.size >= 256) {reply.message = '引用请求过多，请稍后重新选择；可先复制'; return;}
        let body: string | undefined; let revision: string | undefined;
        if (request.source.kind === 'diff') {
          const source = request.source;
          const origin = fileOrigin(request); const root = origin?.root ?? state.workspace;
          const problem = origin !== undefined ? await checkFileSource(origin) : null;
          if (problem) {reply.message = problem + '；选区保留，可复制'; return;}
          const status = await readWorkspaceStatusAsync(root);
          const file = status.ok ? status.status.files.find(file => file.path === source.path) : undefined;
          if (!status.ok || !file) {reply.message = '来源文件已变化或无法读取；选区保留，可复制'; return;}
          const diff = await readFileDiffAsync(root, file, status.status.head !== null);
          if (origin !== undefined && await checkFileSource(origin)) {reply.message = '来源目录已变化；选区保留，可复制'; return;}
          if (diff.ok) {body = diff.text; revision = diff.revision;}
        } else if (request.source.kind === 'plan') {
          const source = request.source; let cursor = source.cursor; let remaining = source.length; body = '';
          while (remaining > 0) {
            if (!alive || !validScope(request) || Date.now() - request.createdAt > 30_000) return;
            const page = await current.current.readPlanPage(source.target, cursor);
            const chars = Array.from(page.body); const taken = chars.slice(0, remaining);
            body += taken.join(''); remaining -= taken.length;
            if (!remaining) break;
            if (!chars.length || page.nextCursor === null || page.nextCursor <= cursor) break;
            cursor = page.nextCursor;
          }
          revision = source.target.plan_hash;
        } else {
          const view = current.current.state;
          const snapshot = view.entries.find(entry => entry.inspection?.sessionId === request.sessionId &&
            entry.inspection.scope.requestId === request.requestId)?.inspection;
          if (snapshot) body = verificationContent({run: snapshot.scope, verification: snapshot.verification, sessionId: snapshot.sessionId});
          else if (request.requestId && view.inspection?.scope.requestId === request.requestId) body = verificationContent({run: view.inspection.scope,
            verification: verificationInspection(view.inspection, view.entries), sessionId: view.sessionId});
          if (body !== undefined) revision = createHash('sha256').update(body).digest('hex');
        }
        if (body === undefined || revision !== request.source.revision || (request.source.kind === 'plan' ? visibleText(body) : excerptText(body, request.start, request.end)) !== request.text) {
          reply.message = '来源版本或选区已变化；请接入后重选，或复制旧内容'; return;
        }
        if (!alive || !validScope(request) || Date.now() - request.createdAt > 30_000) return;
        current.current.append(formatExcerpt(request), request);
        reply.outcome = 'appended'; reply.message = '已附到主区草稿 · 尚未发送';
      } catch (error) {reply.message = `${error instanceof Error ? error.message : '读取来源失败'}；选区保留，可复制`;}
      finally {
        pending = false;
        if (alive) {
          // 拒绝也须去重，避免排队中的同一请求稍后自动生效。
          replies.set(request.id, {request: key, reply, expires: request.createdAt + 35_000});
          publish(reply);
        }
      }
    };
    const timer = setInterval(() => {void tick();}, 100);
    return () => {alive = false; clearInterval(timer); removeDraftChannel(state.workspace, owner);};
  }, [state.workspace, state.sessionId, owner]);
}
