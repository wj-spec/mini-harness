import assert from 'node:assert/strict';
import {mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {tmpdir} from 'node:os';
import test from 'node:test';
import {goalFilePath, loadGoal, saveGoal} from '../src/goal-store.js';
import {startGoal} from '../src/goal.js';

test('goal state round-trips through the session directory', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-goal-'));
  try {
    const goal = {...startGoal('持久化目标'), taskId: 't-20261103-120000-abcdef'};
    saveGoal(dir, goal);
    const raw = JSON.parse(await readFile(goalFilePath(dir), 'utf8'));
    assert.equal(raw.version, 1);
    assert.equal(raw.objective, '持久化目标');
    assert.equal(raw.active, true);
    assert.equal(raw.taskId, 't-20261103-120000-abcdef');

    const restored = loadGoal(dir);
    assert.equal(restored?.objective, '持久化目标');
    assert.equal(restored?.active, false, 'a restarted TUI must wait for explicit /goal resume');
    assert.equal(restored?.pausedReason, 'session_restored');
    assert.equal(restored?.taskId, 't-20261103-120000-abcdef');

    saveGoal(dir, {...goal, active: false, completed: true, pausedReason: null});
    assert.equal(loadGoal(dir)?.completed, true);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('invalid or missing goal files are ignored', async () => {
  const dir = await mkdtemp(path.join(tmpdir(), 'mini-harness-goal-'));
  try {
    assert.equal(loadGoal(dir), null);
    await writeFile(goalFilePath(dir), '{"version":1,"objective":42', 'utf8');
    assert.equal(loadGoal(dir), null);
  } finally {
    await rm(dir, {recursive: true, force: true});
  }
});

test('restore aligns goal budget and usage from backend task snapshot',async()=>{
 const dir=await mkdtemp(path.join(tmpdir(),'mini-harness-goal-budget-'));
 try{
  const taskId='t-20261103-120000-abcdef';
  const goal={...startGoal('backend goal'),taskId};
  saveGoal(dir,goal);
  const {mkdir}=await import('node:fs/promises');
  await mkdir(path.join(dir,'tasks',taskId),{recursive:true});
  await writeFile(path.join(dir,'tasks',taskId,'meta.json'),JSON.stringify({
   task_id:taskId,kind:'goal',objective:'backend goal',status:'paused',created_at:new Date(goal.startedAt).toISOString(),
   goal_limits:{max_segments:3,max_seconds:600,max_tokens:500,max_cost_usd:1},
   segments:[{segment:1,status:'stopped'},{segment:2,status:'stopped'}]}));
  await writeFile(path.join(dir,'budget.jsonl'),JSON.stringify({task_id:taskId,entry_kind:'llm',tokens_in:60,tokens_out:40,cost_usd:0.2})+'\n');
  const restored=loadGoal(dir)!;
  assert.equal(restored.active,false);assert.equal(restored.segment,2);assert.equal(restored.maxSegments,3);
  assert.equal(restored.totalTokens,100);assert.equal(restored.totalCostUsd,0.2);
 }finally{await rm(dir,{recursive:true,force:true});}
});
