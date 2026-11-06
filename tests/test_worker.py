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
        "VIBE_ALLOW_NON_GIT": "1",
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


def test_worker_goal_loop_continues_resource_stops(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("VIBE_ALLOW_NON_GIT", "1")

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


def test_worker_approval_timeout_fails_task_and_expires_inbox(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("VIBE_ALLOW_NON_GIT", "1")

    from app.runtime.worker import Worker

    monkeypatch.setenv("VIBE_WORKER_APPROVAL_TIMEOUT", "0.2")
    (tmp_path / ".vibe").mkdir(exist_ok=True)
    (tmp_path / ".vibe" / "unattended.json").write_text('{"allow_exec": true}', encoding="utf-8")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="审批超时目标", workspace=str(tmp_path), mode="goal")
    store.transition(task.task_id, "running")
    store.transition(task.task_id, "paused")
    script = tmp_path / "approval_child.py"
    script.write_text(
        "import json, sys\n"
        "approval_sent = False\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    if req.get('type') == 'run' and not approval_sent:\n"
        "        approval_sent = True\n"
        "        print(json.dumps({'v': 1, 'type': 'approval_request', 'id': req['id'],\n"
        "              'approval_id': 'a1', 'call': {'id': 'c1', 'name': 'bash', 'args': {}},\n"
        "              'preview': 'rm -rf build', 'decisions': ['once', 'deny'], 'tier': 'exec'}), flush=True)\n"
        "    elif req.get('type') == 'cancel':\n"
        "        print(json.dumps({'v': 1, 'type': 'done', 'id': req['target'],\n"
        "              'result': {'text': 'cancelled', 'steps': 1, 'status': 'cancelled',\n"
        "                          'stop_reason': 'cancelled', 'usage': {}, 'cost_usd': 0, 'run_id': req['target']}}), flush=True)\n"
        "    elif req.get('type') == 'shutdown':\n"
        "        break\n",
        encoding="utf-8",
    )
    worker = Worker(
        session_root=session.root, task_id=task.task_id, workspace=tmp_path,
        child_args=[sys.executable, str(script)], child_env={"VIBE_TASK_RUNTIME": "1"},
    )
    assert asyncio.run(worker.run()) == 0
    assert store.get(task.task_id).status == "failed"
    items = worker.inbox.items(task.task_id)
    assert items and all(item["status"] == "expired" for item in items)


def test_worker_uses_git_worktree_when_available(tmp_path):
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:
        pytest.skip("git required")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([git, "init", "-q"], cwd=repo, check=True)
    subprocess.run([git, "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run([git, "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / ".gitignore").write_text(".vibe/\n", encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run([git, "add", "."], cwd=repo, check=True)
    subprocess.run([git, "commit", "-qm", "initial"], cwd=repo, check=True)

    session = SessionStore.new(repo, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="worktree 目标", workspace=str(repo), mode="goal")
    env = _env(tmp_path)
    env["VIBE_TASK_RUNTIME"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "app", "--worker", "--fake", "--workspace", str(repo),
         "--session", session.id, "--task-id", task.task_id],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    worktree = repo / ".vibe" / "worktrees" / task.task_id
    assert worktree.is_dir()
    assert (store.task_dir(task.task_id) / "worktree.json").is_file()
    assert not (repo / ".vibe" / "workspace.lock").exists()
    meta = json.loads((store.task_dir(task.task_id) / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "completed"


def test_worker_rejects_non_git_without_opt_in(tmp_path):
    session, store, task = _seed(tmp_path)
    env = _env(tmp_path)
    env.pop("VIBE_ALLOW_NON_GIT", None)
    proc = subprocess.run(
        _worker_cmd(session, task), capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1], env=env, timeout=30,
    )
    assert proc.returncode == 3
    assert "non-git workspace" in proc.stderr
    assert not (tmp_path / ".vibe" / "workspace.lock").exists()
    assert not (store.task_dir(task.task_id) / "worker.lock").exists()


def test_worker_reuses_existing_worktree(tmp_path):
    import shutil
    import subprocess

    git = shutil.which("git")
    if git is None:
        pytest.skip("git required")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([git, "init", "-q"], cwd=repo, check=True)
    subprocess.run([git, "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run([git, "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / ".gitignore").write_text(".vibe/\n", encoding="utf-8")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run([git, "add", "."], cwd=repo, check=True)
    subprocess.run([git, "commit", "-qm", "initial"], cwd=repo, check=True)

    session = SessionStore.new(repo, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="reuse worktree", workspace=str(repo), mode="goal")
    from app.runtime.workspace import WorktreeManager
    existing = WorktreeManager(repo).create(task.task_id, session.id)

    env = _env(tmp_path)
    env["VIBE_TASK_RUNTIME"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "app", "--worker", "--fake", "--workspace", str(repo),
         "--session", session.id, "--task-id", task.task_id],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], env=env, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "non-git workspace" not in proc.stderr
    assert existing.path.is_dir()
    assert json.loads((store.task_dir(task.task_id) / "meta.json").read_text(encoding="utf-8"))["status"] == "completed"


def test_worker_pauses_on_unconfirmed_goal_criteria(tmp_path):
    import asyncio

    from app.runtime.goals import GoalStore
    from app.runtime.worker import Worker

    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="等待验收标准确认", workspace=str(tmp_path), mode="goal")
    record = GoalStore(session.root).ensure_for_task(task)
    record.acceptance_criteria = [{"text": "必须通过隐藏测试", "confirmed": False}]
    GoalStore(session.root).save(record)

    worker = Worker(
        session_root=session.root, task_id=task.task_id, workspace=tmp_path,
        child_args=[sys.executable, "-c", "pass"],
        child_env={"VIBE_TASK_RUNTIME": "1"},
    )
    assert asyncio.run(worker.run()) == 0
    assert store.get(task.task_id).status == "created"
    state = json.loads((store.task_dir(task.task_id) / "worker.json").read_text(encoding="utf-8"))
    assert state["status"] == "exited"
    events = (store.task_dir(task.task_id) / "events.jsonl").read_text(encoding="utf-8")
    assert "goal_criteria_pending" in events
    assert not (store.task_dir(task.task_id) / "worker.lock").exists()


def test_worker_task_deadline_fails_task(tmp_path, monkeypatch):
    import asyncio

    from app.runtime.worker import Worker

    monkeypatch.setenv("VIBE_ALLOW_NON_GIT", "1")
    monkeypatch.setenv("VIBE_TASK_DEADLINE_SECONDS", "0.3")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="deadline", workspace=str(tmp_path), mode="goal")
    worker = Worker(
        session_root=session.root, task_id=task.task_id, workspace=tmp_path,
        child_args=[sys.executable, "-c", "import sys,time; [None for _ in sys.stdin]"],
        child_env={"VIBE_TASK_RUNTIME": "1"},
    )
    assert asyncio.run(worker.run()) in {0, 1}
    assert store.get(task.task_id).status in {"failed", "cancelled"}
    events = (store.task_dir(task.task_id) / "events.jsonl").read_text(encoding="utf-8")
    assert "task_deadline_exceeded" in events
    state = json.loads((store.task_dir(task.task_id) / "worker.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
