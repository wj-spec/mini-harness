import {existsSync, mkdirSync, readFileSync, renameSync, writeFileSync} from 'node:fs';
import path from 'node:path';
import {alignGoalBudget,type GoalMode} from './goal.js';
import {validGoalLimits} from './goal-budget.js';

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
    const restored=restoreTaskBudget(sessionPath,goal);
    return restored.active ? {...restored, active: false, pausedReason: 'session_restored'} : restored;
  } catch {
    return null;
  }
}

function restoreTaskBudget(sessionPath:string,goal:GoalMode):GoalMode {
 if(!goal.taskId||!/^t-[a-zA-Z0-9-]+$/.test(goal.taskId))return goal;
 const target=path.join(sessionPath,'tasks',goal.taskId,'meta.json');
 if(!existsSync(target))return goal;
 try{
  const task=JSON.parse(readFileSync(target,'utf8'));
  if(task.task_id!==goal.taskId||task.kind!=='goal'||task.objective!==goal.objective)throw new Error('identity');
  if(task.goal_limits===undefined)return goal;
  if(!validGoalLimits(task.goal_limits)||!Number.isFinite(Date.parse(task.created_at))||!Array.isArray(task.segments))throw new Error('budget');
  let tokens=0,cost=0;
  const ledger=path.join(sessionPath,'budget.jsonl');
  if(existsSync(ledger))for(const line of readFileSync(ledger,'utf8').split('\n')){
   let entry;try{entry=JSON.parse(line);}catch{continue;}
   if(entry.task_id!==goal.taskId||!['llm','tool','approval_wait','run_close'].includes(entry.entry_kind))continue;
   const values=[Number(entry.tokens_in??0),Number(entry.tokens_out??0),Number(entry.cost_usd??0)];
   if(values.some(n=>!Number.isFinite(n)||n<0))throw new Error('ledger');
   tokens+=values[0]!+values[1]!;cost+=values[2]!;
  }
  const aligned=alignGoalBudget(goal,{limits:task.goal_limits,started_at:task.created_at,used_tokens:tokens,used_cost_usd:cost},Math.max(1,task.segments.length));
  return {...aligned,active:false,completed:task.status==='completed',pausedReason:task.status==='completed'?null:'session_restored'};
 }catch{return {...goal,active:false,pausedReason:'目标预算记录无法核对；后端会阻止不安全的续做'};}
}
