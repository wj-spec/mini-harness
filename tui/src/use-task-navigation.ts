import {useEffect, useRef, useState} from 'react';
import type {BackendClient} from './backend.js';
import {validTaskCatalogPage, type ServerEvent, type TaskCatalogRow} from './protocol.js';

type Query = {cursor?: string; previous: (string | undefined)[]};
export function useTaskNavigation(client: BackendClient, workspace: string, sessionId: string, closed: boolean) {
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState<Query & {records: TaskCatalogRow[]; next: string | null; skipped: number; problems: string[]}>({previous: [], records: [], next: null, skipped: 0, problems: []});
  const [loading, setLoading] = useState(false);
  const [problem, setProblem] = useState('');
  const pending = useRef<(Query & {id: string; workspace: string; sessionId: string; timer: ReturnType<typeof setTimeout>}) | null>(null);
  const lastQuery = useRef<Query>({previous: []});
  const ids = useRef(new Set<string>());
  const finish = (message = '') => {
    if (pending.current) clearTimeout(pending.current.timer);
    pending.current = null; setLoading(false); setProblem(message);
  };
  const request = (query: Query) => {
    if (pending.current) return;
    lastQuery.current = query;
    if (closed || !client.capabilities?.().includes('task-navigation')) {setProblem('后端未连接或不支持任务目录'); return;}
    try {
      const id = client.command('tasks', query.cursor ? {cursor: query.cursor} : {});
      ids.current.add(id); while (ids.current.size > 128) ids.current.delete(ids.current.values().next().value!);
      pending.current = {...query, id, workspace, sessionId, timer: setTimeout(() => finish('任务目录读取超时，可重试'), 10_000)};
      setLoading(true); setProblem('');
    } catch (error) {finish(`任务目录未能读取：${String(error)}`);}
  };
  useEffect(() => {
    setOpen(false); setPage({previous: [], records: [], next: null, skipped: 0, problems: []}); setLoading(false); setProblem('');
    return () => {if (pending.current) clearTimeout(pending.current.timer); pending.current = null; ids.current.clear();};
  }, [client, workspace, sessionId]);
  return {open, ...page, loading, problem,
    show: () => {setOpen(true); request({previous: []});},
    close: () => {setOpen(false); finish();},
    refresh: () => request({cursor: page.cursor, previous: page.previous}),
    retry: () => request(lastQuery.current),
    older: () => {if (page.next) request({cursor: page.next, previous: [...page.previous, page.cursor]});},
    newer: () => {if (page.previous.length) request({cursor: page.previous.at(-1), previous: page.previous.slice(0, -1)});},
    receive: (event: ServerEvent) => {
      if (event.type === 'closed') {if (open || pending.current) finish('连接已断开，保留已读取目录'); return false;}
      if (!event.id || !ids.current.has(event.id)) return false;
      const query = pending.current;
      if (!query || query.id !== event.id) return true;
      if (event.type === 'error') finish(event.message);
      else if (event.type === 'command_result') finish('后端没有返回结构化任务目录');
      else if (event.type === 'task_catalog') {
        if (query.workspace !== workspace || query.sessionId !== sessionId || event.workspace !== workspace || event.session_id !== sessionId ||
            !validTaskCatalogPage(event) || event.next_cursor !== null && event.next_cursor === query.cursor) finish('任务目录回执不匹配，原页保留');
        else {setPage({cursor: query.cursor, previous: query.previous, records: event.records, next: event.next_cursor, skipped: event.skipped, problems: event.problems}); finish();}
      }
      return true;
    },
  };
}
