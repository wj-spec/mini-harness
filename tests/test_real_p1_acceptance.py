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
