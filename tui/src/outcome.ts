import {validRunOutcome, type RunResult} from './protocol.js';
import type {SemanticTone} from './theme.js';

export type OutcomePresentation = {visible: boolean; label: string; detail: string; tone: SemanticTone; legacy: boolean};
type ResultFacts = Pick<RunResult, 'status' | 'stop_reason' | 'outcome' | 'verified'>;

const reasons: Record<string, string> = {
  max_steps: '达到本段步数上限', max_run_seconds: '达到本段时间上限', token_budget: '达到本段 token 上限',
  cost_budget: '达到本段费用上限', plan_submitted: '计划已提交，等待审阅',
  approval_timeout: '等待审批超时', no_progress: '暂未取得新进展',
  acceptance_failed: '计划检查未通过', acceptance_timeout: '计划检查超时',
  goal_max_segments: '达到目标段数上限', goal_max_seconds: '达到目标时间上限',
  goal_max_tokens: '达到目标 token 上限', goal_max_cost: '达到目标费用上限',
};
export function stopReasonText(reason: string): string {
  return reasons[reason] ?? (reason ? '请查看停止原因' : '');
}
export function outcomePresentation(result: ResultFacts): OutcomePresentation {
  const facts = validRunOutcome(result.outcome) ? result.outcome : null;
  const parts: string[] = [];
  const count = facts ? facts.files.length : 0;
  if (count) parts.push(`修改 ${facts!.omitted_files ? '至少 ' : ''}${count} 个文件`);
  if (facts?.operations) parts.push(`执行 ${facts.operations} 项操作 · 影响请核对`);
  const reason = stopReasonText(result.stop_reason);
  if (result.status !== 'done') {
    const status = result.stop_reason === 'plan_submitted' ? '计划待审阅' :
      {stopped: '任务已暂停', cancelled: '已取消', error: '运行失败', truncated: '本轮未完成'}[result.status];
    return {visible: true, label: [status, ...parts].join(' · '),
      detail: reason || (result.status === 'cancelled' ? '已发生的影响保留，请核对后继续' : '查看验证详情与工具记录'),
      tone: result.status === 'error' ? 'error' : 'warning', legacy: !facts};
  }
  if (!facts) return {visible: true, label: '历史运行记录 · 结果待核对',
    detail: '缺少结构化结果；查看验证详情中的原始记录', tone: 'accent', legacy: true};

  if (facts.denied) parts.push('有操作被拒绝');
  if (facts.tool_errors) parts.push(`${facts.tool_errors} 项工具异常`);
  if (facts.audit_warning) parts.push('环境变化待核对');
  if (facts.prior_unknown) parts.push('部分证据待核对');
  const validation = facts.plan_verification === 'failed' ? '计划检查未通过' :
    facts.verification === 'failed' ? '检查未通过' : facts.verification === 'stale' ? '需重新验证' :
      facts.plan_verification === 'not_run' ? '计划尚未验证' :
        facts.plan_verification === 'passed' ? '计划检查通过' :
          facts.verification === 'passed' ? '检查通过' : count ? '尚未验证' : '';
  if (validation) parts.push(validation);
  const warning = facts.denied || facts.tool_errors > 0 || facts.audit_warning || facts.prior_unknown ||
    facts.operations > 0 || facts.plan_verification === 'failed' || facts.plan_verification === 'not_run' ||
    ['failed', 'stale'].includes(facts.verification) || count > 0 && facts.verification === 'not_run' && facts.plan_verification !== 'passed';
  return {visible: parts.length > 0, label: parts.join(' · ') || '就绪',
    detail: parts.length ? '查看变更与验证详情；检查结果不代表需求已满足' : '可继续提问或开始下一项任务',
    tone: warning ? 'warning' : 'success', legacy: false};
}
