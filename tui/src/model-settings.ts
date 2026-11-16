export type ModelDraft = {model: string; base_url: string; api_key: string; trust_env: boolean; revision?: string};
export type ModelAction = 'save' | 'test' | 'apply';
export type ModelArgs = {action: 'list'} | (ModelDraft & {action: ModelAction});
export type ModelOverview = {model: string; base_url: string; provider: string; trust_env: boolean; key_present: boolean;
  sources: Record<string,string>; path: string; revision: string; saved_model: string; applied: boolean;
  message: string; workspace: string; session_id: string};
export function validModelOverview(v: unknown): v is ModelOverview {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return false;
  const x = v as Record<string,unknown>;
  if ('api_key' in x || !['model','base_url','provider','path','revision','saved_model','message','workspace','session_id'].every(k => typeof x[k] === 'string' && String(x[k]).length <= 8192)) return false;
  return typeof x.trust_env === 'boolean' && typeof x.key_present === 'boolean' && typeof x.applied === 'boolean' &&
    /^[a-f0-9]{64}$/.test(String(x.revision)) && Boolean(x.sources && typeof x.sources === 'object' && !Array.isArray(x.sources) &&
      ['model','base_url','api_key','trust_env','provider'].every(k => typeof (x.sources as Record<string,unknown>)[k] === 'string'));
}
export function validateModelArgs(v: unknown): asserts v is ModelArgs {
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw new Error('模型设置参数无效');
  const x = v as Record<string,unknown>;
  if (x.action === 'list' && Object.keys(x).length === 1) return;
  if (!['save','test','apply'].includes(String(x.action)) ||
      Object.keys(x).some(k => !['action','model','base_url','api_key','trust_env','revision'].includes(k)) ||
      !['model','base_url','api_key'].every(k => typeof x[k] === 'string' && String(x[k]).length <= 4096 && !/[\x00-\x1f\x7f]/.test(String(x[k]))) ||
      typeof x.trust_env !== 'boolean' || (x.revision !== undefined && (typeof x.revision !== 'string' || !/^[a-f0-9]{64}$/.test(x.revision)))) throw new Error('模型设置参数无效');
}
