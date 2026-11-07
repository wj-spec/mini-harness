from __future__ import annotations

import json

import pytest

from app import __main__ as entrypoint
from app.config import Config
from app.runtime.plan import Plan, propose_plan, revise_plan
from app.runtime.tasks import TaskStore
from app.session.store import SessionStore


def _seed(tmp_path):
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task = TaskStore(session.root).create(kind="run", objective="计划任务", workspace=str(tmp_path), mode="plan")
    task_dir = TaskStore(session.root).task_dir(task.task_id)
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-cli", "task_id": task.task_id,
                           "objective": "旧目标", "steps": [{"step_id": "s1", "title": "读"}]})
    propose_plan(task_dir, plan)
    revise_plan(task_dir, plan, objective="新目标")
    return session


def test_plans_lists_versions(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--plans", "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    versions = [item["version"] for item in payload["plans"]]
    assert versions == [1, 2]
    assert payload["plans"][-1]["objective"] == "新目标"
    assert payload["plans"][-1]["acceptance"] is None


def test_plans_empty_read_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--plans"])
    entrypoint.main()
    assert json.loads(capsys.readouterr().out)["plans"] == []
    assert not (tmp_path / ".vibe").exists()


def test_plans_rejects_other_modes(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["vibe", "--plans", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--plans cannot be combined" in capsys.readouterr().err


def test_plans_lists_step_progress(tmp_path, monkeypatch, capsys):
    from app.runtime.plan_execution import StepLedger, save_ledger

    session = _seed(tmp_path)
    task = TaskStore(session.root).list(kind="run")[0]
    task_dir = TaskStore(session.root).task_dir(task.task_id)
    from app.runtime.plan import load_plan, plan_path
    plan = load_plan(plan_path(task_dir, 1))
    ledger = StepLedger.from_plan(plan)
    ledger.start_step("s1")
    ledger.add_evidence("s1", kind="read", ref="README.md", ok=True)
    ledger.complete_step(plan, "s1")
    save_ledger(task_dir, ledger)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--plans", "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    row = next(item for item in payload["plans"] if item["version"] == 1)
    assert row["total_steps"] == 1
    assert row["done_steps"] == 1
    assert row["executed_steps"] == 1
    row2 = next(item for item in payload["plans"] if item["version"] == 2)
    assert row2["done_steps"] == 0 and row2["total_steps"] == 1
