"""GoalRecord P0 测试（spec: docs/specs/goal-record.md）。"""

from __future__ import annotations

import json

import pytest

from app.runtime.goals import (
    GOAL_SCHEMA_VERSION, GoalCorrupt, GoalError, GoalRecord, GoalStore,
    continue_decision, goal_prompt,
)
from app.runtime.tasks import TaskStore


def _task(tmp_path, session_name="session", **overrides):
    session = tmp_path / session_name
    store = TaskStore(session)
    values = {"kind": "goal", "objective": "完成目标", "workspace": str(tmp_path), "mode": "goal"}
    values.update(overrides)
    return session, store, store.create(**values)


def test_goal_record_roundtrip(tmp_path):
    store = GoalStore(tmp_path)
    record = GoalRecord(
        schema_version=GOAL_SCHEMA_VERSION, goal_id="g-1", task_id="t-1",
        objective="目标", state="running",
        acceptance_criteria=[{"id": "ac1", "text": "测试通过", "confirmed": True}],
        constraints=[{"text": "不要改测试"}],
        segments=[{"segment": 1, "run_id": "r1", "status": "stopped"}],
        open_items=[{"text": "未完成", "status": "open", "source": "model"}],
        budget={"max_goal_tokens": 10, "max_goal_cost_usd": 0, "max_goal_seconds": 0},
        source={"created_by": "user"},
        created_at="2026-11-03T00:00:00+00:00", updated_at="2026-11-03T00:00:00+00:00",
    )
    store.save(record)
    loaded = store.load()
    assert loaded == record
    assert store.load("other") is None


def test_corrupt_record_raises(tmp_path):
    store = GoalStore(tmp_path)
    store.path.write_text("{bad", encoding="utf-8")
    with pytest.raises(GoalCorrupt):
        store.load()


def test_ensure_for_task_derives_goal_id_and_syncs_state(tmp_path):
    session, tasks, task = _task(tmp_path)
    goals = GoalStore(session)
    record = goals.ensure_for_task(task)
    assert record.task_id == task.task_id
    assert record.goal_id.startswith("g-")
    assert record.state == "created"
    assert record.objective == "完成目标"

    tasks.transition(task.task_id, "running")
    assert goals.sync_from_task(tasks.get(task.task_id)).state == "running"
    tasks.transition(task.task_id, "paused")
    assert goals.sync_from_task(tasks.get(task.task_id)).state == "paused"
    tasks.transition(task.task_id, "running")
    tasks.transition(task.task_id, "completed")
    assert goals.sync_from_task(tasks.get(task.task_id)).state == "completed"


def test_segments_sync_preserves_criteria_and_open_items(tmp_path):
    session, tasks, task = _task(tmp_path)
    goals = GoalStore(session)
    record = goals.ensure_for_task(task)
    record.acceptance_criteria = [{"id": "ac1", "text": "测试通过", "confirmed": True}]
    record.open_items = [{"text": "待办", "status": "open", "source": "model"}]
    goals.save(record)

    run = "run-1"
    tasks.start_segment(task.task_id, run_id=run)
    tasks.end_segment(task.task_id, run_id=run, status="stopped", stop_reason="max_run_seconds")
    synced = goals.sync_from_task(tasks.get(task.task_id))
    assert synced.segments[0]["run_id"] == run
    assert synced.segments[0]["stop_reason"] == "max_run_seconds"
    assert synced.acceptance_criteria[0]["id"] == "ac1"
    assert synced.open_items[0]["text"] == "待办"


def test_non_goal_task_rejected(tmp_path):
    session, tasks, task = _task(tmp_path, kind="run", mode="act")
    with pytest.raises(GoalError):
        GoalStore(session).ensure_for_task(task)


def test_legacy_goal_json_migration_keeps_legacy_file(tmp_path):
    session, tasks, task = _task(tmp_path)
    legacy = {"version": 1, "objective": "旧目标", "active": False, "completed": False,
              "segment": 2, "maxSegments": 8, "startedAt": 1, "maxSeconds": 100,
              "totalTokens": 0, "totalCostUsd": 0, "maxTotalTokens": 0,
              "maxTotalCostUsd": 0, "pausedReason": None}
    (session / "goal.json").write_text(json.dumps(legacy), encoding="utf-8")
    record = GoalStore(session).ensure_for_task(task)
    assert record.objective == "旧目标"
    assert record.state == "paused"
    assert record.source["created_by"] == "tui_legacy"
    assert (session / "goal.json").is_file()
    assert (session / "goal-record.json").is_file()


def test_active_other_goal_is_rejected(tmp_path):
    session, tasks, task = _task(tmp_path)
    goals = GoalStore(session)
    goals.ensure_for_task(task)
    other = tasks.create(kind="goal", objective="另一个", workspace=str(tmp_path), mode="goal")
    with pytest.raises(GoalError):
        goals.ensure_for_task(other)

def _record(tmp_path, **overrides):
    _, _, task = _task(tmp_path)
    values = dict(
        schema_version=GOAL_SCHEMA_VERSION, goal_id="g-1", task_id=task.task_id,
        objective="目标", state="running", created_at="2026-11-03T00:00:00+00:00",
        updated_at="2026-11-03T00:00:00+00:00",
    )
    values.update(overrides)
    return task, GoalRecord(**values)


def test_goal_prompt_includes_confirmed_criteria_and_open_items(tmp_path):
    task, record = _record(
        tmp_path,
        acceptance_criteria=[
            {"id": "ac1", "text": "测试通过", "confirmed": True},
            {"id": "ac2", "text": "未确认", "confirmed": False},
        ],
        open_items=[{"text": "待办", "status": "open"}],
        constraints=[{"text": "不要改测试"}],
    )
    prompt = goal_prompt(record, segment=2, stop_reason="max_run_seconds")
    assert "[GOAL MODE · 第 2 段]" in prompt
    assert "测试通过" in prompt and "未确认" not in prompt
    assert "待办" in prompt and "不要改测试" in prompt


def test_continue_decision_only_for_resource_stops(tmp_path):
    from datetime import datetime, timezone
    from datetime import datetime
    now = datetime.fromisoformat("2026-11-03T00:10:00+00:00")
    task, record = _record(tmp_path)
    assert continue_decision(record, task, "stopped", "max_run_seconds", now=now)[0] is True
    assert continue_decision(record, task, "done", "", now=now)[0] is False
    assert continue_decision(record, task, "stopped", "no_progress", now=now)[0] is False
    assert continue_decision(record, task, "cancelled", "", now=now)[0] is False
    # 段数上限
    record.segments = [{"segment": 1}, {"segment": 2}]
    task.segments = [None, None]  # type: ignore[list-item]
    assert continue_decision(record, task, "stopped", "max_run_seconds", max_segments=2, now=now)[1] == "goal_max_segments"
    # 总时长上限
    old = datetime(2026, 11, 1, tzinfo=timezone.utc)
    record.segments = []
    task.segments = []  # type: ignore[assignment]
    assert continue_decision(record, task, "stopped", "max_run_seconds", max_seconds=1, now=datetime(2026, 11, 4, tzinfo=timezone.utc))[1] == "goal_max_seconds"



def test_continue_decision_token_and_cost_caps(tmp_path):
    from datetime import datetime
    now = datetime.fromisoformat("2026-11-03T00:10:00+00:00")
    task, record = _record(tmp_path)
    assert continue_decision(record, task, "stopped", "max_run_seconds", now=now)[0] is True
    # 未达到上限仍继续
    assert continue_decision(record, task, "stopped", "max_run_seconds",
                             max_tokens=100, used_tokens=99, now=now)[0] is True
    # token 上限
    assert continue_decision(record, task, "stopped", "max_run_seconds",
                             max_tokens=100, used_tokens=100, now=now)[1] == "goal_max_tokens"
    # cost 上限
    assert continue_decision(record, task, "stopped", "max_run_seconds",
                             max_cost_usd=1.5, used_cost_usd=1.5, now=now)[1] == "goal_max_cost_usd"
    assert continue_decision(record, task, "stopped", "max_run_seconds",
                             max_cost_usd=1.5, used_cost_usd=1.49, now=now)[0] is True
    # 0 表示不设上限
    assert continue_decision(record, task, "stopped", "max_run_seconds",
                             max_tokens=0, used_tokens=10**6,
                             max_cost_usd=0, used_cost_usd=999.0, now=now)[0] is True
    # 兼容旧行为：segments.usage.total_tokens 累加
    record.segments = [{"segment": 1, "usage": {"total_tokens": 40}},
                       {"segment": 2, "usage": {"total_tokens": 60}}]
    assert continue_decision(record, task, "stopped", "max_run_seconds",
                             max_tokens=100, now=now)[1] == "goal_max_tokens"
