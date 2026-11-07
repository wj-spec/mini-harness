"""`--version` 构建信息输出测试。"""

from __future__ import annotations

import json
import sys

import pytest

from app import __main__ as entrypoint


def test_version_outputs_json(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vibe", "--version"])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] and payload["python"]
    assert set(payload) == {"version", "python", "agent_version", "dirty", "platform"}
    assert isinstance(payload["dirty"], bool)


def test_version_rejects_other_modes(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vibe", "--version", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--version cannot be combined" in capsys.readouterr().err


def test_build_info_dirty_ignores_untracked(tmp_path):
    import shutil
    import subprocess

    from app.__main__ import _build_info

    if not shutil.which("git"):
        pytest.skip("git unavailable")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / "untracked.txt").write_text("y\n", encoding="utf-8")
    assert _build_info(repo)["dirty"] is False
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    assert _build_info(repo)["dirty"] is True
