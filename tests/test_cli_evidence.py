"""--evidence / --evidence-json 只读 CLI 测试。"""

from __future__ import annotations

import json

import pytest

from app import __main__ as entrypoint
from app.config import Config
from app.session.store import SessionStore


def _seed(tmp_path):
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    session.save_round({
        "schema_version": 1, "run_id": "r-1", "goal": "验收任务",
        "task_kind": "run", "status": "done", "stop_reason": "", "steps": 2,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "cost_usd": 0.0, "started_at": "2026-11-07T00:00:00+00:00",
        "verified": True,
        "completion": {"allowed": True, "codes": [], "messages": []},
        "acceptance": {
            "status": True, "codes": [], "plan_id": "pl-ev", "plan_version": 1,
            "commands": [{"id": "v1", "ok": True, "attempts": 1}],
        },
        "task_state": {},
    })
    return session


def test_evidence_cli_renders_latest_round(tmp_path, monkeypatch, capsys):
    session = _seed(tmp_path)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--evidence", "--session", session.id,
                                     "--workspace", str(tmp_path)])
    entrypoint.main()
    out = capsys.readouterr().out
    assert "最近一轮证据" in out
    assert "验收结果：已通过" in out
    assert "r-1" in out


def test_evidence_cli_json_output(tmp_path, monkeypatch, capsys):
    session = _seed(tmp_path)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--evidence", "--evidence-json",
                                     "--session", session.id, "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["session"] == session.id
    assert payload["round"]["acceptance"]["status"] is True
    assert payload["round"]["run_id"] == "r-1"


def test_evidence_cli_empty_session(tmp_path, monkeypatch, capsys):
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--evidence", "--session", session.id,
                                     "--workspace", str(tmp_path)])
    entrypoint.main()
    assert "当前会话还没有轮次记录" in capsys.readouterr().out


def test_evidence_cli_requires_session(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["vibe", "--evidence", "--workspace", "/tmp"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "requires a concrete --session" in capsys.readouterr().err


def test_evidence_cli_rejects_other_modes(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["vibe", "--evidence", "--session", "s-1",
                                     "--workspace", "/tmp", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--evidence cannot be combined" in capsys.readouterr().err


def test_evidence_reports_corrupt_rounds(tmp_path, monkeypatch, capsys):
    session = _seed(tmp_path)
    corrupt = session.rounds_dir / "round_9999.json"
    corrupt.write_text("{bad json", encoding="utf-8")
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--evidence", "--evidence-json",
                                     "--session", session.id, "--workspace", str(tmp_path)])
    entrypoint.main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["skipped_rounds"] == 1
    assert payload["round"]["run_id"] == "r-1"
    assert "skipped 1 corrupt round records" in captured.err
