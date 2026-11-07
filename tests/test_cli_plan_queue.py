"""--prepare-plan-task / --run-plan-queue CLI 测试。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from app import __main__ as entrypoint
from app.config import Config
from app.runtime.plan import Plan, approve_plan, propose_plan
from app.runtime.plan_execution import StepLedger, save_ledger
from app.runtime.tasks import TaskStore
from app.session.store import SessionStore

GIT = shutil.which("git")


def _init_repo(root):
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run([GIT, "init", "-q"], cwd=root, check=True)
    subprocess.run([GIT, "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run([GIT, "config", "user.name", "Test"], cwd=root, check=True)
    (root / ".gitignore").write_text(".vibe/\n", encoding="utf-8")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run([GIT, "add", "."], cwd=root, check=True)
    subprocess.run([GIT, "commit", "-qm", "initial"], cwd=root, check=True)
    return root


@pytest.fixture()
def repo(tmp_path):
    return _init_repo(tmp_path / "repo")


def _approve_empty_plan(repo):
    session = SessionStore.new(repo, "fake", purpose="test")
    store = TaskStore(session.root)
    plan_task = store.create(kind="run", objective="计划", workspace=str(repo), mode="plan")
    task_dir = store.task_dir(plan_task.task_id)
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-queue", "task_id": plan_task.task_id,
        "version": 1, "objective": "排队执行计划",
        "steps": [{"step_id": "s1", "title": "读 README", "kind": "read"}],
    })
    propose_plan(task_dir, plan)
    approve_plan(task_dir, plan, approved_by="alice")
    # 预置已完成的 execution ledger：queue 测试只验证 worker 编排，不依赖 fake 模型执行 step。
    ledger = StepLedger.from_plan(plan)
    ledger.start_step("s1")
    ledger.add_evidence("s1", kind="read", ref="README.md", ok=True)
    ledger.complete_step(plan, "s1")
    save_ledger(task_dir, ledger)
    return session, plan


@pytest.mark.skipif(GIT is None, reason="git required")
def test_prepare_plan_task_creates_bound_task(repo, monkeypatch, capsys):
    session, plan = _approve_empty_plan(repo)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=repo))
    monkeypatch.setattr(sys, "argv", ["vibe", "--prepare-plan-task", "--session", session.id,
                                      "--workspace", str(repo), "--fake"])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan_id"] == "pl-queue" and payload["plan_version"] == 1
    task = TaskStore(session.root).get(payload["task_id"])
    assert task.status == "created"
    assert task.kind == "run" and task.mode == "act"
    assert task.plan_id == "pl-queue" and task.plan_version == 1


@pytest.mark.skipif(GIT is None, reason="git required")
def test_run_plan_queue_completes_prepared_task(repo, monkeypatch, capsys):
    session, plan = _approve_empty_plan(repo)
    store = TaskStore(session.root)
    task = store.create(kind="run", objective=plan.objective, workspace=str(repo),
                        mode="act", plan_id=plan.plan_id, plan_version=plan.version,
                        session_id=session.id)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=repo))
    monkeypatch.setattr(sys, "argv", ["vibe", "--run-plan-queue", "--session", session.id,
                                      "--workspace", str(repo), "--fake"])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["completed"] == 1 and payload["stopped_early"] is None
    assert payload["ran"][0]["task_id"] == task.task_id
    assert store.get(task.task_id).status == "completed"


def test_plan_queue_rejects_other_modes(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vibe", "--prepare-plan-task", "--session", "s-1", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--prepare-plan-task/--run-plan-queue cannot be combined" in capsys.readouterr().err


def test_plan_queue_requires_session(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vibe", "--run-plan-queue", "--workspace", "/tmp"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "requires --session or --queue-all-sessions" in capsys.readouterr().err


def test_plan_queue_all_sessions_requires_run(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vibe", "--queue-all-sessions", "--session", "s-1",
                                      "--workspace", "/tmp"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--queue-all-sessions requires --run-plan-queue" in capsys.readouterr().err


@pytest.mark.skipif(GIT is None, reason="git required")
def test_run_plan_queue_dry_run_reports_without_worker(repo, monkeypatch, capsys):
    session, plan = _approve_empty_plan(repo)
    store = TaskStore(session.root)
    task = store.create(kind="run", objective=plan.objective, workspace=str(repo),
                        mode="act", plan_id=plan.plan_id, plan_version=plan.version,
                        session_id=session.id)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=repo))
    monkeypatch.setattr(sys, "argv", ["vibe", "--run-plan-queue", "--queue-dry-run",
                                      "--session", session.id, "--workspace", str(repo), "--fake"])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True and payload["count"] == 1
    assert payload["queue"][0]["task_id"] == task.task_id
    assert payload["queue"][0]["plan_executable"] is True
    assert store.get(task.task_id).status == "created"  # 未启动 worker


@pytest.mark.skipif(GIT is None, reason="git required")
def test_run_plan_queue_skips_when_plan_missing(repo, monkeypatch, capsys):
    session, plan = _approve_empty_plan(repo)
    store = TaskStore(session.root)
    task = store.create(kind="run", objective=plan.objective, workspace=str(repo),
                        mode="act", plan_id=plan.plan_id, plan_version=plan.version,
                        session_id=session.id)
    (session.root / "tasks" / plan.task_id / "plan" / "plan.v1.json").unlink()
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=repo))
    monkeypatch.setattr(sys, "argv", ["vibe", "--run-plan-queue", "--session", session.id,
                                      "--workspace", str(repo), "--fake"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["stopped_early"] == "plan_unavailable"
    assert payload["ran"][0]["status"] == "skipped_plan_unavailable"
    assert store.get(task.task_id).status == "created"


def _prepared_task(repo):
    session, plan = _approve_empty_plan(repo)
    store = TaskStore(session.root)
    task = store.create(kind="run", objective=plan.objective, workspace=str(repo),
                        mode="act", plan_id=plan.plan_id, plan_version=plan.version,
                        session_id=session.id)
    return session, task


@pytest.mark.skipif(GIT is None, reason="git required")
def test_plan_queue_all_sessions_runs_across_sessions(repo, monkeypatch, capsys):
    first_session, first_task = _prepared_task(repo)
    second_session, second_task = _prepared_task(repo)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=repo))
    monkeypatch.setattr(sys, "argv", ["vibe", "--run-plan-queue", "--queue-all-sessions",
                                      "--workspace", str(repo), "--fake"])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["completed"] == 2 and payload["stopped_early"] is None
    assert payload["sessions"] == [first_session.id, second_session.id]
    assert {item["task_id"] for item in payload["ran"]} == {first_task.task_id, second_task.task_id}
    assert TaskStore(first_session.root).get(first_task.task_id).status == "completed"
    assert TaskStore(second_session.root).get(second_task.task_id).status == "completed"


@pytest.mark.skipif(GIT is None, reason="git required")
def test_plan_queue_all_sessions_dry_run_lists_sessions(repo, monkeypatch, capsys):
    first_session, first_task = _prepared_task(repo)
    second_session, second_task = _prepared_task(repo)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=repo))
    monkeypatch.setattr(sys, "argv", ["vibe", "--run-plan-queue", "--queue-all-sessions",
                                      "--queue-dry-run", "--workspace", str(repo), "--fake"])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True and payload["count"] == 2
    assert payload["sessions"] == [first_session.id, second_session.id]
    assert all(item["plan_executable"] for item in payload["queue"])
    assert TaskStore(first_session.root).get(first_task.task_id).status == "created"


@pytest.mark.skipif(GIT is None, reason="git required")
def test_plan_queue_across_workspaces(tmp_path, monkeypatch, capsys):
    first_repo = _init_repo(tmp_path / "first")
    second_repo = _init_repo(tmp_path / "second")
    first_session, first_task = _prepared_task(first_repo)
    second_session, second_task = _prepared_task(second_repo)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=first_repo))
    monkeypatch.setattr(sys, "argv", [
        "vibe", "--run-plan-queue", "--queue-all-sessions",
        "--queue-workspace", str(first_repo), "--queue-workspace", str(second_repo), "--fake",
    ])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["completed"] == 2 and payload["stopped_early"] is None
    assert payload["workspaces"] == [str(first_repo.resolve()), str(second_repo.resolve())]
    assert {item["task_id"] for item in payload["ran"]} == {first_task.task_id, second_task.task_id}
    assert TaskStore(first_session.root).get(first_task.task_id).status == "completed"
    assert TaskStore(second_session.root).get(second_task.task_id).status == "completed"


def test_queue_workspace_requires_run(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vibe", "--queue-workspace", "/tmp", "--workspace", "/tmp"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--queue-workspace requires --run-plan-queue" in capsys.readouterr().err


@pytest.mark.skipif(GIT is None, reason="git required")
def test_plan_queue_continue_on_failure_keeps_going(repo, monkeypatch, capsys):
    first_session, first_task = _prepared_task(repo)
    bad_plan = first_session.root / "tasks" / "t-20261106-000000-abcdef" / "plan" / "plan.v1.json"
    # 第一个 task 绑定的计划来自 _prepared_task 里的计划任务目录；直接删掉该会话计划文件。
    for candidate in first_session.root.glob("tasks/*/plan/plan.v1.json"):
        candidate.unlink()
    second_session, second_task = _prepared_task(repo)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=repo))
    monkeypatch.setattr(sys, "argv", ["vibe", "--run-plan-queue", "--queue-all-sessions",
                                      "--queue-continue-on-failure",
                                      "--workspace", str(repo), "--fake"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["continue_on_failure"] is True
    assert payload["completed"] == 1 and payload["failed"] == 1
    assert payload["stopped_early"] is None
    statuses = {item["task_id"]: item["status"] for item in payload["ran"]}
    assert statuses[first_task.task_id] == "skipped_plan_unavailable"
    assert statuses[second_task.task_id] == "completed"
    assert TaskStore(second_session.root).get(second_task.task_id).status == "completed"


def test_queue_continue_on_failure_requires_run(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vibe", "--queue-continue-on-failure", "--workspace", "/tmp"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--queue-continue-on-failure requires --run-plan-queue" in capsys.readouterr().err
