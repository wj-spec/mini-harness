import {randomUUID} from 'node:crypto';
import {useEffect, useRef, useState} from 'react';
import type {RunResult} from './protocol.js';
import {openFollowupStore, type FollowupData, type FollowupScope} from './followup-store.js';

export function useFollowups(scope: FollowupScope) {
  const key = JSON.stringify(scope);
  type State = FollowupData & {open: boolean; persisted: boolean; writable: boolean; problem: string; live: boolean};
  const empty: State = {items: [], inflight: null, paused: null, open: false, persisted: false, writable: true, problem: '', live: false};
  const [state, setState] = useState<State>(empty);
  const current = useRef(state);
  const store = useRef<ReturnType<typeof openFollowupStore> | null>(null);
  const loaded = useRef('');
  const update = (next: State) => {current.current = next; setState(next);};
  const restore = () => {
    store.current?.close(); store.current = null; loaded.current = key;
    if (!scope.sessionPath) {update(empty); return;}
    try {
      const opened = openFollowupStore(scope); store.current = opened;
      update({...empty, ...opened.data, persisted: true, writable: opened.writable,
        open: current.current.open, paused: opened.data.inflight ? '结果待核对，队列已暂停' : opened.data.items.length ? '已恢复，暂停等待你继续' : null,
        problem: opened.writable ? '' : '另一窗口正在管理待办 · 只读'});
    } catch (error) {update({...current.current, live: false, writable: false, problem: `待办未能读取：${String(error)}`});}
  };
  useEffect(() => {update(empty); restore(); return () => {store.current?.close(); store.current = null;};}, [key]);
  const commit = (next: State) => {
    if (loaded.current !== key || !current.current.writable) return false;
    try {
      store.current?.write({items: next.items, paused: next.paused, inflight: next.inflight});
      update({...next, problem: ''}); return true;
    } catch (error) {update({...current.current, paused: '保存失败，已暂停', problem: `待办未保存：${String(error)}`}); return false;}
  };
  const pause = (reason: string) => {
    if (current.current.items.length || current.current.inflight) commit({...current.current, paused: reason});
  };
  return {...state, count: state.items.length + Number(Boolean(state.inflight)),
    retry: () => {
      if (store.current?.writable) commit({...current.current, paused: current.current.inflight ? '结果待核对，队列已暂停' : '已恢复，暂停等待你继续'});
      else restore();
    },
    add: (text: string) => commit({...current.current, items: [...current.current.items, {id: randomUUID(), text}]}),
    edit: (id: string, text: string) => commit({...current.current, items: current.current.items.map(item => item.id === id ? {...item, text} : item)}),
    remove: (id: string) => {
      const items = current.current.items.filter(item => item.id !== id);
      return commit({...current.current, items, paused: items.length || current.current.inflight ? current.current.paused : null});
    },
    acknowledge: () => !current.current.live && Boolean(current.current.inflight) &&
      commit({...current.current, inflight: null, paused: '已移出追踪记录，待办仍暂停'}),
    show: () => {pause(current.current.paused ?? '查看中，等待你继续'); update({...current.current, open: true});},
    close: () => update({...current.current, open: false}),
    pause,
    disconnected: (reason = '连接已断开，结果未知；待办保留') => {update({...current.current, live: false}); pause(reason);},
    rejected: (requestId: string) => {
      const inflight = current.current.inflight;
      if (!inflight || inflight.requestId !== requestId) return false;
      update({...current.current, live: false});
      commit({...current.current, inflight: null,
        items: [{id: inflight.id, text: inflight.text}, ...current.current.items], paused: '任务未启动，等待你重试'});
      return true;
    },
    resume: () => !current.current.inflight && !current.current.problem && commit({...current.current, paused: null, open: false}),
    settle: (result: RunResult, failedVerification = false, requestId?: string) => {
      if (current.current.inflight && current.current.inflight.requestId !== requestId) return false;
      const reason = result.status === 'cancelled' ? '已取消，待办保留' : result.status !== 'done' ? '上一轮未完成，待办保留' :
        result.verified === false || result.completion?.allowed === false || failedVerification && result.verified !== true ? '验收未通过，待办保留' :
          result.stop_reason === 'plan_submitted' ? '计划待审阅，待办保留' : null;
      update({...current.current, live: false});
      return commit({...current.current, inflight: null, paused: reason ?? current.current.paused}) && !reason;
    },
    start: (send: (text: string, requestId?: string) => string) => {
      const value = current.current;
      if (value.open || value.paused || value.problem || value.inflight || !value.writable || !value.items.length) return;
      const first = value.items[0]!;
      if (store.current) {
        const requestId = randomUUID();
        if (!commit({...value, items: value.items.slice(1), inflight: {...first, requestId}, live: true})) return;
        try {
          if (send(first.text, requestId) !== requestId) throw new Error('后端请求身份不一致');
        } catch (error) {
          update({...current.current, live: false}); pause(`发送结果待核对：${String(error)}`);
        }
      } else {
        try {
          send(first.text);
          commit({...current.current, items: current.current.items.filter(item => item.id !== first.id)});
        } catch (error) {pause(`后续消息未能启动：${String(error)}`);}
      }
    },
  };
}
