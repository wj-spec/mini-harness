export const goalFields=['max_segments','max_seconds','max_tokens','max_cost_usd'] as const;
export type GoalLimits=Record<typeof goalFields[number],number>;
export type GoalBudget={limits:GoalLimits;started_at:string;used_tokens:number;used_cost_usd:number};
export function validGoalLimits(v:unknown):v is GoalLimits{
 if(!v||typeof v!=='object'||Array.isArray(v))return false;
 const x=v as GoalLimits;
 return Object.keys(x).length===4&&goalFields.every(k=>typeof x[k]==='number'&&Number.isFinite(x[k])&&x[k]>=0&&x[k]<=1e12)&&
  Number.isInteger(x.max_segments)&&x.max_segments>=1&&x.max_seconds>=60&&Number.isInteger(x.max_tokens);
}
export function validGoalBudget(v:unknown):v is GoalBudget{
 if(!v||typeof v!=='object')return false;
 const x=v as GoalBudget;
 return validGoalLimits(x.limits)&&typeof x.started_at==='string'&&Number.isFinite(Date.parse(x.started_at))&&
  Number.isSafeInteger(x.used_tokens)&&x.used_tokens>=0&&Number.isFinite(x.used_cost_usd)&&x.used_cost_usd>=0;
}
