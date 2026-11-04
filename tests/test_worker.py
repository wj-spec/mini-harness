"""Worker/attach P0b 测试（spec: docs/specs/process-supervision.md）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.runtime.supervise import WorkerState, write_worker_state
from app.runtime.tasks import TaskStore
from app.session.store import SessionStore


def _seed(tmp_path):
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="worker 端到端目标", workspace=str(tmp_path), mode="goal")
    return session, store, task


def _worker_cmd(session, task):
    return [
        sys.executable, "-m", "app", "--worker", "--fake",
        "--workspace", str(Path(session.root).parents[2]),
        "--session", session.id, "--task-id", task.task_id,
    ]


def _env(tmp_path):
    return {
        **os.environ,
        "VIBE_TASK_RUNTIME": "1",
        "VIBE_WORKER_SOCKET_DIR": str(tmp_path / "sockets"),
        "VIBE_WORKER_HEARTBEAT_SECONDS": "0.2",
    }


def test_worker_runs_one_segment_end_to_end(tmp_path):
    session, store, task = _seed(tmp_path)
    proc = subprocess.run(
        _worker_cmd(session, task), capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1], env=_env(tmp_path), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    meta = json.loads((store.task_dir(task.task_id) / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "completed"
    assert len(meta["segments"]) == 1
    assert meta["segments"][0]["status"] == "done"
    assert meta["segments"][0]["run_id"].startswith("worker-")

    worker = json.loads((store.task_dir(task.task_id) / "worker.json").read_text(encoding="utf-8"))
    assert worker["status"] == "exited" and worker["exit_code"] == 0
    assert not (store.task_dir(task.task_id) / "worker.lock").exists()
    assert not (store.task_dir(task.task_id) / "worker.pid").exists()

    raw_lines = [
        json.loads(line)["raw"]
        for line in (store.task_dir(task.task_id) / "worker-events.jsonl").read_text(
            encoding="utf-8").splitlines()
    ]
    frames = [json.loads(raw) for raw in raw_lines]
    assert any(frame.get("type") == "ready" for frame in frames)
    assert any(frame.get("type") == "done" for frame in frames)
    assert any(frame.get("type") == "task_state" for frame in frames)


def test_second_worker_start_is_rejected(tmp_path):
    session, store, task = _seed(tmp_path)
    task_dir = store.task_dir(task.task_id)
    state = WorkerState(
        schema_version=1, task_id=task.task_id, pid=os.getpid(), host=__import__("socket").gethostname(),
        status="running", started_at="2026-11-03T00:00:00+00:00",
        heartbeat_at="2026-11-03T00:00:00+00:00",
    )
    write_worker_state(task_dir, state)
    (task_dir / "worker.lock").write_text(f"{os.getpid()}\n", encoding="utf-8")
    proc = subprocess.run(
        _worker_cmd(session, task), capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1], env=_env(tmp_path), timeout=30,
    )
    assert proc.returncode == 3
    assert "already running" in proc.stderr


def test_attach_without_worker_socket_fails_cleanly(tmp_path, capsys):
    from app.runtime.worker import attach_worker

    session, store, task = _seed(tmp_path)
    assert attach_worker(session.root, task.task_id) == 1
    assert "worker socket not found" in capsys.readouterr().err


def test_worker_goal_loop_continues_resource_stops(tmp_path):
    import asyncio

    from app.runtime.worker import Worker

    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="循环目标", workspace=str(tmp_path), mode="goal")
    count_file = tmp_path / "runs.txt"
    script = tmp_path / "child.py"
    script.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        f"count_file = Path({str(count_file)!r})\n"
        "count = 0\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    if req.get('type') == 'run':\n"
        "        count += 1\n"
        "        count_file.write_text(str(count))\n"
        "        stopped = count == 1\n"
        "        result = {'text': 'x', 'steps': 1, 'status': 'stopped' if stopped else 'done',\n"
        "                  'stop_reason': 'max_run_seconds' if stopped else '',\n"
        "                  'usage': {}, 'cost_usd': 0, 'run_id': req['id']}\n"
        "        print(json.dumps({'v': 1, 'type': 'done', 'id': req['id'], 'result': result}), flush=True)\n"
        "    elif req.get('type') == 'shutdown':\n"
        "        break\n",
        encoding="utf-8",
    )
    worker = Worker(
        session_root=session.root, task_id=task.task_id, workspace=tmp_path,
        child_args=[sys.executable, str(script)],
        child_env={"VIBE_TASK_RUNTIME": "1"},
    )
    assert asyncio.run(worker.run()) == 0
    assert count_file.read_text(encoding="utf-8") == "2"
    state = json.loads((store.task_dir(task.task_id) / "worker.json").read_text(encoding="utf-8"))
    assert state["status"] == "exited"
