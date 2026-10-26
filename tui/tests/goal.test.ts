import assert from 'node:assert/strict';
import test from 'node:test';
import {goalDecision, goalLabel, goalPrompt, resumeGoal, startGoal} from '../src/goal.js';
import type {RunResult} from '../src/protocol.js';

function result(status: RunResult['status'], stop_reason = '', text = ''): RunResult {
  return {text, steps: 1, status, stop_reason, usage: {total_tokens: 100}, cost_usd: 0.01, run_id: 'run-1'};
}

test('goal starts in segment one with a long-task prompt', () => {
  const goal = startGoal('修复并验证登录流程');
  assert.equal(goal.active, true);
  assert.equal(goal.segment, 1);
  assert.match(goalPrompt(goal), /\[GOAL MODE\]/);
  assert.match(goalPrompt(goal), /修复并验证登录流程/);
  assert.match(goalLabel(goal), /1\/8/);
});

test('resource pauses automatically continue as a new segment', () => {
  const goal = startGoal('完成重构');
  const decision = goalDecision(goal, result('stopped', 'max_run_seconds'));
  assert.equal(decision.continue, true);
  assert.equal(decision.goal.segment, 2);
  assert.equal(decision.goal.active, true);
  assert.match(decision.prompt ?? '', /\[GOAL MODE · 第 2\/8 段\]/);
});

test('goal completes on done and pauses on non-resource failures', () => {
  const done = goalDecision(startGoal('完成任务'), result('done'));
  assert.equal(done.continue, false);
  assert.equal(done.goal.completed, true);
  assert.equal(done.goal.active, false);

  const paused = goalDecision(startGoal('完成任务'), result('stopped', 'no_progress'));
  assert.equal(paused.continue, false);
  assert.equal(paused.goal.active, false);
  assert.equal(paused.goal.pausedReason, 'no_progress');
});

test('goal segment and total limits stop without a new segment', () => {
  const goal = {...startGoal('长任务'), segment: 8};
  const maxSegments = goalDecision(goal, result('stopped', 'max_run_seconds'));
  assert.equal(maxSegments.continue, false);
  assert.equal(maxSegments.goal.pausedReason, 'goal_max_segments');

  const tokenCapped = goalDecision({...startGoal('长任务'), maxTotalTokens: 50}, result('stopped', 'token_budget'));
  assert.equal(tokenCapped.continue, false);
  assert.equal(tokenCapped.goal.pausedReason, 'goal_max_tokens');
});

test('resume starts a new segment for a paused goal', () => {
  const paused = {...startGoal('继续'), active: false, pausedReason: 'user_stopped'};
  const resumed = resumeGoal(paused);
  assert.equal(resumed.active, true);
  assert.equal(resumed.segment, 2);
});
