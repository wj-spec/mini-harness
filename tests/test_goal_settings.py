import asyncio
import json
import pytest

from app.runtime.goal_limits import validate_goal_limits, goal_limits_from_env, goal_limit_reason
from app.runtime.goal_settings import GoalSettings
from app.config import Config
from app.safety.policy import ApprovalDecision
from app.runtime.tasks import TaskStore
from app.runtime.task_runtime import TaskRuntime, TaskBudgetExceeded
from app.session.store import SessionStore

LIMITS = dict(max_segments=2, max_seconds=600, max_tokens=100, max_cost_usd=1)


@pytest.mark.parametrize("key,value", [("max_segments", 0), ("max_segments", 1.5),
    ("max_seconds", 59), ("max_tokens", -1), ("max_tokens", True), ("max_cost_usd", float("inf"))])
def test_invalid_goal_limits(key, value):
    with pytest.raises(ValueError):
        validate_goal_limits({**LIMITS, key: value})


def test_goal_settings_save_apply_and_precedence(tmp_path):
    from test_stdio import agent_for
    agent = agent_for(tmp_path, [])
    async def allow(call, preview):
        return ApprovalDecision(True, "once")
    agent.approval.handler = allow
    s = GoalSettings(Config(workspace=tmp_path), agent, path=tmp_path / ".env", environ={})
    s.path.write_text("VIBE_API_KEY=private\nVIBE_MAX_STEPS=4\n")
    asyncio.run(s.save({"scope":"goal", "limits":LIMITS, "revision":s.revision()}))
    assert s.values() != LIMITS
    assert "VIBE_API_KEY=private" in s.path.read_text() and "VIBE_MAX_STEPS=4" in s.path.read_text()
    assert s.path.stat().st_mode & 0o777 == 0o600
    s.apply({"scope":"goal", "limits":LIMITS})
    assert s.values() == LIMITS
    overridden = GoalSettings(Config(workspace=tmp_path), agent, environ={"VIBE_GOAL_MAX_SEGMENTS":"3"})
    overridden.apply({"scope":"goal","limits":LIMITS})
    assert overridden.values()["max_segments"] == 3


def test_goal_settings_denial_and_stale_file(tmp_path):
    from test_stdio import agent_for
    agent = agent_for(tmp_path, [])
    async def deny(call, preview):
        return ApprovalDecision(False, "deny")
    agent.approval.handler = deny
    s = GoalSettings(Config(workspace=tmp_path), agent, path=tmp_path / ".env", environ={})
    with pytest.raises(ValueError):
        asyncio.run(s.save({"scope":"goal","limits":LIMITS,"revision":s.revision()}))
    assert not s.path.exists()
    rev = s.revision()
    s.path.write_text("external=1\n")
    with pytest.raises(ValueError, match="变化"):
        asyncio.run(s.save({"scope":"goal","limits":LIMITS,"revision":rev}))
    assert s.path.read_text() == "external=1\n"


def test_snapshot_and_resume_gate_survive_default_changes(tmp_path):
    from app.runtime.agent import AgentResult
    session = SessionStore.new(tmp_path, "fake")
    store = TaskStore(session.root)
    runtime = TaskRuntime(store, workspace=tmp_path)
    runtime.goal_defaults = lambda: LIMITS
    run = runtime.prepare_run("r1", {"kind":"goal", "objective":"goal"})
    assert run.record.extra["goal_limits"] == LIMITS
    runtime.finish_run(run, AgentResult("pause",1,"stopped",stop_reason="max_steps"))
    runtime.goal_defaults = lambda: {**LIMITS, "max_segments":8}
    second = runtime.prepare_run("r2", {"kind":"goal","task_id":run.task_id,"objective":"goal"})
    runtime.finish_run(second, AgentResult("pause",1,"stopped",stop_reason="max_steps"))
    with pytest.raises(TaskBudgetExceeded) as error:
        runtime.prepare_run("r3", {"kind":"goal","task_id":run.task_id,"objective":"goal"})
    assert error.value.check["reason"] == "goal_max_segments"
    assert len(store.get(run.task_id).segments) == 2
    assert not (store.task_dir(run.task_id) / "lock").exists()


@pytest.mark.parametrize("totals,now,reason", [
    ({"tokens_total":100,"cost_usd":0},None,"goal_max_tokens"),
    ({"tokens_total":0,"cost_usd":1},None,"goal_max_cost_usd"),
    ({"tokens_total":0,"cost_usd":0},700,"goal_max_seconds"),
])
def test_goal_gate_checks_accumulation_and_original_clock(tmp_path, totals, now, reason):
    from datetime import datetime, timedelta
    store = TaskStore(tmp_path)
    task = store.create(kind="goal",objective="goal",workspace=str(tmp_path),goal_limits=LIMITS)
    when = datetime.fromisoformat(task.created_at) + timedelta(seconds=now or 1)
    assert goal_limit_reason(task, totals, now=when) == reason


def test_legacy_matching_budget_is_frozen_not_new_default(tmp_path):
    from app.runtime.agent import AgentResult
    session = SessionStore.new(tmp_path,"fake")
    store = TaskStore(session.root)
    task = store.create(kind="goal",objective="legacy",workspace=str(tmp_path),mode="goal")
    (session.root / "goal.json").write_text(json.dumps(dict(version=1,taskId=task.task_id,
        objective=task.objective,maxSegments=3,maxSeconds=900,maxTotalTokens=500,maxTotalCostUsd=2)))
    runtime = TaskRuntime(store,workspace=tmp_path)
    runtime.goal_defaults = lambda: LIMITS
    run = runtime.prepare_run("r1",{"kind":"goal","task_id":task.task_id,"objective":"legacy"})
    assert run.record.extra["goal_limits"]["max_segments"] == 3
    assert run.goal_error and "旧目标" in run.goal_error
    runtime.finish_run(run,AgentResult("pause",1,"stopped",stop_reason="max_steps"))


def test_backend_rejects_exhausted_resume_without_model_call(tmp_path, monkeypatch):
    from test_stdio import Harness, agent_for
    from app.runtime.agent import AgentResult
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    agent = agent_for(tmp_path, [])
    calls = []
    async def pause(*args, **kwargs):
        calls.append(1)
        from core.contracts import Message
        agent.history.append(Message("user", args[0]))
        return AgentResult("pause", 1, "stopped", stop_reason="max_steps")
    monkeypatch.setattr(agent, "run", pause)
    session = SessionStore.new(tmp_path,"fake")
    async def scenario():
        h = Harness(agent, Config(workspace=tmp_path), session)
        await h.until("ready")
        h.send("command","settings",command="settings",args={"action":"apply","scope":"goal","limits":{**LIMITS,"max_segments":1}})
        await h.until("command_result","settings")
        h.send("run","r1",text="goal",task={"kind":"goal","objective":"goal"})
        state = await h.until("task_state","r1")
        task_id = state["task"]["task_id"]
        assert state["task"]["goal_budget"]["limits"]["max_segments"] == 1
        await h.until("done","r1")
        h.send("run","r2",text="continue",task={"kind":"goal","objective":"goal","task_id":task_id})
        error = await h.until("error","r2")
        assert error["code"] == "task_budget_exceeded" and "goal_max_segments" in error["message"]
        h.send("command","r3",command="resume")
        assert (await h.until("done","r3"))["result"]["stop_reason"] == "resume_unavailable"
        h.send("run","r4",text="ordinary")
        await h.until("done","r4")
        await h.close()
    asyncio.run(scenario())
    assert len(calls) == 2
    assert len(TaskStore(session.root).list()[0].segments) == 1


def test_worker_continuation_uses_task_snapshot_not_environment(tmp_path, monkeypatch):
    from app.runtime.goals import GoalStore, continue_decision
    from app.runtime.agent import AgentResult
    session = SessionStore.new(tmp_path,"fake")
    runtime = TaskRuntime(TaskStore(session.root),workspace=tmp_path)
    runtime.goal_defaults = lambda: {**LIMITS,"max_segments":1}
    run = runtime.prepare_run("r1",{"kind":"goal","objective":"goal"})
    task = runtime.finish_run(run,AgentResult("pause",1,"stopped",stop_reason="max_steps"))
    goal = GoalStore(session.root).load()
    assert continue_decision(goal,task,"stopped","max_steps",max_segments=999,max_seconds=999999) == (False,"goal_max_segments")


def test_corrupt_snapshot_is_rejected_and_lock_released(tmp_path):
    from app.runtime.task_runtime import TaskRuntimeError
    session = SessionStore.new(tmp_path,"fake")
    store = TaskStore(session.root)
    task = store.create(kind="goal",mode="goal",objective="bad",workspace=str(tmp_path),goal_limits=LIMITS)
    target=store.task_dir(task.task_id)/"meta.json"
    data=json.loads(target.read_text())
    data["goal_limits"]["max_segments"]=0
    target.write_text(json.dumps(data))
    runtime=TaskRuntime(store,workspace=tmp_path)
    with pytest.raises(TaskRuntimeError,match="不会重置"):
        runtime.prepare_run("r1",{"kind":"goal","task_id":task.task_id,"objective":"bad"})
    assert not (store.task_dir(task.task_id)/"lock").exists()
    assert store.get(task.task_id).segments == []


def test_legacy_empty_goal_record_does_not_erase_startup_caps(tmp_path):
    from app.runtime.goals import GoalStore
    from app.runtime.agent import AgentResult
    session=SessionStore.new(tmp_path,"fake")
    store=TaskStore(session.root)
    task=store.create(kind="goal",mode="goal",objective="legacy",workspace=str(tmp_path))
    GoalStore(session.root).ensure_for_task(task)
    runtime=TaskRuntime(store,workspace=tmp_path)
    runtime.goal_initial=dict(LIMITS)
    run=runtime.prepare_run("r1",{"kind":"goal","task_id":task.task_id,"objective":"legacy"})
    assert run.record.extra["goal_limits"]["max_tokens"] == 100
    assert run.record.extra["goal_limits"]["max_cost_usd"] == 1
    runtime.finish_run(run,AgentResult("pause",1,"stopped",stop_reason="max_steps"))
