"""Worker/attach P0b 测试（spec: docs/specs/process-supervision.md）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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
    from app.runtime.notifications import read_notifications
    items = read_notifications(tmp_path)
    kinds = [item["kind"] for item in items]
    assert "goal_segment" in kinds and "worker_exited" in kinds
    started = next(item for item in items if item["kind"] == "worker_started")
    assert started["task_kind"] == "goal" and started["mode"] == "goal"
    exited = next(item for item in items if item["kind"] == "worker_exited")
    assert exited["task_kind"] == "goal" and exited["mode"] == "goal"


    frames = [json.loads(json.loads(line)["raw"]) for line in (store.task_dir(task.task_id) / "worker-events.jsonl").read_text().splitlines()]
    results = [frame["result"] for frame in frames if frame.get("type") == "done"]
    assert len(results) >= 2
    assert results[0].get("continuing") is True
    assert not results[-1].get("continuing", False)

def test_worker_approval_timeout_fails_task_and_expires_inbox(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("VIBE_ALLOW_NON_GIT", "1")

    from app.runtime.worker import Worker

    monkeypatch.setenv("VIBE_WORKER_APPROVAL_TIMEOUT", "0.2")
    monkeypatch.setenv("VIBE_WORKER_APPROVAL_WARN_SECONDS", "0.05")
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
    from app.runtime.notifications import read_notifications
    kinds = [item["kind"] for item in read_notifications(tmp_path)]
    assert "approval_requested" in kinds and "approval_timeout" in kinds
    assert "approval_warning" in kinds


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
    from app.runtime.inspection import InspectionStore
    saved = InspectionStore(session.root, repo).page()['records']
    assert len(saved) == 1
    assert saved[0]['scope']['taskId'] == task.task_id
    assert saved[0]['fileSource']['root'] == str(worktree.resolve())
    assert saved[0]['fileSource']['identity'] == f'{worktree.stat().st_dev}:{worktree.stat().st_ino}'
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
    monkeypatch.setenv("VIBE_TASK_DEADLINE_WARN_SECONDS", "0.1")
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
    assert "task_deadline_warning" in events and "task_deadline_exceeded" in events
    from app.runtime.notifications import read_notifications
    kinds = [item["kind"] for item in read_notifications(tmp_path)]
    assert "task_deadline_warning" in kinds and "task_deadline_exceeded" in kinds
    state = json.loads((store.task_dir(task.task_id) / "worker.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"


def test_worker_workspace_wait_timeout_marks_failed(tmp_path, monkeypatch):
    import shutil, subprocess
    git = shutil.which("git")
    if git is None:
        pytest.skip("git required")
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run([git, "init", "-q"], cwd=repo, check=True)
    subprocess.run([git, "config", "user.email", "t@e.com"], cwd=repo, check=True)
    subprocess.run([git, "config", "user.name", "T"], cwd=repo, check=True)
    (repo / ".gitignore").write_text(".vibe/\n", encoding="utf-8")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run([git, "add", "."], cwd=repo, check=True)
    subprocess.run([git, "commit", "-qm", "init"], cwd=repo, check=True)
    session = SessionStore.new(repo, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="wait", workspace=str(repo), mode="goal")
    # 占住 workspace lock 的是本测试进程自己
    from app.runtime.workspace import acquire_workspace_lock
    lock = acquire_workspace_lock(repo, task_id="t-other", session_id="s-other")
    try:
        env = _env(tmp_path)
        env["VIBE_WORKSPACE_LOCK_WAIT"] = "0.2"
        env["VIBE_TASK_RUNTIME"] = "1"
        proc = subprocess.run(
            [sys.executable, "-m", "app", "--worker", "--fake", "--workspace", str(repo),
             "--session", session.id, "--task-id", task.task_id],
            capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], env=env, timeout=30,
        )
    finally:
        lock.release()
    assert proc.returncode == 3
    assert store.get(task.task_id).status == "failed"
    from app.runtime.notifications import read_notifications
    queued = [item for item in read_notifications(repo) if item["kind"] == "workspace_queued"]
    assert queued and queued[-1]["task_id"] == task.task_id


def test_worker_sends_run_task_meta_for_prepared_plan(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("VIBE_ALLOW_NON_GIT", "1")
    from app.runtime.worker import Worker

    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="run", objective="后台计划", workspace=str(tmp_path),
                        mode="act", plan_id="pl-bg", plan_version=2, session_id=session.id)
    captured = tmp_path / "task.json"
    script = tmp_path / "child.py"
    script.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        f"captured = Path({str(captured)!r})\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    if req.get('type') == 'run':\n"
        "        captured.write_text(json.dumps(req['task']))\n"
        "        result = {'text': 'x', 'steps': 1, 'status': 'done', 'stop_reason': '',\n"
        "                  'usage': {}, 'cost_usd': 0, 'run_id': req['id']}\n"
        "        print(json.dumps({'v': 1, 'type': 'done', 'id': req['id'], 'result': result}), flush=True)\n"
        "    elif req.get('type') == 'shutdown':\n"
        "        break\n",
        encoding="utf-8",
    )
    worker = Worker(session_root=session.root, task_id=task.task_id, workspace=tmp_path,
                    child_args=[sys.executable, str(script)], child_env={"VIBE_TASK_RUNTIME": "1"})
    assert asyncio.run(worker.run()) == 0
    meta = json.loads(captured.read_text(encoding="utf-8"))
    assert meta["kind"] == "run" and meta["mode"] == "act"
    assert meta["plan_id"] == "pl-bg" and meta["plan_version"] == 2


def test_worker_approval_inbox_full_notifies(tmp_path, monkeypatch):
    import asyncio
    from app.runtime.approvals import ApprovalInbox
    from app.runtime.tasks import TaskStore
    from app.runtime.worker import Worker
    from app.session.store import SessionStore

    monkeypatch.setenv("VIBE_APPROVAL_MAX_PENDING", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    task = TaskStore(session.root).create(kind="goal", objective="cap", workspace=str(tmp_path), mode="goal")
    ApprovalInbox(session.root).submit(task.task_id, tool="write_file")
    worker = Worker(session_root=session.root, task_id=task.task_id, workspace=tmp_path,
                    child_args=[], child_env={})
    asyncio.run(worker._record_approval({
        "approval_id": "a-cap", "call": {"name": "bash"}, "tier": "exec",
        "decisions": ["once", "deny"],
    }))
    from app.runtime.notifications import read_notifications
    notices = [item for item in read_notifications(tmp_path) if item["kind"] == "approval_inbox_full"]
    assert notices and "inbox full" in notices[-1]["reason"]


def test_read_only_worker_skips_workspace_lock(tmp_path, monkeypatch):
    import asyncio, sys
    monkeypatch.setenv("VIBE_ALLOW_NON_GIT", "1")
    from app.runtime.tasks import TaskStore
    from app.runtime.worker import Worker
    from app.runtime.workspace import acquire_workspace_lock
    from app.session.store import SessionStore

    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="run", objective="plan lock", workspace=str(tmp_path), mode="plan")
    lock = acquire_workspace_lock(tmp_path, task_id="t-other", session_id="s-other")
    try:
        worker = Worker(session_root=session.root, task_id=task.task_id, workspace=tmp_path,
                        child_args=[sys.executable, "-c", "import sys; [None for _ in sys.stdin]"],
                        child_env={"VIBE_TASK_RUNTIME": "1"})
        monkeypatch.setenv("VIBE_TASK_DEADLINE_SECONDS", "0.3")
        assert asyncio.run(worker.run()) in {0, 1}
        assert worker.workspace_lock is None
        events = (store.task_dir(task.task_id) / "events.jsonl").read_text(encoding="utf-8")
        assert "workspace_lock_skipped" in events
    finally:
        lock.release()


@pytest.mark.parametrize("env,reason,ledger_entry", [
    ({"VIBE_GOAL_MAX_TOKENS": "10"}, "goal_max_tokens",
     {"entry_kind": "llm", "tokens_in": 20, "tokens_out": 0, "cost_usd": 0.0}),
    ({"VIBE_GOAL_MAX_COST_USD": "0.5"}, "goal_max_cost_usd",
     {"entry_kind": "llm", "tokens_in": 0, "tokens_out": 0, "cost_usd": 1.0}),
])
def test_worker_goal_loop_stops_on_budget(tmp_path, monkeypatch, env, reason, ledger_entry):
    import asyncio

    monkeypatch.setenv("VIBE_ALLOW_NON_GIT", "1")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    from app.runtime.budget import BudgetLedger
    from app.runtime.worker import Worker

    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="预算目标", workspace=str(tmp_path), mode="goal")
    BudgetLedger(session.root).append({
        "schema_version": 1, "task_id": task.task_id, "goal_id": None,
        "parent_id": None, "node_kind": "goal", "segment": 1, "run_id": "r0",
        **ledger_entry, "estimated": False, "tool_calls": 0, "active_seconds": 0.0,
        "approval_wait_seconds": 0.0, "source": "test",
    })
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
        "        result = {'text': 'x', 'steps': 1, 'status': 'stopped',\n"
        "                  'stop_reason': 'max_run_seconds', 'usage': {},\n"
        "                  'cost_usd': 0, 'run_id': req['id']}\n"
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
    assert count_file.read_text(encoding="utf-8") == "1"
    from app.runtime.notifications import read_notifications
    stopped = [item for item in read_notifications(tmp_path) if item["kind"] == "goal_stopped"]
    assert stopped and stopped[-1]["reason"] == reason


def test_worker_warns_when_session_events_exceed_soft_limit(tmp_path, monkeypatch):
    from app.runtime.notifications import read_notifications
    from app.runtime.worker import Worker

    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="日志告警", workspace=str(tmp_path), mode="goal")
    (session.root / "events.jsonl").write_text("e" * 100, encoding="utf-8")
    worker = Worker(session_root=session.root, task_id=task.task_id, workspace=tmp_path,
                    child_args=[], child_env={})
    monkeypatch.setenv("VIBE_EVENTS_MAX_BYTES", "10")
    worker._check_session_events_size()
    items = [item for item in read_notifications(tmp_path) if item["kind"] == "session_events_large"]
    assert items and items[-1]["task_id"] == task.task_id
    assert items[-1]["bytes"] == 100 and items[-1]["soft_limit"] == 10
    events = store.read_events(task.task_id)
    assert any(event.get("event") == "session_events_large" for event in events)


def test_worker_does_not_warn_for_small_session_events(tmp_path, monkeypatch):
    from app.runtime.notifications import read_notifications
    from app.runtime.worker import Worker

    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="日志正常", workspace=str(tmp_path), mode="goal")
    (session.root / "events.jsonl").write_text("small", encoding="utf-8")
    worker = Worker(session_root=session.root, task_id=task.task_id, workspace=tmp_path,
                    child_args=[], child_env={})
    monkeypatch.setenv("VIBE_EVENTS_MAX_BYTES", "1000")
    worker._check_session_events_size()
    assert not [item for item in read_notifications(tmp_path) if item["kind"] == "session_events_large"]


def test_worker_logs_relaxed_unattended_profile(tmp_path, monkeypatch):
    import asyncio

    from app.runtime.notifications import read_notifications
    from app.runtime.worker import Worker

    monkeypatch.setenv("VIBE_ALLOW_NON_GIT", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="profile 审计", workspace=str(tmp_path), mode="goal")
    (tmp_path / ".vibe" / "unattended.json").write_text(
        '{"schema_version": 1, "allow_exec": true}', encoding="utf-8",
    )
    script = tmp_path / "child.py"
    script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    if req.get('type') == 'run':\n"
        "        result = {'text': 'ok', 'steps': 1, 'status': 'done', 'stop_reason': '',\n"
        "                  'usage': {}, 'cost_usd': 0, 'run_id': req['id']}\n"
        "        print(json.dumps({'v': 1, 'type': 'done', 'id': req['id'], 'result': result}), flush=True)\n"
        "    elif req.get('type') == 'shutdown':\n"
        "        break\n",
        encoding="utf-8",
    )
    worker = Worker(
        session_root=session.root, task_id=task.task_id, workspace=tmp_path,
        child_args=[sys.executable, str(script)], child_env={"VIBE_TASK_RUNTIME": "1"},
    )
    assert asyncio.run(worker.run()) == 0
    events = store.read_events(task.task_id)
    profile_event = next(item for item in events if item.get("event") == "unattended_profile")
    assert profile_event["data"]["relaxed"] == ["allow_exec"]
    assert len(profile_event["data"]["hash"]) == 64
    alerts = [item for item in read_notifications(tmp_path)
              if item["kind"] == "unattended_profile_relaxed"]
    assert alerts and alerts[-1]["relaxed"] == ["allow_exec"]
    assert alerts[-1]["task_id"] == task.task_id


def test_worker_refuses_relaxed_profile_without_approval(tmp_path, monkeypatch):
    import asyncio

    from app.runtime.worker import Worker, WorkerError

    monkeypatch.setenv("VIBE_ALLOW_NON_GIT", "1")
    monkeypatch.setenv("VIBE_UNATTENDED_REQUIRE_APPROVAL", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="profile gate", workspace=str(tmp_path), mode="goal")
    (tmp_path / ".vibe" / "unattended.json").write_text(
        '{"schema_version": 1, "allow_exec": true}', encoding="utf-8",
    )
    worker = Worker(session_root=session.root, task_id=task.task_id, workspace=tmp_path,
                    child_args=[], child_env={})
    with pytest.raises(WorkerError, match="requires approval"):
        asyncio.run(worker.run())


def test_worker_accepts_relaxed_profile_after_approval(tmp_path, monkeypatch):
    import asyncio

    from app.runtime.worker import Worker
    from app.safety.unattended import load_profile, write_profile_approval

    monkeypatch.setenv("VIBE_ALLOW_NON_GIT", "1")
    monkeypatch.setenv("VIBE_UNATTENDED_REQUIRE_APPROVAL", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="profile gate ok", workspace=str(tmp_path), mode="goal")
    (tmp_path / ".vibe" / "unattended.json").write_text(
        '{"schema_version": 1, "allow_exec": true}', encoding="utf-8",
    )
    write_profile_approval(tmp_path, load_profile(tmp_path))
    script = tmp_path / "child_ok.py"
    script.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    if req.get('type') == 'run':\n"
        "        result = {'text': 'ok', 'steps': 1, 'status': 'done', 'stop_reason': '',\n"
        "                  'usage': {}, 'cost_usd': 0, 'run_id': req['id']}\n"
        "        print(json.dumps({'v': 1, 'type': 'done', 'id': req['id'], 'result': result}), flush=True)\n"
        "    elif req.get('type') == 'shutdown':\n"
        "        break\n",
        encoding="utf-8",
    )
    worker = Worker(session_root=session.root, task_id=task.task_id, workspace=tmp_path,
                    child_args=[sys.executable, str(script)], child_env={"VIBE_TASK_RUNTIME": "1"})
    assert asyncio.run(worker.run()) == 0


def test_clock_offset_env_is_bounded(monkeypatch):
    from app.runtime.worker import _clock_offset_seconds

    monkeypatch.delenv("VIBE_FAKE_TIME_OFFSET_SECONDS", raising=False)
    assert _clock_offset_seconds() == 0.0
    monkeypatch.setenv("VIBE_FAKE_TIME_OFFSET_SECONDS", "120")
    assert _clock_offset_seconds() == 120.0
    monkeypatch.setenv("VIBE_FAKE_TIME_OFFSET_SECONDS", "nope")
    assert _clock_offset_seconds() == 0.0
    monkeypatch.setenv("VIBE_FAKE_TIME_OFFSET_SECONDS", str(10**9))
    assert _clock_offset_seconds() == 365 * 24 * 3600.0


def test_real_worker_respects_exhausted_foreground_goal_snapshot(tmp_path):
    from app.runtime.task_runtime import TaskRuntime
    from app.runtime.agent import AgentResult
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    runtime = TaskRuntime(store, workspace=tmp_path)
    runtime.goal_defaults = lambda: dict(max_segments=1,max_seconds=600,max_tokens=0,max_cost_usd=0)
    first = runtime.prepare_run("foreground", {"kind":"goal","objective":"bounded"})
    task = runtime.finish_run(first,AgentResult("pause",1,"stopped",stop_reason="max_steps"))
    proc = subprocess.run(_worker_cmd(session,task),capture_output=True,text=True,
        cwd=Path(__file__).resolve().parents[1],
        env={**_env(tmp_path),"VIBE_GOAL_MAX_SEGMENTS":"999"},timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert len(store.get(task.task_id).segments) == 1
    frames=[json.loads(json.loads(line)["raw"]) for line in
        (store.task_dir(task.task_id)/"worker-events.jsonl").read_text().splitlines()]
    assert any(f.get("code")=="task_budget_exceeded" and "goal_max_segments" in f["message"] for f in frames)
    assert not any(f.get("type")=="text_delta" for f in frames)
