"""TaskRuntime 接入单元测试（spec: docs/specs/task-runtime-integration.md）。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.runtime.task_runtime import (
    RunTask,
    TaskRuntime,
    TaskRuntimeDisabled,
    TaskRuntimeError,
    task_runtime_enabled,
)
from app.runtime.tasks import TaskBusy, TaskError, TaskStore


def _result(status: str, *, verified=None, stop_reason=""):
    return SimpleNamespace(status=status, verified=verified, stop_reason=stop_reason)


def _meta(**overrides):
    values = {"kind": "goal", "objective": "完成一个长任务"}
    values.update(overrides)
    return values


@pytest.fixture()
def runtime(tmp_path):
    return TaskRuntime(TaskStore(tmp_path / "session"), workspace=tmp_path, enabled=True)


def test_create_run_and_complete(runtime, tmp_path):
    run = runtime.prepare_run("run-1", _meta())
    assert run is not None and run.segment == 1 and run.run_id == "run-1"
    assert runtime.store.get(run.task_id).status == "running"
    record = runtime.finish_run(run, _result("done", verified=True))
    assert record is not None and record.status == "completed"
    loaded = runtime.store.get(run.task_id)
    assert loaded.status == "completed"
    assert loaded.segments[0].status == "done"
    assert loaded.segments[0].run_id == "run-1"
    assert not (runtime.store.task_dir(run.task_id) / "lock").exists()


@pytest.mark.parametrize(
    ("status", "verified", "stop_reason", "task_status", "segment_status"),
    [
        ("done", True, "", "completed", "done"),
        ("done", False, "", "paused", "done"),
        ("stopped", None, "max_run_seconds", "paused", "stopped"),
        ("cancelled", None, "", "paused", "cancelled"),
        ("error", None, "", "paused", "failed"),
    ],
)
def test_finish_run_mapping(runtime, status, verified, stop_reason, task_status, segment_status):
    run = runtime.prepare_run(f"run-{status}", _meta())
    record = runtime.finish_run(run, _result(status, verified=verified, stop_reason=stop_reason))
    assert record.status == task_status
    assert record.segments[-1].status == segment_status
    assert not (runtime.store.task_dir(run.task_id) / "lock").exists()


def test_attach_existing_goal_task(runtime):
    first = runtime.prepare_run("run-1", _meta())
    runtime.finish_run(first, _result("stopped", stop_reason="max_run_seconds"))

    second = runtime.prepare_run("run-2", _meta(task_id=first.task_id))
    assert second is not None and second.task_id == first.task_id
    assert second.segment == 2
    record = runtime.finish_run(second, _result("done", verified=True))
    assert record.status == "completed"
    segments = runtime.store.get(first.task_id).segments
    assert [segment.run_id for segment in segments] == ["run-1", "run-2"]


def test_attach_rejects_workspace_and_session_mismatch(runtime, tmp_path):
    other_workspace = tmp_path / "other"
    other_workspace.mkdir()
    other = TaskRuntime(TaskStore(tmp_path / "other-session"), workspace=other_workspace)
    other_run = other.prepare_run("run-x", _meta())
    other.finish_run(other_run, _result("stopped", stop_reason="max_run_seconds"))

    with pytest.raises((TaskRuntimeError, TaskError)):
        runtime.prepare_run("run-y", _meta(task_id=other_run.task_id))
    # 清理 other 的锁（finish 已释放），避免 tmp 残留影响测试语义
    assert not (other.store.task_dir(other_run.task_id) / "lock").exists()


def test_attach_terminal_task_rejected(runtime):
    run = runtime.prepare_run("run-1", _meta())
    runtime.finish_run(run, _result("done", verified=True))
    with pytest.raises(TaskRuntimeError):
        runtime.prepare_run("run-2", _meta(task_id=run.task_id))


def test_active_task_is_busy(runtime):
    first = runtime.prepare_run("run-1", _meta())
    with pytest.raises((TaskBusy, TaskRuntimeError)):
        runtime.prepare_run("run-2", _meta(task_id=first.task_id))
    runtime.finish_run(first, _result("cancelled"))


def test_invalid_meta(runtime):
    for meta in (
        {"kind": "run", "objective": "x"},
        {"kind": "goal"},
        {"kind": "goal", "objective": ""},
        {"kind": "goal", "objective": "x", "unknown": True},
        {"kind": "goal", "objective": "x", "task_id": "bad"},
    ):
        with pytest.raises((TaskRuntimeError, ValueError)):
            runtime.prepare_run("run-1", meta)


def test_disabled_runtime(tmp_path):
    runtime = TaskRuntime(TaskStore(tmp_path / "session"), workspace=tmp_path, enabled=False)
    with pytest.raises(TaskRuntimeDisabled):
        runtime.prepare_run("run-1", _meta())
    assert runtime.finish_run(None, _result("done")) is None
    assert runtime.recover() == []


def test_task_runtime_enabled_env(monkeypatch):
    monkeypatch.delenv("VIBE_TASK_RUNTIME", raising=False)
    assert task_runtime_enabled("user") is True
    assert task_runtime_enabled("test") is False
    for value in ("0", "false", "off", "no"):
        monkeypatch.setenv("VIBE_TASK_RUNTIME", value)
        assert task_runtime_enabled("user") is False
        assert task_runtime_enabled("test") is False
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    assert task_runtime_enabled("user") is True
    assert task_runtime_enabled("test") is True


def test_payload_shape(runtime):
    run = runtime.prepare_run("run-1", _meta(goal_id="g-1"))
    payload = runtime.payload(run.record)
    assert payload["task_id"] == run.task_id
    assert payload["status"] == "running"
    assert payload["segment"] == 1
    assert set(payload) == {
        "task_id", "kind", "status", "segment", "objective", "goal_id", "updated_at",
    }
    runtime.finish_run(run, _result("done", verified=True))
