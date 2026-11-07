from __future__ import annotations

import json

import pytest

from app import __main__ as entrypoint
from app.config import Config
from app.runtime.acceptance import save_acceptance_result
from app.runtime.tasks import TaskStore
from app.session.store import SessionStore


def _seed(tmp_path):
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task = TaskStore(session.root).create(kind="run", objective="验收任务", workspace=str(tmp_path), mode="act")
    task_dir = TaskStore(session.root).task_dir(task.task_id)
    from app.runtime.plan import Plan, plan_path, propose_plan
    for version, status in ((1, True), (2, False)):
        plan = Plan.from_dict({"schema_version": 1, "plan_id": f"pl-{version}",
                               "task_id": task.task_id, "version": version,
                               "objective": f"目标{version}",
                               "steps": [{"step_id": "s1", "title": "读"}]})
        propose_plan(task_dir, plan)
    save_acceptance_result(task_dir, 1, {"status": True, "codes": [],
                                         "results": {"v1": {"ok": True}}})
    save_acceptance_result(task_dir, 2, {"status": False, "codes": ["acceptance_failed"],
                                         "results": {"v1": {"ok": False}}})
    return task


def test_acceptance_lists_results(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--acceptance", "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert [item["plan_version"] for item in payload["acceptance"]] == [1, 2]
    assert payload["acceptance"][0]["plan_id"] == "pl-1"
    assert payload["acceptance"][1]["status"] is False


def test_acceptance_empty_read_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--acceptance"])
    entrypoint.main()
    assert json.loads(capsys.readouterr().out)["acceptance"] == []
    assert not (tmp_path / ".vibe").exists()


def test_acceptance_rejects_other_modes(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["vibe", "--acceptance", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--acceptance cannot be combined" in capsys.readouterr().err


def test_acceptance_reports_corrupt_files(tmp_path, monkeypatch, capsys):
    task = _seed(tmp_path)
    session_root = next((tmp_path / ".vibe" / "sessions").iterdir())
    task_dir = TaskStore(session_root).task_dir(task.task_id)
    (task_dir / "acceptance.v99.json").write_text("{bad json", encoding="utf-8")
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--acceptance", "--workspace", str(tmp_path)])
    entrypoint.main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["skipped_acceptance_files"] == 1
    assert [item["plan_version"] for item in payload["acceptance"]] == [1, 2]
    assert "skipped 1 corrupt acceptance files" in captured.err
