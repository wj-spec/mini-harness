import {useEffect, useRef, useState} from 'react';
import type {BackendClient} from './backend.js';
import type {PlanReviewResult, ReviewRef, ServerEvent} from './protocol.js';
import {reviewKey} from './protocol.js';

type Choice = 'execute' | 'body' | 'approve' | 'dismiss' | 'query' | 'replacement' | 'revise' | 'revisions';
type Request = {target?: ReviewRef; action: 'review' | 'approve-execute' | 'approve' | 'dismiss'; sequence: number; cursor?: number; previous?: number[]; activate?: boolean; autoFrom?: string; quiet?: boolean; timer?: ReturnType<typeof setTimeout>};
type Card = {result: PlanReviewResult; problem: string | null; pending: string | null; sequence: number; statusSegment: number; cursor: number; previous: number[]; body: string; next: number | null};
type ReviewState = {cards: Map<string, Card>; current: string | null; focused: boolean; body: boolean; choice: Choice | null};
export type ReviewChoice = {value: Choice; key: string; label: string};

export function usePlanReview(client: BackendClient, onCard: (result: PlanReviewResult, problem?: string) => void,
  onMessage: (text: string) => void, onLaunch: (id: string, result: PlanReviewResult) => boolean, canDecide: () => boolean,
  onRevise: (target: ReviewRef, objective: string, history?: boolean) => void) {
  const [state, setState] = useState<ReviewState>({cards: new Map(), current: null, focused: false, body: false, choice: null});
  const current = useRef(state);
  const sequence = useRef(0);
  const pending = useRef(new Map<string, Request>());
  const callbacks = useRef({onCard, onMessage, onLaunch, canDecide, onRevise});
  callbacks.current = {onCard, onMessage, onLaunch, canDecide, onRevise};
  const update = (next: ReviewState) => {current.current = next; setState(next);};
  useEffect(() => () => {for (const request of pending.current.values()) clearTimeout(request.timer); pending.current.clear();}, []);
  const unknown = (id: string, reason: string) => {
    const request = pending.current.get(id);
    if (!request) return;
    clearTimeout(request.timer);
    pending.current.delete(id);
    const state = current.current;
    const key = request.target ? reviewKey(request.target) : null;
    const card = key ? state.cards.get(key) : undefined;
    const problem = `结果待确认：${reason}；查询原计划核对`;
    if (card && key && card.pending === id) {
      update({...state, choice: null, cards: new Map(state.cards).set(key, {...card, pending: null, problem})});
      callbacks.current.onCard(card.result, problem);
    }
    callbacks.current.onMessage(problem);
  };
  const send = (action: Request['action'], target?: ReviewRef, cursor?: number, previous?: number[], activate = false, autoFrom?: string, quiet = false) => {
    try {
      const id = client.command('plan', action === 'review' ? {action, ...(target ? {target} : {}), ...(cursor !== undefined ? {cursor} : {})} : {action, target: target!});
      const request: Request = {action, target, cursor, previous, activate, autoFrom, quiet, sequence: ++sequence.current};
      pending.current.set(id, request);
      request.timer = setTimeout(() => unknown(id, '等待回执超时'), 15_000);
      const state = current.current;
      const key = target ? reviewKey(target) : null;
      const card = key ? state.cards.get(key) : null;
      if (key && card) update({...state, choice: null, cards: new Map(state.cards).set(key, {...card, pending: id, sequence: request.sequence})});
      callbacks.current.onMessage(action === 'review' ? '正在查询计划…' : '计划决策已发送，等待回执');
      return true;
    } catch (error) {
      callbacks.current.onMessage(`计划请求未送达：${String(error)}；草稿保留`);
      return false;
    }
  };
  const discover = (quiet = false) => send('review', undefined, undefined, undefined, false, undefined, quiet);
  const blur = () => {
    if (current.current.choice) callbacks.current.onMessage('');
    update({...current.current, focused: false, body: false, choice: null});
  };
  const focus = () => {
    callbacks.current.onMessage('');
    update({...current.current, focused: !current.current.focused, body: false, choice: null});
  };
  const actions = () => {callbacks.current.onMessage(''); update({...current.current, body: false, choice: null});};
  const receive = (event: ServerEvent) => {
    if (event.type === 'task_state') {
      const state = current.current;
      for (const [key, card] of state.cards) {
        const result = card.result;
        if (result.outcome !== 'launched' || result.launch.task_id !== event.task.task_id || event.task.segment < card.statusSegment) continue;
        const updated: PlanReviewResult = {...result, message: '计划运行状态已更新', launch: {...result.launch, status: event.task.status}};
        update({...current.current, cards: new Map(current.current.cards).set(key, {...card, result: updated, statusSegment: event.task.segment})});
        callbacks.current.onCard(updated);
        if (key === state.current && !state.focused) callbacks.current.onMessage('');
      }
      return false;
    }
    if (event.type === 'closed') {
      for (const id of pending.current.keys()) unknown(id, '连接已断开');
      pending.current.clear(); blur(); return false;
    }
    if (event.type === 'error' && event.id && pending.current.has(event.id)) {unknown(event.id, event.message); return true;}
    if (event.type !== 'plan_review' || !event.id) return false;
    const request = pending.current.get(event.id);
    if (!request) return true;
    if (request.target && (!event.target || reviewKey(request.target) !== reviewKey(event.target))) {
      unknown(event.id, '回执对象不匹配'); return true;
    }
    clearTimeout(request.timer);
    pending.current.delete(event.id);
    if (!event.target) {if (!request.quiet) callbacks.current.onMessage(event.message); return true;}
    const state = current.current;
    const key = reviewKey(event.target);
    const existing = state.cards.get(key);
    if (existing && existing.sequence > request.sequence) return true;
    const page = event.outcome !== 'unavailable' && (request.action === 'review' || !existing);
    const cards = new Map(state.cards).set(key, {result: event, problem: null, sequence: request.sequence, pending: null,
      statusSegment: existing?.statusSegment ?? event.launch?.segment ?? 0,
      cursor: page ? request.cursor ?? 0 : existing?.cursor ?? 0,
      previous: page ? request.previous ?? [] : existing?.previous ?? [],
      body: page ? event.body_page : existing?.body ?? '', next: page ? event.next_cursor : existing?.next ?? null});
    const newCurrent = !state.current || request.activate || !state.focused && (request.autoFrom === state.current || !request.target && request.sequence >= (state.cards.get(state.current)?.sequence ?? 0));
    const dismissed = event.outcome === 'dismissed' && key === state.current;
    update({...state, cards, current: newCurrent ? key : state.current,
      focused: dismissed || request.activate ? false : state.focused, choice: newCurrent || key === state.current ? null : state.choice});
    callbacks.current.onCard(event);
    if (key === current.current.current) callbacks.current.onMessage(event.message);
    if (request.action === 'approve-execute' && event.outcome === 'launched' && event.launch.run_started &&
        callbacks.current.onLaunch(event.id, event)) {
      if (key === current.current.current) blur();
    }
    return true;
  };
  const card = state.current ? state.cards.get(state.current) : undefined;
  const choices: ReviewChoice[] = [];
  if (card && !card.pending) {
    const result = card.result;
    if (!card.problem && result.available_actions.includes('approve-execute')) choices.push({value: 'execute', key: 'e', label: result.outcome === 'approved_not_started' ? '继续执行此计划' : '批准并执行'});
    choices.push({value: 'body', key: 'b', label: '查看正文'});
    if (result.target && client.capabilities?.().includes('plan-revision')) choices.push({value: 'revise', key: 'r', label: '修改计划 / 修订反馈'});
    if (result.target && client.capabilities?.().includes('plan-revision-history')) choices.push({value: 'revisions', key: 'h', label: '修订记录 / 找回反馈'});
    if (!card.problem && result.available_actions.includes('approve-only')) choices.push({value: 'approve', key: 'a', label: '仅批准'});
    if (!card.problem && result.available_actions.includes('dismiss')) choices.push({value: 'dismiss', key: 'd', label: '关闭审阅'});
    if (result.replacement_target) choices.push({value: 'replacement', key: 'v', label: '查看新版，重新审阅'});
    choices.push({value: 'query', key: 'q', label: '查询结果'});
  }
  const select = (key: string, move = 0) => {
    const index = choices.findIndex(choice => choice.value === current.current.choice);
    const choice = move ? choices[index < 0 ? move > 0 ? 0 : choices.length - 1 : (index + move + choices.length) % choices.length] : choices.find(choice => choice.key === key);
    if (choice) {
      update({...current.current, choice: choice.value});
      callbacks.current.onMessage(`已选择：${choice.label}；Enter 确认`);
    }
  };
  const confirm = () => {
    const state = current.current;
    const card = state.current ? state.cards.get(state.current) : undefined;
    if (!card || card.pending) return;
    if (!state.choice) {callbacks.current.onMessage('先选择动作，再按 Enter 确认'); return;}
    if (state.choice === 'query') {send('review', card.result.target!, card.cursor, card.previous); return;}
    if (state.choice === 'body') {callbacks.current.onMessage(''); update({...state, body: true, choice: null}); return;}
    if (state.choice === 'replacement') {send('review', card.result.replacement_target!, undefined, undefined, true); return;}
    if (state.choice === 'revise') {callbacks.current.onRevise(card.result.target!, card.result.summary?.objective ?? ''); blur(); return;}
    if (state.choice === 'revisions') {callbacks.current.onRevise(card.result.target!, card.result.summary?.objective ?? '', true); blur(); return;}
    if (!callbacks.current.canDecide()) {callbacks.current.onMessage('当前任务运行中；结束后查询计划再操作'); return;}
    send(({execute: 'approve-execute', approve: 'approve', dismiss: 'dismiss'} as const)[state.choice], card.result.target!);
  };
  const page = (direction: number) => {
    const state = current.current;
    const card = state.current ? state.cards.get(state.current) : undefined;
    if (!card || card.pending || card.result.outcome === 'unavailable') return;
    const cursor = direction > 0 ? card.next : card.previous.at(-1);
    if (cursor === null || cursor === undefined) {callbacks.current.onMessage(direction > 0 ? '已到正文末页' : '已到正文首页'); return;}
    send('review', card.result.target!, cursor, direction > 0 ? [...card.previous, card.cursor] : card.previous.slice(0, -1));
  };
  const cycle = (direction: number) => {
    const state = current.current;
    const keys = [...state.cards.keys()];
    if (!keys.length) return;
    const index = keys.indexOf(state.current!);
    callbacks.current.onMessage('');
    update({...state, current: keys[(index + direction + keys.length) % keys.length], choice: null, body: false});
  };
  const showRevision = (source: ReviewRef, target: ReviewRef, activate = false) => {
    const key = reviewKey(source); const state = current.current; const card = state.cards.get(key);
    if (card) {
      const result: PlanReviewResult = {outcome: 'unavailable', target: source, replacement_target: target,
        code: 'superseded', message: `修订已提交，新版 v${target.plan_version} 待审阅`, launch: null,
        summary: null, body_page: '', next_cursor: null, available_actions: []};
      update({...state, cards: new Map(state.cards).set(key, {...card, result, problem: null, pending: null})});
      callbacks.current.onCard(result);
    }
    send('review', target, undefined, undefined, activate, key);
  };
  const act = (value: Choice) => {
    const choice = choices.find(item => item.value === value);
    if (!choice) {callbacks.current.onMessage('当前回执不支持该动作'); return;}
    update({...current.current, choice: value});
    confirm();
  };
  return {card: card?.result ?? null, problem: card?.problem, pageNumber: (card?.previous.length ?? 0) + 1, lastPage: card?.next === null,
    key: state.current, pending: Boolean(card?.pending), decisionPending: [...pending.current.values()].some(request => request.action !== 'review'),
    focused: state.focused, body: state.body, bodyText: card?.body ?? '', cursor: card?.cursor ?? 0,
    choice: state.choice, choices, discover, receive, focus, blur, select, confirm, act, page, actions, cycle, showRevision};
}
