"""TaskRuntime P0：TaskRecord/TaskStore 的确定性回归（测试先行）。"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone

import pytest

from app.runtime.tasks import (
    InvalidTaskTransition,
    TaskBusy,
    TaskCorrupt,
    TaskIOError,
    TaskNotFound,
    TaskStore,
)

TASK_ID_RE = re.compile(r"^t-\d{8}-\d{6}-[0-9a-f]{6}$")


@pytest.fixture()
def store(tmp_path):
    return TaskStore(tmp_path / "session")


def _create(store, tmp_path, **overrides):
    values = {
        "kind": "goal",
        "objective": "完成一个长任务",
        "workspace": str(tmp_path / "workspace"),
        "mode": "goal",
    }
    values.update(overrides)
    return store.create(**values)


def _events(store, task_id):
    path = store.task_dir(task_id) / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_lock(store, task_id, *, pid, host=None):
    payload = {
        "task_id": task_id,
        "pid": pid,
        "host": host or __import__("socket").gethostname(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    store.task_dir(task_id).joinpath("lock").write_text(json.dumps(payload), encoding="utf-8")


def test_create_get_list_roundtrip(store, tmp_path):
    record = _create(store, tmp_path, budget={"max_run_tokens": 1000}, approval_policy={"autonomy": "edits"})
    assert TASK_ID_RE.match(record.task_id)
    assert record.session_id == "session"
    assert record.status == "created"
    assert record.kind == "goal" and record.mode == "goal"
    assert record.workspace == str((tmp_path / "workspace").resolve())
    assert record.budget["max_run_tokens"] == 1000
    assert record.approval_policy["autonomy"] == "edits"
    assert (store.task_dir(record.task_id) / "meta.json").is_file()

    loaded = store.get(record.task_id)
    assert loaded.to_dict() == record.to_dict()
    assert [item.task_id for item in store.list()] == [record.task_id]
    assert store.list(status="created")[0].task_id == record.task_id
    assert store.list(status="completed") == []
    assert store.list(kind="goal")[0].task_id == record.task_id
    assert store.list(kind="run") == []

    first_event = _events(store, record.task_id)[0]
    assert first_event["event"] == "task_created"
    assert first_event["seq"] == 1


def test_create_validation(store, tmp_path):
    with pytest.raises(ValueError):
        _create(store, tmp_path, kind="unknown")
    with pytest.raises(ValueError):
        _create(store, tmp_path, mode="unknown")
    with pytest.raises(ValueError):
        _create(store, tmp_path, objective="   ")
    with pytest.raises(ValueError):
        _create(store, tmp_path, objective="x" * 4001)
    with pytest.raises(ValueError):
        _create(store, tmp_path, workspace="relative/path")
    with pytest.raises(ValueError):
        _create(store, tmp_path, budget={"max_run_tokens": -1})
    with pytest.raises(ValueError):
        _create(store, tmp_path, approval_policy={"autonomy": "sometimes"})


def test_meta_write_is_atomic(store, tmp_path, monkeypatch):
    record = _create(store, tmp_path)
    target = store.task_dir(record.task_id) / "meta.json"
    before = json.loads(target.read_text(encoding="utf-8"))

    def broken_replace(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(TaskIOError):
        store.transition(record.task_id, "running")

    after = json.loads(target.read_text(encoding="utf-8"))
    assert after["status"] == "created" == before["status"]
    assert not (store.task_dir(record.task_id) / "meta.json.tmp").exists()


def test_corrupt_meta_and_invalid_dirs_are_skipped(store, tmp_path):
    good = _create(store, tmp_path)
    bad = _create(store, tmp_path)
    (store.task_dir(bad.task_id) / "meta.json").write_text("{broken", encoding="utf-8")
    (store.tasks_root / "not-a-task").mkdir()

    records = store.list()
    assert [item.task_id for item in records] == [good.task_id]
    assert store.last_list_skipped >= 2
    with pytest.raises(TaskCorrupt):
        store.get(bad.task_id)
    with pytest.raises(TaskNotFound):
        store.get("t-20260101-000000-abcdef")


def test_corrupt_event_lines_do_not_block(store, tmp_path):
    record = _create(store, tmp_path)
    with (store.task_dir(record.task_id) / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"broken":\n')
        handle.write("\n")
    store.transition(record.task_id, "running")
    events = store.read_events(record.task_id)
    assert [item["seq"] for item in events] == [1, 2]
    assert events[-1]["event"] == "task_started"


def test_transitions_and_terminal_idempotency(store, tmp_path):
    record = _create(store, tmp_path)
    store.transition(record.task_id, "running")
    paused = store.transition(record.task_id, "paused", reason="human")
    assert paused.status == "paused"
    store.transition(record.task_id, "running")
    completed = store.transition(record.task_id, "completed")
    assert completed.status == "completed"
    before = len(_events(store, record.task_id))
    again = store.transition(record.task_id, "completed")
    assert again.status == "completed"
    assert len(_events(store, record.task_id)) == before


def test_illegal_transitions_rejected_without_write(store, tmp_path):
    record = _create(store, tmp_path)
    before_meta = (store.task_dir(record.task_id) / "meta.json").read_text(encoding="utf-8")
    before_events = len(_events(store, record.task_id))
    with pytest.raises(InvalidTaskTransition):
        store.transition(record.task_id, "paused")
    assert (store.task_dir(record.task_id) / "meta.json").read_text(encoding="utf-8") == before_meta
    assert len(_events(store, record.task_id)) == before_events

    store.transition(record.task_id, "running")
    store.transition(record.task_id, "completed")
    with pytest.raises(InvalidTaskTransition):
        store.transition(record.task_id, "running")


def test_lock_double_acquire_and_release(store, tmp_path):
    record = _create(store, tmp_path)
    store.transition(record.task_id, "running")
    store.acquire_lock(record.task_id)
    lock = store.task_dir(record.task_id) / "lock"
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()
    with pytest.raises(TaskBusy):
        store.acquire_lock(record.task_id)
    store.release_lock(record.task_id)
    assert not lock.exists()
    store.acquire_lock(record.task_id)
    store.release_lock(record.task_id)


def test_recover_stale_dead_pid_or_missing_lock(store, tmp_path):
    dead = _create(store, tmp_path)
    store.transition(dead.task_id, "running")
    _write_lock(store, dead.task_id, pid=999_999_999)

    missing = _create(store, tmp_path)
    store.transition(missing.task_id, "running")

    recovered = store.recover_stale()
    by_id = {item.task_id: item for item in recovered}
    assert by_id[dead.task_id].status == "interrupted"
    assert by_id[missing.task_id].status == "interrupted"
    assert not (store.task_dir(dead.task_id) / "lock").exists()
    events = [item["event"] for item in _events(store, dead.task_id)]
    assert "lock_stale_recovered" in events
    assert "task_interrupted" in events


def test_recover_stale_skips_live_and_foreign_host(store, tmp_path):
    live = _create(store, tmp_path)
    store.transition(live.task_id, "running")
    _write_lock(store, live.task_id, pid=os.getpid())

    foreign = _create(store, tmp_path)
    store.transition(foreign.task_id, "running")
    _write_lock(store, foreign.task_id, pid=999_999_999, host="another-host")

    assert store.recover_stale() == []
    assert store.get(live.task_id).status == "running"
    assert store.get(foreign.task_id).status == "running"


def test_segments_lifecycle(store, tmp_path):
    record = _create(store, tmp_path)
    segment = store.start_segment(record.task_id, run_id="run-1")
    assert segment.segment == 1 and segment.status == "running"
    assert store.get(record.task_id).status == "running"
    with pytest.raises(TaskBusy):
        store.start_segment(record.task_id, run_id="run-2")

    ended = store.end_segment(
        record.task_id, run_id="run-1", status="stopped",
        stop_reason="max_run_seconds", round_ref="rounds/round_001.json",
    )
    assert ended.status == "stopped" and ended.round_ref == "rounds/round_001.json"
    second = store.start_segment(record.task_id, run_id="run-2")
    assert second.segment == 2


def test_segment_mismatch_and_invalid_status(store, tmp_path):
    record = _create(store, tmp_path)
    store.start_segment(record.task_id, run_id="run-1")
    with pytest.raises(InvalidTaskTransition):
        store.end_segment(record.task_id, run_id="run-x", status="stopped")
    with pytest.raises(ValueError):
        store.end_segment(record.task_id, run_id="run-1", status="running")


def test_task_id_path_safety(store, tmp_path):
    record = _create(store, tmp_path)
    for bad in ("../escape", "t-2026", "../../etc", "not-a-task"):
        with pytest.raises(ValueError):
            store.get(bad)
    # 合法目录但 meta 是符号链接：必须拒绝，不跟到外部
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (store.task_dir(record.task_id) / "meta.json").unlink()
    (store.task_dir(record.task_id) / "meta.json").symlink_to(outside)
    with pytest.raises(TaskCorrupt):
        store.get(record.task_id)


def test_concurrent_creates_are_isolated(store, tmp_path):
    created = []
    lock = threading.Lock()

    def worker(index):
        record = _create(store, tmp_path, objective=f"任务 {index}")
        with lock:
            created.append(record.task_id)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(created)) == 8
    assert len(store.list()) == 8


def test_unknown_fields_are_preserved(store, tmp_path):
    record = _create(store, tmp_path)
    meta_path = store.task_dir(record.task_id) / "meta.json"
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    payload["future_field"] = {"kept": True}
    meta_path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = store.get(record.task_id)
    assert loaded.to_dict()["future_field"] == {"kept": True}

def test_writes_reject_unknown_task(store):
    for task_id in ("t-20260101-000000-abcdef",):
        with pytest.raises(TaskNotFound):
            store.append_event(task_id, "task_paused")
        with pytest.raises(TaskNotFound):
            store.acquire_lock(task_id)



def test_queue_transitions(store, tmp_path):
    record = _create(store, tmp_path)
    queued = store.transition(record.task_id, "queued", reason="workspace_wait")
    assert queued.status == "queued"
    running = store.transition(record.task_id, "running", reason="run_start")
    assert running.status == "running"
