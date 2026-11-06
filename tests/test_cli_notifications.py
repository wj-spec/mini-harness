from __future__ import annotations

import json

import pytest

from app import __main__ as entrypoint
from app.config import Config
from app.runtime.notifications import notify


def test_notifications_lists_recent(tmp_path, monkeypatch, capsys):
    notify(tmp_path, "worker_started", task_id="t-1")
    notify(tmp_path, "worker_exited", task_id="t-1", status="exited")
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--notifications", "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    assert [item["kind"] for item in payload["notifications"]] == ["worker_started", "worker_exited"]


def test_notifications_empty_read_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--notifications"])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["notifications"] == []
    assert not (tmp_path / ".vibe").exists()


def test_notifications_rejects_other_modes(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["vibe", "--notifications", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--notifications cannot be combined" in capsys.readouterr().err
