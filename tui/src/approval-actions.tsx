import type {ApprovalDecision} from './protocol.js';
import {ActionButtons, type ActionTarget} from './action-buttons.js';

export type ApprovalAction = ApprovalDecision | 'more' | 'back';
export type ApprovalButton = {action: ApprovalAction; label: string; warning?: boolean};
export type ApprovalTarget = ActionTarget<ApprovalAction>;

export function approvalButtons(decisions: ApprovalDecision[], tool: string, expanded: boolean, columns: number): ApprovalButton[] {
  const compact = columns < 60;
  if (expanded) return [
    {action: 'back' as const, label: '返回'},
    ...(decisions.includes('session') ? [{action: 'session' as const, label: compact ? '规则' : '记住精确规则'}] : []),
    ...(decisions.includes('workspace') ? [{action: 'workspace' as const, label: compact ? '工作区' : '授权此工作区'}] : []),
  ];
  const once = compact ? '允许本次' : tool === 'write_file' || tool === 'edit_file' ? '允许本次写入' : tool === 'bash' ? '允许本次命令' : '允许本次操作';
  return [
    ...(decisions.includes('once') ? [{action: 'once' as const, label: once}] : []),
    ...(decisions.includes('deny') ? [{action: 'deny' as const, label: '拒绝', warning: true}] : []),
    ...(decisions.some(decision => decision === 'session' || decision === 'workspace') ? [{action: 'more' as const, label: compact ? '更多' : '更多授权…'}] : []),
  ];
}

export const ApprovalActions = ActionButtons<ApprovalAction>;
