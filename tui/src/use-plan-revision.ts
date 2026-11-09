import {useEffect, useRef, useState} from 'react';
import type {BackendClient} from './backend.js';
import {reviewKey, type PlanRevisionArgs, type PlanRevisionResult, type ReviewRef, type ServerEvent} from './protocol.js';

export type RevisionAction = 'preview' | 'send' | 'edit' | 'query' | 'resume' | 'close' | 'open-record' | 'new-feedback' | 'reload' | 'view-result';
type Record = {target: ReviewRef; objective: string; draft: string; submitted: string | null;
  receipt: PlanRevisionResult | null; problem: string | null; pending: string | null; sequence: number};
type History = {target: ReviewRef; objective: string; records: PlanRevisionResult[]; index: number;
  cursor?: string; previous: (string | undefined)[]; next: string | null; pending: string | null; problem: string | null};
type HistoryRequest = {target: ReviewRef; cursor?: string; previous: (string | undefined)[]; timer?: ReturnType<typeof setTimeout>};
type State = {records: Map<string, Record>; key: string | null; mode: 'edit' | 'confirm' | 'status' | 'history' | null; selected: RevisionAction | null; history: History | null};
type Request = {key: string; feedback: string; action: PlanRevisionArgs['action']; sequence: number; timer?: ReturnType<typeof setTimeout>};

export function usePlanRevision(client: BackendClient, onMessage: (message: string) => void,
  onLaunch: (id: string, target: ReviewRef, objective: string, receipt: PlanRevisionResult) => boolean,
  onSubmitted: (source: ReviewRef, result: ReviewRef, activate?: boolean) => void, canStart: () => boolean, onReceipt: (receipt: PlanRevisionResult) => void) {
  const [state, setState] = useState<State>({records: new Map(), key: null, mode: null, selected: null, history: null});
  const current = useRef(state); const sequence = useRef(0);
  const pending = useRef(new Map<string, Request>()); const runs = useRef(new Map<string, Request>());
  const launched = useRef(new Set<string>());
  const historyRequests = useRef(new Map<string, HistoryRequest>());
  const callbacks = useRef({onMessage, onLaunch, onSubmitted, canStart, onReceipt});
  callbacks.current = {onMessage, onLaunch, onSubmitted, canStart, onReceipt};
  const update = (next: State) => {current.current = next; setState(next);};
  const save = (key: string, record: Record, extra: Partial<State> = {}) => update({...current.current, ...extra, records: new Map(current.current.records).set(key, record)});
  useEffect(() => () => {
    for (const request of [...pending.current.values(), ...historyRequests.current.values()]) clearTimeout(request.timer);
    pending.current.clear(); historyRequests.current.clear(); runs.current.clear();
  }, []);
  const close = () => {update({...current.current, mode: null, selected: null}); callbacks.current.onMessage('');};
  const historyUnknown = (id: string, reason: string) => {
    const request = historyRequests.current.get(id); if (!request) return;
    clearTimeout(request.timer); historyRequests.current.delete(id);
    const history = current.current.history;
    if (history?.pending === id) update({...current.current, history: {...history, pending: null, problem: `修订记录未读取：${reason}`}});
  };
  const loadHistory = (cursor?: string, previous: (string | undefined)[] = []) => {
    const history = current.current.history; if (!history || history.pending) return;
    try {
      const id = client.command('plan', {action: 'list-revisions', target: history.target, ...(cursor ? {cursor} : {})});
      const request: HistoryRequest = {target: history.target, cursor, previous};
      historyRequests.current.set(id, request);
      request.timer = setTimeout(() => historyUnknown(id, '等待回执超时，可重试只读查询'), 15_000);
      update({...current.current, selected: null, history: {...history, records: [], index: 0, cursor, previous,
        next: null, pending: id, problem: null}});
    } catch (error) {update({...current.current, history: {...history, problem: `修订记录未读取：${String(error)}`}});}
  };
  const openHistory = (target: ReviewRef, objective: string) => {
    update({...current.current, key: reviewKey(target), mode: 'history', selected: null,
      history: {target, objective, records: [], index: 0, previous: [], next: null, pending: null, problem: null}});
    callbacks.current.onMessage(''); loadHistory();
  };
  const unknown = (id: string, reason: string) => {
    const request = pending.current.get(id); if (!request) return;
    clearTimeout(request.timer); pending.current.delete(id);
    const record = current.current.records.get(request.key);
    if (!record || record.sequence !== request.sequence) return;
    const problem = `修订结果待确认：${reason}；先查询结果`;
    save(request.key, {...record, problem, pending: null}); callbacks.current.onMessage(problem);
  };
  const send = (key: string, action: PlanRevisionArgs['action']) => {
    const record = current.current.records.get(key); if (!record || record.pending) return;
    if (action !== 'query-revision' && !callbacks.current.canStart()) {callbacks.current.onMessage('当前任务未结束；反馈已保留'); return;}
    const feedback = action === 'request-changes' ? record.draft.trim() : record.submitted;
    if (!feedback || [...feedback].length > 4000) {callbacks.current.onMessage('反馈须为 1–4000 字，原文已保留'); return;}
    try {
      const args: PlanRevisionArgs = action === 'resume-revision'
        ? {action, target: record.target, feedback, expected_segment: record.receipt!.task!.segment}
        : {action, target: record.target, feedback};
      const id = client.command('plan', args);
      const request: Request = {key, feedback, action, sequence: ++sequence.current};
      pending.current.set(id, request);
      request.timer = setTimeout(() => unknown(id, '等待回执超时'), 15_000);
      save(key, {...record, submitted: feedback, pending: id, problem: null, sequence: request.sequence},
        current.current.key === key ? {mode: 'status', selected: null} : {});
      callbacks.current.onMessage(action === 'query-revision' ? '正在查询修订结果…' : '修订请求已发送，等待回执');
    } catch (error) {callbacks.current.onMessage(`修订请求未送达：${String(error)}；反馈已保留`);}
  };
  const receive = (event: ServerEvent): boolean => {
    if (event.type === 'closed') {
      for (const id of pending.current.keys()) unknown(id, '连接已断开');
      for (const id of historyRequests.current.keys()) historyUnknown(id, '连接已断开');
      runs.current.clear(); launched.current.clear(); close(); return false;
    }
    if (event.type === 'error' && event.id && historyRequests.current.has(event.id)) {historyUnknown(event.id, event.message); return true;}
    if (event.type === 'plan_revision_history' && event.id) {
      const request = historyRequests.current.get(event.id); if (!request) return true;
      if (!event.target || reviewKey(event.target) !== reviewKey(request.target)) {historyUnknown(event.id, '回执对象不匹配'); return true;}
      clearTimeout(request.timer); historyRequests.current.delete(event.id);
      const history = current.current.history;
      if (history?.pending === event.id) update({...current.current, selected: current.current.mode === 'history' ? null : current.current.selected,
        history: {...history, records: event.records, index: 0, cursor: request.cursor, previous: request.previous,
          next: event.next_cursor, pending: null, problem: event.code ? event.message : null}});
      return true;
    }
    if (event.type === 'error' && event.id && pending.current.has(event.id)) {unknown(event.id, event.message); return true;}
    if (event.type === 'done' && event.id) {
      const request = runs.current.get(event.id); runs.current.delete(event.id);
      if (request) send(request.key, 'query-revision');
      return false;
    }
    if (event.type !== 'plan_revision' || !event.id) return false;
    const request = pending.current.get(event.id) ?? runs.current.get(event.id);
    if (!request) return true;
    const record = current.current.records.get(request.key);
    if (!record) return true;
    if (!event.target || reviewKey(event.target) !== reviewKey(record.target) || event.feedback !== request.feedback) {
      unknown(event.id, '修订回执对象不匹配'); return true;
    }
    clearTimeout(request.timer); pending.current.delete(event.id);
    if (event.task?.run_started && request.action !== 'query-revision') {runs.current.set(event.id, request); launched.current.add(event.id);}
    if (record.sequence > request.sequence) return true;
    const terminal = ['submitted', 'paused', 'incomplete', 'unavailable'].includes(event.outcome);
    if (terminal) runs.current.delete(event.id);
    save(request.key, {...record, pending: null, problem: null, receipt: event});
    if (current.current.key === request.key) callbacks.current.onMessage(event.message);
    if (event.task?.run_started && request.action !== 'query-revision' && callbacks.current.onLaunch(event.id, record.target, record.objective, event)) close();
    callbacks.current.onReceipt(event);
    if (event.result_target && (!record.receipt?.result_target || reviewKey(record.receipt.result_target) !== reviewKey(event.result_target))) {
      callbacks.current.onSubmitted(record.target, event.result_target);
    }
    return true;
  };
  const open = (target: ReviewRef, objective: string) => {
    const key = reviewKey(target); const record = current.current.records.get(key) ?? {
      target, objective, draft: '', submitted: null, receipt: null, problem: null, pending: null, sequence: 0,
    };
    save(key, record, {key, mode: record.submitted ? 'status' : 'edit', selected: null}); callbacks.current.onMessage('');
  };
  const record = state.key ? state.records.get(state.key) : undefined;
  const historyMove = (direction: number) => {
    const history = current.current.history; if (!history || history.pending || !history.records.length) return;
    update({...current.current, selected: null, history: {...history, index: Math.max(0, Math.min(history.records.length - 1, history.index + direction))}});
  };
  const historyPage = (direction: number) => {
    const history = current.current.history; if (!history || history.pending) return;
    if (direction > 0 && history.next) loadHistory(history.next, [...history.previous, history.cursor]);
    if (direction < 0 && history.previous.length) loadHistory(history.previous.at(-1), history.previous.slice(0, -1));
  };
  const preview = () => {
    const record = current.current.key ? current.current.records.get(current.current.key) : undefined;
    if (!record?.draft.trim() || [...record.draft.trim()].length > 4000) {callbacks.current.onMessage('反馈须为 1–4000 字，原文已保留'); return;}
    update({...current.current, mode: 'confirm', selected: 'send'}); callbacks.current.onMessage('');
  };
  const buttons: {action: RevisionAction; label: string}[] = state.mode === 'history' ? [
    ...(!state.history?.pending ? state.history?.problem ? [{action: 'reload' as const, label: '重试'}] : [
      ...(state.history?.records.length ? [{action: 'open-record' as const, label: '查看'}] : []),
      {action: 'new-feedback' as const, label: '新反馈'},
    ] : []), {action: 'close', label: '返回'},
  ] : state.mode === 'edit' ? [{action: 'preview', label: '下一步'}, {action: 'close', label: '返回'}]
    : state.mode === 'confirm' ? [{action: 'send', label: '确认修订'}, {action: 'edit', label: '编辑'}, {action: 'close', label: '返回'}]
    : [
      ...(!record?.pending && record?.submitted ? [{action: 'query' as const, label: '查询结果'}] : []),
      ...(!record?.pending && !record?.problem && !record?.receipt?.result_target && record?.receipt?.task && record.receipt.task.segment > 0 &&
        ['paused', 'interrupted'].includes(record.receipt.task.status) ? [{action: 'resume' as const, label: '续做'}] : []),
      ...(!record?.pending && !record?.problem && record?.receipt?.result_target ? [{action: 'view-result' as const, label: '新版'}] : []),
      ...(!record?.pending && !record?.problem && !state.key?.startsWith('saved:') && ['absent', 'unavailable'].includes(record?.receipt?.outcome ?? '') ? [{action: 'edit' as const, label: '编辑'}] : []),
      {action: 'close', label: '返回'},
    ];
  const activate = (action: RevisionAction) => {
    if (!buttons.some(button => button.action === action)) return;
    if (action === 'close') {close(); return;}
    if (action === 'reload') {loadHistory(current.current.history?.cursor, current.current.history?.previous); return;}
    if (action === 'new-feedback') {const history = current.current.history; if (history) open(history.target, history.objective); return;}
    if (action === 'open-record') {
      const history = current.current.history; const entry = history?.records[history.index];
      if (!entry?.target || !history) return;
      const key = `saved:${JSON.stringify([reviewKey(entry.target), entry.feedback])}`;
      const existing = current.current.records.get(key);
      save(key, existing?.pending ? existing : {target: entry.target, objective: `计划 ${entry.target.plan_id} v${entry.target.plan_version}`, draft: entry.feedback,
        submitted: entry.feedback, receipt: entry, problem: null, pending: null, sequence: ++sequence.current},
      {key, mode: 'status', selected: null});
      return;
    }
    if (action === 'view-result') {
      if (record?.receipt?.result_target) {callbacks.current.onSubmitted(record.target, record.receipt.result_target, true); close();}
      return;
    }
    if (action === 'preview') {preview(); return;}
    if (action === 'edit') {update({...current.current, mode: 'edit', selected: null}); return;}
    if (current.current.key) send(current.current.key, action === 'query' ? 'query-revision' : action === 'resume' ? 'resume-revision' : 'request-changes');
  };
  const select = (action: RevisionAction | null) => update({...current.current, selected: action});
  const move = (direction: number) => {
    const index = buttons.findIndex(button => button.action === current.current.selected);
    select(buttons[(index < 0 ? direction > 0 ? 0 : buttons.length - 1 : (index + direction + buttons.length) % buttons.length)]!.action);
  };
  const change = (draft: string) => {const key = current.current.key; if (key) save(key, {...current.current.records.get(key)!, draft});};
  return {mode: state.mode, selected: state.selected, record, buttons, key: state.key, history: state.history,
    decisionPending: [...pending.current.values()].some(request => request.action !== 'query-revision'),
    open, openHistory, historyMove, historyPage, close, change, preview, activate, select, move, receive, takeCompletedRun: (id: string) => launched.current.delete(id)};
}
