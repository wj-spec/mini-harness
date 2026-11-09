import {useEffect, useRef, useState} from 'react';
import type {BackendClient} from './backend.js';
import {validPermissionOverview, type PermissionOverview, type ServerEvent} from './protocol.js';

export type PermissionPanel = ReturnType<typeof usePermissionPanel>;

export function usePermissionPanel(client: BackendClient, workspace: string, sessionId: string, closed: boolean) {
  const [open, setOpen] = useState(false);
  const [overview, setOverview] = useState<PermissionOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [problem, setProblem] = useState('');
  const [flash, setFlash] = useState('');
  const pending = useRef<{id: string; kind: 'list' | 'revoke'; workspace: string; sessionId: string; timer: ReturnType<typeof setTimeout>} | null>(null);
  const lastQuery = useRef<'list' | 'revoke'>('list');
  const ids = useRef(new Set<string>());
  const finish = (message = '') => {
    if (pending.current) clearTimeout(pending.current.timer);
    pending.current = null; setLoading(false); setRevoking(null); setProblem(message);
  };
  const send = (kind: 'list' | 'revoke', grantId?: string) => {
    if (pending.current) return;
    lastQuery.current = kind;
    if (closed || !client.capabilities?.().includes('permission-panel')) {setProblem('后端未连接或不支持权限面板'); return;}
    try {
      const id = client.command('permissions', kind === 'list' ? {action: 'list'} : {action: 'revoke', grant_id: grantId!});
      ids.current.add(id); while (ids.current.size > 128) ids.current.delete(ids.current.values().next().value!);
      pending.current = {id, kind, workspace, sessionId, timer: setTimeout(() => finish(kind === 'revoke' ? '撤销回执超时，原列表保留；可刷新核对' : '权限读取超时，可重试'), 10_000)};
      setLoading(true); setProblem('');
      if (kind === 'revoke') setRevoking(grantId ?? null);
    } catch (error) {finish(`权限操作未送达：${String(error)}`);}
  };
  useEffect(() => {
    setOpen(false); setOverview(null); setLoading(false); setRevoking(null); setProblem(''); setFlash('');
    return () => {if (pending.current) clearTimeout(pending.current.timer); pending.current = null; ids.current.clear();};
  }, [client, workspace, sessionId]);
  return {
    open, overview, loading, revoking, problem, flash,
    show: () => {setOpen(true); send('list');},
    close: () => {setOpen(false); finish();},
    refresh: () => send('list'),
    retry: () => {if (lastQuery.current === 'revoke') send('list'); else send('list');},
    revoke: (grantId: string) => send('revoke', grantId),
    receive: (event: ServerEvent) => {
      if (event.type === 'closed') {if (open || pending.current) finish('连接已断开，保留已读取权限'); return false;}
      if (!event.id || !ids.current.has(event.id)) return false;
      const query = pending.current;
      if (!query || query.id !== event.id) return true;
      if (event.type === 'error') {finish(event.message); return true;}
      if (event.type === 'command_result') {finish('后端没有返回结构化权限信息'); return true;}
      if (event.type === 'permission_overview') {
        if (query.workspace !== workspace || query.sessionId !== sessionId || event.workspace !== workspace || event.session_id !== sessionId ||
            !validPermissionOverview(event)) {finish('权限回执不匹配，原列表保留'); return true;}
        setOverview(event);
        setFlash(event.revoked ? `已撤销 ${event.revoked.kind}${event.revoked.tool ? ' · ' + event.revoked.tool : ''} · ${event.revoked.target}；剩余 ${event.grants.length} 条进程授权` : '');
        finish();
      }
      return true;
    },
  };
}
