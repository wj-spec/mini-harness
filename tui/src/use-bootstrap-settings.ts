import {useEffect, useRef, useState} from 'react';
import type {BackendClient} from './backend.js';
import type {ServerEvent} from './protocol.js';
import {validModelOverview, type ModelOverview, type ModelAction, type ModelDraft} from './model-settings.js';

// 仅配置状态：模型概览/保存/检测/取消与“进入项目”。没有会话身份，回执按 workspace 核对。
export function useBootstrapSettings(client: BackendClient, workspace: string) {
  const [overview, setOverview] = useState<ModelOverview | null>(null);
  const [problem, setProblem] = useState('');
  const [problemField, setProblemField] = useState('');
  const [loading, setLoading] = useState(false);
  const [activating, setActivating] = useState(false);
  const pending = useRef<{id: string; timer: ReturnType<typeof setTimeout>} | null>(null);
  const ids = useRef(new Set<string>());
  const finish = (message = '', field = '') => {
    if (pending.current) clearTimeout(pending.current.timer);
    pending.current = null;
    setLoading(false);
    setProblem(message);
    setProblemField(field);
  };
  useEffect(() => () => {if (pending.current) clearTimeout(pending.current.timer);}, [client]);
  const request = (action: ModelAction | 'list', draft?: ModelDraft) => {
    if (pending.current) return;
    if (!client.capabilities?.().includes('model-settings')) {finish('当前后端不支持模型设置；请更新后端后重试'); return;}
    try {
      const id = client.command('model', action === 'list' ? {action} : {...draft!, action});
      ids.current.add(id); while (ids.current.size > 128) ids.current.delete(ids.current.values().next().value!);
      pending.current = {id, timer: setTimeout(() => finish('回执超时；请核对当前配置，勿重复保存或检测'), 120_000)};
      setLoading(true); setProblem(''); setProblemField('');
    } catch (error) {finish(`模型操作未送达：${String(error)}`);}
  };
  const cancel = () => {
    const current = pending.current;
    if (!current) return;
    try {
      client.cancel(current.id);
      setProblem('正在取消…');
    } catch (error) {finish(`取消请求未送达：${String(error)}`);}
  };
  const activate = () => {
    if (pending.current || activating) return;
    if (!client.capabilities?.().includes('config-bootstrap')) {setProblem('后端已不在配置准备状态'); return;}
    try {
      const id = client.command('bootstrap', {action: 'activate'});
      ids.current.add(id);
      pending.current = {id, timer: setTimeout(() => {finish('进入项目超时；配置已保留，可重试'); setActivating(false);}, 60_000)};
      setActivating(true); setProblem(''); setProblemField('');
    } catch (error) {finish(`进入项目失败：${String(error)}`);}
  };
  return {
    overview, problem, problemField, loading, activating, request, cancel, activate,
    show: () => request('list'),
    reset: () => {finish(); setOverview(null); setActivating(false);},
    receive: (event: ServerEvent) => {
      if (event.type === 'closed') {setProblem('连接已断开，配置编辑内容保留；重启后可继续'); return false;}
      if (!event.id || !ids.current.has(event.id)) return false;
      if (event.type === 'bootstrap_activation') {
        if (pending.current) {clearTimeout(pending.current.timer); pending.current = null;}
        setLoading(false); setActivating(true); setProblem(''); setProblemField('');
        return true;
      }
      if (event.type !== 'model_settings' && event.type !== 'error') return false;
      if (event.type === 'error') {
        if (pending.current?.id === event.id) finish(event.message, event.field ?? '');
        setActivating(false);
        return true;
      }
      if (!validModelOverview(event) || event.workspace !== workspace || event.session_id !== '') {
        if (pending.current?.id === event.id) finish('设置回执身份不匹配，原内容保留');
        return true;
      }
      if (pending.current?.id === event.id) finish();
      setOverview(event);
      return true;
    },
  };
}
