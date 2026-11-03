"""GoalRecord P0 测试（spec: docs/specs/goal-record.md）。"""

from __future__ import annotations

import json

import pytest

from app.runtime.goals import GOAL_SCHEMA_VERSION, GoalCorrupt, GoalError, GoalRecord, GoalStore
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
