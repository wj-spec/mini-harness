import {useEffect, useRef, useState} from 'react';
import type {BackendClient, TaskConnectionTarget} from './backend.js';

export type TaskDraft = {value: string; cursor: number};
export function useTaskConnection(client: BackendClient) {
  const [connection, setConnection] = useState<{client: BackendClient; target: TaskConnectionTarget; stop: boolean} | null>(null);
  const [pending, setPending] = useState(false);
  const [problem, setProblem] = useState('');
  const attempt = useRef<AbortController | null>(null);
  const current = useRef(connection); current.current = connection;
  const drafts = useRef(new Map<string, TaskDraft>());
  const key = (target: TaskConnectionTarget) => JSON.stringify(target);
  const cancel = () => {attempt.current?.abort(); attempt.current = null; setPending(false);};
  useEffect(() => () => {attempt.current?.abort(); void current.current?.client.shutdown();}, [client]);
  return {connection, pending, problem,
    draft: connection ? drafts.current.get(key(connection.target)) ?? {value: '', cursor: 0} : undefined,
    saveDraft: (draft: TaskDraft) => {if (current.current) drafts.current.set(key(current.current.target), draft);},
    cancel,
    returned: () => {setConnection(null); setProblem('已返回原会话；任务草稿在本窗口保留');},
    connect: async (target: TaskConnectionTarget, stop = false) => {
      if (attempt.current || current.current) return;
      if (!client.connectTask) {setProblem('当前客户端不支持工作台内接入'); return;}
      const controller = new AbortController(); attempt.current = controller; setPending(true); setProblem('');
      try {
        const attached = await client.connectTask(target, controller.signal);
        if (controller.signal.aborted || attempt.current !== controller) {await attached.shutdown(); return;}
        setConnection({client: attached, target, stop});
      } catch (error) {if (!controller.signal.aborted) setProblem(`接入失败：${String(error)}；原草稿保留`);}
      finally {if (attempt.current === controller) {attempt.current = null; setPending(false);}}
    },
  };
}
