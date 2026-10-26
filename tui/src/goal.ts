import type {RunResult} from './protocol.js';

export type GoalMode = {
  objective: string;
  active: boolean;
  completed: boolean;
  segment: number;
  maxSegments: number;
  startedAt: number;
  maxSeconds: number;
  totalTokens: number;
  totalCostUsd: number;
  maxTotalTokens: number;
  maxTotalCostUsd: number;
  pausedReason: string | null;
};

export type GoalDecision = {
  goal: GoalMode;
  continue: boolean;
  prompt?: string;
  notice: string;
};

const RESOURCE_STOPS = new Set(['max_run_seconds', 'token_budget', 'cost_budget', 'max_steps']);
const envInt = (name: string, fallback: number) => {
  const value = Number(process.env[name]);
  return Number.isFinite(value) && value >= 0 ? value : fallback;
};

export const GOAL_MAX_SEGMENTS = Math.max(1, envInt('VIBE_GOAL_MAX_SEGMENTS', 8));
export const GOAL_MAX_SECONDS = Math.max(60, envInt('VIBE_GOAL_SECONDS', 4 * 60 * 60));
export const GOAL_MAX_TOKENS = envInt('VIBE_GOAL_MAX_TOKENS', 0);
export const GOAL_MAX_COST_USD = envInt('VIBE_GOAL_MAX_COST_USD', 0);

export function goalLabel(goal: GoalMode): string {
  return `第 ${goal.segment}/${goal.maxSegments} 段`;
}

export function startGoal(objective: string, now = Date.now()): GoalMode {
  return {
    objective: objective.trim(), active: true, completed: false,
    segment: 1, maxSegments: GOAL_MAX_SEGMENTS, startedAt: now, maxSeconds: GOAL_MAX_SECONDS,
    totalTokens: 0, totalCostUsd: 0, maxTotalTokens: GOAL_MAX_TOKENS, maxTotalCostUsd: GOAL_MAX_COST_USD,
    pausedReason: null,
  };
}

function usageTokens(result: RunResult): number {
  const usage = result.usage ?? {};
  const total = Number(usage.total_tokens);
  if (Number.isFinite(total) && total > 0) return total;
  return ['prompt_tokens', 'completion_tokens', 'input_tokens', 'output_tokens']
    .reduce((sum, key) => sum + (Number(usage[key]) || 0), 0);
}

export function goalPrompt(goal: GoalMode, reason: string | null = null): string {
  if (goal.segment === 1) {
    return [
      '[GOAL MODE]',
      `目标：${goal.objective}`,
      '这是一个可能跨多个运行段的长任务。先核对工作区，再分步执行；不要停在计划或空泛总结上。',
      '目标真正达成后给出最终答复；暂时无法推进时说明具体阻塞。',
      '本模式会在运行段因时间/预算暂停时自动续段。',
    ].join('\n');
  }
  return [
    `[GOAL MODE · ${goalLabel(goal)}]`,
    `继续目标：${goal.objective}`,
    `上一段因 ${reason ?? '资源暂停'} 结束。先核对实际工作区与已保存进度，再继续未完成部分；不要重复已完成的工作。`,
    '目标真正达成后给出最终答复；暂时无法推进时说明具体阻塞。',
  ].join('\n');
}

export function goalDecision(goal: GoalMode, result: RunResult, now = Date.now()): GoalDecision {
  const totalTokens = goal.totalTokens + usageTokens(result);
  const totalCostUsd = goal.totalCostUsd + (Number(result.cost_usd) || 0);
  const base = {...goal, totalTokens, totalCostUsd};
  const noticePrefix = `目标模式 · ${goalLabel(goal)}`;
  if (result.status === 'done') {
    return {
      goal: {...base, active: false, completed: true, pausedReason: null},
      continue: false,
      notice: `${noticePrefix} 完成`,
    };
  }
  if (result.status !== 'stopped' || !RESOURCE_STOPS.has(result.stop_reason)) {
    return {
      goal: {...base, active: false, pausedReason: result.stop_reason || result.status},
      continue: false,
      notice: `${noticePrefix} 暂停：${result.stop_reason || result.status}；输入 /goal resume 续段`,
    };
  }
  if (goal.segment >= goal.maxSegments) {
    return {
      goal: {...base, active: false, pausedReason: 'goal_max_segments'},
      continue: false, notice: `${noticePrefix} 达到最大段数；输入 /goal resume 继续`,
    };
  }
  if (now - goal.startedAt >= goal.maxSeconds * 1000) {
    return {
      goal: {...base, active: false, pausedReason: 'goal_max_seconds'},
      continue: false, notice: `${noticePrefix} 达到目标总时长；输入 /goal resume 继续`,
    };
  }
  if (base.maxTotalTokens > 0 && totalTokens >= base.maxTotalTokens) {
    return {
      goal: {...base, active: false, pausedReason: 'goal_max_tokens'},
      continue: false, notice: `${noticePrefix} 达到目标总 token 上限；输入 /goal resume 继续`,
    };
  }
  if (base.maxTotalCostUsd > 0 && totalCostUsd >= base.maxTotalCostUsd) {
    return {
      goal: {...base, active: false, pausedReason: 'goal_max_cost'},
      continue: false, notice: `${noticePrefix} 达到目标总费用上限；输入 /goal resume 继续`,
    };
  }
  const next = {...base, active: true, segment: goal.segment + 1, pausedReason: null};
  return {
    goal: next, continue: true, prompt: goalPrompt(next, result.stop_reason),
    notice: `目标模式：第 ${next.segment}/${next.maxSegments} 段自动开始（上一段 ${result.stop_reason}）`,
  };
}

export function resumeGoal(goal: GoalMode, now = Date.now()): GoalMode {
  const next = {...goal, active: true, completed: false, pausedReason: null};
  // 暂停后手动恢复也算新的一段，避免无限重复同一段预算。
  if (next.segment < next.maxSegments) next.segment += 1;
  return next;
}

export function goalStatusText(goal: GoalMode): string {
  if (goal.completed) return `目标完成 · ${goalLabel(goal)}`;
  if (!goal.active) return `目标暂停 · ${goalLabel(goal)}${goal.pausedReason ? ` · ${goal.pausedReason}` : ''}`;
  return `目标进行中 · ${goalLabel(goal)}`;
}
