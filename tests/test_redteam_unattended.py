from __future__ import annotations

import json

from scripts import redteam_unattended
from app.safety.unattended import UnattendedProfile


def test_default_profile_matches_redteam_expectations(tmp_path, capsys):
    (tmp_path / ".vibe").mkdir()
    assert redteam_unattended.main(["--workspace", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    cases = {row["tool"] + str(row["worktree"]): row for row in payload["checks"]}
    assert cases["bashTrue"]["allowed"] is False
    assert cases["write_fileFalse"]["allowed"] is False
    assert cases["read_fileTrue"]["allowed"] is True


def test_allow_exec_profile_flips_bash(tmp_path, capsys):
    (tmp_path / ".vibe").mkdir()
    (tmp_path / ".vibe" / "unattended.json").write_text(
        json.dumps({"schema_version": 1, "allow_exec": True}), encoding="utf-8")
    assert redteam_unattended.main(["--workspace", str(tmp_path)]) == 1
    payload = json.loads(capsys.readouterr().out)
    bash = next(row for row in payload["checks"] if row["tool"] == "bash")
    assert bash["allowed"] is True and bash["ok"] is False


def test_unknown_profile_fails_closed(tmp_path, capsys):
    (tmp_path / ".vibe").mkdir()
    (tmp_path / ".vibe" / "unattended.json").write_text(
        json.dumps({"schema_version": 1, "allow_everything": True}), encoding="utf-8")
    assert redteam_unattended.main(["--workspace", str(tmp_path)]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
