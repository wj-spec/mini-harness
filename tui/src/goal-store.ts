import {existsSync, mkdirSync, readFileSync, renameSync, writeFileSync} from 'node:fs';
import path from 'node:path';
import type {GoalMode} from './goal.js';

export const GOAL_FILE = 'goal.json';
const VERSION = 1;
const MAX_OBJECTIVE = 4000;

export type StoredGoal = GoalMode & {version: number; updatedAt: string};

export function goalFilePath(sessionPath: string): string {
  return path.join(sessionPath, GOAL_FILE);
}

export function saveGoal(sessionPath: string, goal: GoalMode, now = new Date()): void {
  if (!sessionPath) return;
  const target = goalFilePath(sessionPath);
  mkdirSync(path.dirname(target), {recursive: true});
  const payload: StoredGoal = {...goal, version: VERSION, updatedAt: now.toISOString()};
  const temp = `${target}.${process.pid}.tmp`;
  writeFileSync(temp, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
  renameSync(temp, target);
}

function valid(value: unknown): value is StoredGoal {
  if (!value || typeof value !== 'object') return false;
  const goal = value as Record<string, unknown>;
  return goal.version === VERSION &&
    typeof goal.objective === 'string' && goal.objective.length > 0 && goal.objective.length <= MAX_OBJECTIVE &&
    typeof goal.active === 'boolean' && typeof goal.completed === 'boolean' &&
    typeof goal.segment === 'number' && Number.isSafeInteger(goal.segment) && goal.segment >= 1 &&
    typeof goal.maxSegments === 'number' && Number.isSafeInteger(goal.maxSegments) && goal.maxSegments >= 1 &&
    typeof goal.startedAt === 'number' && Number.isFinite(goal.startedAt) &&
    typeof goal.maxSeconds === 'number' && Number.isFinite(goal.maxSeconds) &&
    typeof goal.totalTokens === 'number' && Number.isFinite(goal.totalTokens) && goal.totalTokens >= 0 &&
    typeof goal.totalCostUsd === 'number' && Number.isFinite(goal.totalCostUsd) && goal.totalCostUsd >= 0 &&
    typeof goal.maxTotalTokens === 'number' && Number.isFinite(goal.maxTotalTokens) &&
    typeof goal.maxTotalCostUsd === 'number' && Number.isFinite(goal.maxTotalCostUsd) &&
    (goal.pausedReason === null || typeof goal.pausedReason === 'string');
}

/** 恢复时永远不把 active 目标直接续跑：交给用户 /goal resume。 */
export function loadGoal(sessionPath: string): GoalMode | null {
  if (!sessionPath) return null;
  const target = goalFilePath(sessionPath);
  if (!existsSync(target)) return null;
  try {
    const value: unknown = JSON.parse(readFileSync(target, 'utf8'));
    if (!valid(value)) return null;
    const {version: _version, updatedAt: _updatedAt, ...goal} = value;
    return goal.active ? {...goal, active: false, pausedReason: 'session_restored'} : goal;
  } catch {
    return null;
  }
}
