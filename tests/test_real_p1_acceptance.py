from __future__ import annotations

import json
import subprocess
import sys

from app.config import Config
from scripts import real_p1_acceptance


def test_real_p1_script_requires_key(monkeypatch, capsys):
    monkeypatch.setattr(real_p1_acceptance.Config, "from_env", lambda: Config(workspace=None))
    assert real_p1_acceptance.main([]) == 2
    assert "missing API key" in capsys.readouterr().out


def test_real_p1_script_help_exits_zero(tmp_path):
    proc = subprocess.run(
        [sys.executable, "scripts/real_p1_acceptance.py", "--help"],
        capture_output=True, text=True,
        cwd=__import__("pathlib").Path(__file__).resolve().parents[1],
    )
    assert proc.returncode == 0
    assert "真实模型 Plan P1 验收脚本" in proc.stdout


def test_real_p1_script_parses_background_and_verify(monkeypatch, capsys):
    monkeypatch.setattr(real_p1_acceptance.Config, "from_env", lambda: Config(workspace=None))
    assert real_p1_acceptance.main(["--background", "--verify-command", "true"]) == 2
    assert "missing API key" in capsys.readouterr().out


def test_real_p1_script_parses_repair_mode(monkeypatch, capsys):
    monkeypatch.setattr(real_p1_acceptance.Config, "from_env", lambda: Config(workspace=None))
    assert real_p1_acceptance.main(["--repair"]) == 2
    assert "missing API key" in capsys.readouterr().out
    assert callable(real_p1_acceptance._run_repair)
    assert callable(real_p1_acceptance._generate_plan)


def test_real_p1_script_parses_merge_mode(monkeypatch, capsys):
    monkeypatch.setattr(real_p1_acceptance.Config, "from_env", lambda: Config(workspace=None))
    assert real_p1_acceptance.main(["--merge"]) == 2
    assert "missing API key" in capsys.readouterr().out
    assert callable(real_p1_acceptance._run_merge)


def test_real_p1_script_parses_suite_mode(monkeypatch, capsys):
    monkeypatch.setattr(real_p1_acceptance.Config, "from_env", lambda: Config(workspace=None))
    assert real_p1_acceptance.main(["--suite"]) == 2
    assert "missing API key" in capsys.readouterr().out
    assert callable(real_p1_acceptance._run_suite)
    assert callable(real_p1_acceptance._run_queue)
    assert callable(real_p1_acceptance._run_goal_stage)


def test_generate_plan_retries_when_first_run_writes_no_plan(tmp_path, monkeypatch):
    import asyncio

    from app.runtime.plan import Plan, propose_plan
    from app.session.store import SessionStore

    script = real_p1_acceptance
    sent: list[int] = []

    class FakeHarness:
        def __init__(self, agent, config, session):
            self.session = session

        async def until(self, kind, request_id=None):
            if kind == "task_state":
                return {"task": {"task_id": f"t-fake-{len(sent)}"}}
            return {}

        def send(self, kind, request_id, text=None, task=None):
            sent.append(len(sent) + 1)
            if len(sent) == 2:
                task_id = f"t-fake-{len(sent)}"
                task_dir = self.session.root / "tasks" / task_id
                task_dir.mkdir(parents=True)
                plan = Plan.from_dict({
                    "schema_version": 1, "plan_id": "pl-fake", "task_id": task_id,
                    "objective": "假计划", "steps": [{"step_id": "s1", "title": "读"}],
                })
                propose_plan(task_dir, plan)

        async def close(self):
            return None

    monkeypatch.setattr(script, "Harness", FakeHarness)
    monkeypatch.setattr(script, "_agent", lambda *args, **kwargs: None)
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id, plan = asyncio.run(
        script._generate_plan(Config(workspace=tmp_path, provider="fake"), tmp_path, session, "true")
    )
    assert task_id == "t-fake-2"
    assert plan is not None and plan["plan_id"] == "pl-fake"
    assert len(sent) == 2
