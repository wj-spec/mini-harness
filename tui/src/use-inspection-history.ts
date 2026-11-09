import {useEffect, useRef, useState} from 'react';
import type {BackendClient} from './backend.js';
import {inspectionCursor, type ServerEvent} from './protocol.js';
import {validInspectionSnapshot, type InspectionSnapshot} from './inspection.js';

export function useInspectionHistory(client: BackendClient, workspace: string, sessionId: string, closed: boolean, taskId?: string) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState<{records: InspectionSnapshot[]; taskId?: string; cursor?: string; previous: (string | undefined)[]; next: string | null; skipped: number}>({records: [], previous: [], next: null, skipped: 0});
  const [loading, setLoading] = useState(false);
  const [problem, setProblem] = useState('');
  type Query = {cursor?: string; previous: (string | undefined)[]; taskId?: string};
  const pending = useRef<(Query & {id: string; workspace: string; sessionId: string; timer: ReturnType<typeof setTimeout>}) | null>(null);
  const retryQuery = useRef<Query>({previous: []});
  const ids = useRef(new Set<string>());
  const finish = (error = '') => {
    if (pending.current) clearTimeout(pending.current.timer);
    pending.current = null; setLoading(false); setProblem(error);
  };
  const request = (query: Query) => {
    if (pending.current) return;
    retryQuery.current = query;
    if (closed || !client.capabilities?.().includes('inspection-history')) {setProblem('后端未连接或不支持历史结果'); return;}
    try {
      const args = {...(query.taskId ? {task_id: query.taskId} : {}), ...(query.cursor ? {cursor: query.cursor} : {})};
      const id = client.command('inspections', Object.keys(args).length ? args : undefined);
      ids.current.add(id);
      while (ids.current.size > 128) ids.current.delete(ids.current.values().next().value!);
      pending.current = {...query, id, workspace, sessionId, timer: setTimeout(() => finish('历史结果读取超时，可重试'), 10_000)};
      setLoading(true); setProblem('');
    } catch {finish('历史结果请求未送达，可重试');}
  };
  useEffect(() => {
    setOpen(false); setPage({records: [], previous: [], next: null, skipped: 0}); setLoading(false); setProblem('');
    return () => {
      if (pending.current) clearTimeout(pending.current.timer);
      pending.current = null; ids.current.clear();
    };
  }, [client, workspace, sessionId, taskId]);
  const receive = (event: ServerEvent) => {
    if (event.type === 'closed') {if (pending.current) finish('连接已断开，保留已读取的结果'); return false;}
    if (!event.id || !ids.current.has(event.id)) return false;
    const query = pending.current;
    if (!query || query.id !== event.id) return true;
    if (event.type === 'error') finish(event.message);
    else if (event.type === 'inspection_history') {
      if (query.workspace !== workspace || query.sessionId !== sessionId || event.workspace !== workspace || event.session_id !== sessionId ||
          query.taskId !== event.task_id ||
          !Array.isArray(event.records) || event.records.length > 3 || event.records.some(record => !validInspectionSnapshot(record) || record.workspace !== workspace || record.sessionId !== sessionId || query.taskId !== undefined && record.scope.taskId !== query.taskId) ||
          new Set(event.records.map(record => record.scope.requestId)).size !== event.records.length ||
          !Number.isSafeInteger(event.skipped) || event.skipped < 0 || event.next_cursor !== null &&
            (!inspectionCursor(event.next_cursor) || query.cursor !== undefined && event.next_cursor >= query.cursor)) finish('历史结果回执范围不匹配，原页已保留');
      else {
        setPage({records: event.records, taskId: query.taskId, cursor: query.cursor, previous: query.previous, next: event.next_cursor, skipped: event.skipped});
        finish();
      }
    }
    return true;
  };
  return {open, ...page, loading, problem, receive,
    show: (selectedTaskId?: string) => {setOpen(true); request({previous: [], taskId: taskId ?? selectedTaskId});},
    close: () => {setOpen(false); finish();},
    retry: () => request(retryQuery.current),
    older: () => {if (page.next) request({cursor: page.next, previous: [...page.previous, page.cursor], taskId: page.taskId});},
    newer: () => {if (page.previous.length) request({cursor: page.previous.at(-1), previous: page.previous.slice(0, -1), taskId: page.taskId});},
  };
}
