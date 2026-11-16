import {validRunOutcome} from '../protocol.js';
import {outcomePresentation} from '../outcome.js';
import type {InspectionScope, InspectionVerification} from '../inspection.js';

export type VerificationSnapshot = {run: InspectionScope; verification: InspectionVerification; sessionId: string | null};

export function verificationContent({run, verification: result, sessionId}: VerificationSnapshot): string {
  const presentation = result.outcome ? outcomePresentation({status: result.status ?? 'stopped', stop_reason: result.stopReason, outcome: result.outcome, verified: result.verified}) : null;
  const lines = [run.objective, `会话 ${sessionId ?? '未知'} · 段 ${run.segment ?? '未绑定'}`,
    `任务 ${run.taskId ?? '未绑定'} · 请求 ${run.requestId ?? '未知'}`, `追踪 ${run.traceRunId ?? '未知'}`,
    presentation ? presentation.label : result.verified === true ? '验证已通过' : result.verified === false ? '验证未通过' : '验证结果未知',
    `运行状态 ${result.status ?? run.status}${result.stopReason ? ` · ${result.stopReason}` : ''}`,
    `完成判定：${result.completionAllowed === true ? '通过' : result.completionAllowed === false ? '未通过' : '未知'}`,
    ...result.completionMessages];
  if (result.omittedCommands) lines.push(`仅显示最近 20 条命令，省略 ${result.omittedCommands} 条；完整记录在主区工具详情`);
  if (!result.commands.length) lines.push('此请求尚无命令执行记录；不代表验证通过');
  for (const command of result.commands) {
    lines.push('', `${command.verification ? '验证命令' : '普通命令'} · ${command.id}`, command.command);
    const execution = command.execution;
    lines.push(command.pending ? '等待命令返回' : execution
      ? `${{succeeded: '零退出', failed: '非零退出', timed_out: '超时', cancelled: '已取消', unknown: '执行结果未知'}[execution.status]}${execution.exit_code === null ? '' : ` · exit=${execution.exit_code}`}`
      : command.ok === false ? '调用未成功 · 无执行结果' : '未记录执行结果');
    lines.push(command.output || '（无输出）');
    if (command.truncated) lines.push('输出已截断；完整输出在主区工具详情');
  }
  if (validRunOutcome(result.outcome)) {
    if (result.outcome.files.length) lines.push('', '本任务记录的变更：', ...result.outcome.files);
    if (result.outcome.omitted_files) lines.push('部分变更记录未在摘要列出；完整证据见轮次记录');
  }
  if (result.receipt) lines.push('', '原始运行记录（保留证据）', result.receipt);
  return lines.join('\n');
}

