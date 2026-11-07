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


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run([GIT, "init", "-q"], cwd=root, check=True)
    subprocess.run([GIT, "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run([GIT, "config", "user.name", "Test"], cwd=root, check=True)
    (root / ".gitignore").write_text(".vibe/\n", encoding="utf-8")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run([GIT, "add", "."], cwd=root, check=True)
    subprocess.run([GIT, "commit", "-qm", "initial"], cwd=root, check=True)
    return root


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
    assert "require a concrete --session" in capsys.readouterr().err


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
