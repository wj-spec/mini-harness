from __future__ import annotations

import json

import pytest

from app import __main__ as entrypoint
from app.config import Config


def test_redteam_cli_passes_default(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--redteam", "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True


def test_redteam_cli_detects_allow_exec(tmp_path, monkeypatch, capsys):
    (tmp_path / ".vibe").mkdir()
    (tmp_path / ".vibe" / "unattended.json").write_text('{"schema_version":1,"allow_exec":true}', encoding="utf-8")
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--redteam", "--workspace", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False


def test_redteam_rejects_other_modes(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["vibe", "--redteam", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--redteam cannot be combined" in capsys.readouterr().err
