from __future__ import annotations

import json

from app.session.retention import RetentionPolicy, cleanup_workspace_logs, rotate_file


def test_rotate_file_keeps_backups(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text("x" * 100, encoding="utf-8")
    assert rotate_file(path, max_bytes=10, backups=2)
    assert not path.exists()
    assert (tmp_path / "trace.jsonl.1").exists()
    assert not rotate_file(tmp_path / "missing.jsonl", max_bytes=10, backups=2)


def test_cleanup_rotates_only_oversized_trace(tmp_path):
    workspace = tmp_path / "ws"
    session = workspace / ".vibe" / "sessions" / "s-1"
    session.mkdir(parents=True)
    (session / "trace.jsonl").write_text("y" * 100, encoding="utf-8")
    (session / "events.jsonl").write_text("z" * 100, encoding="utf-8")
    result = cleanup_workspace_logs(workspace, policy=RetentionPolicy(max_bytes=10, backups=2))
    assert len(result["rotated"]) == 1
    assert (session / "trace.jsonl.1").exists()
    assert (session / "events.jsonl").exists()
    audit = (workspace / ".vibe" / "retention.jsonl").read_text(encoding="utf-8")
    assert "trace.jsonl" in audit
    payload = json.loads(audit.splitlines()[0])
    assert payload["candidates"] == 1


def test_cleanup_dry_run_does_not_rotate(tmp_path):
    workspace = tmp_path / "ws"
    session = workspace / ".vibe" / "sessions" / "s-1"
    session.mkdir(parents=True)
    (session / "trace.jsonl").write_text("y" * 100, encoding="utf-8")
    result = cleanup_workspace_logs(workspace, policy=RetentionPolicy(max_bytes=10), dry_run=True)
    assert result["rotated"][0]["rotated"] is False
    assert (session / "trace.jsonl").exists()
    assert not (workspace / ".vibe" / "retention.jsonl").exists()


def test_cleanup_logs_cli(monkeypatch, capsys, tmp_path):
    from app import __main__ as entrypoint

    session = tmp_path / ".vibe" / "sessions" / "s-1"
    session.mkdir(parents=True)
    (session / "trace.jsonl").write_text("y" * 100, encoding="utf-8")
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: entrypoint.Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", [
        "vibe", "--cleanup-logs", "--workspace", str(tmp_path), "--log-max-bytes", "10",
    ])
    entrypoint.main()
    out = json.loads(capsys.readouterr().out)
    assert out["rotated"] and out["rotated"][0]["rotated"] is True
    assert out["events_soft_limit"] > 0 and out["oversized_events"] == []
    assert (session / "trace.jsonl.1").exists()


def test_session_append_trace_auto_rotates(monkeypatch, tmp_path):
    from app.session.store import SessionStore

    monkeypatch.setenv("VIBE_TRACE_MAX_BYTES", "100")
    monkeypatch.setenv("VIBE_TRACE_BACKUPS", "2")
    session = SessionStore(tmp_path)
    for _ in range(20):
        session.append_trace({"type": "run_start", "run_id": "r", "pad": "x" * 20})
    assert (tmp_path / "trace.jsonl.1").exists()
    assert session.trace_path.exists()


def test_cleanup_reports_oversized_events_without_rotating(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    session = workspace / ".vibe" / "sessions" / "s-1"
    session.mkdir(parents=True)
    (session / "events.jsonl").write_text("e" * 100, encoding="utf-8")
    monkeypatch.setenv("VIBE_EVENTS_MAX_BYTES", "10")
    result = cleanup_workspace_logs(workspace, policy=RetentionPolicy(max_bytes=1000))
    assert result["events"] == 1
    assert len(result["oversized_events"]) == 1
    assert result["oversized_events"][0]["session"] == "s-1"
    assert result["oversized_events"][0]["bytes"] == 100
    assert (session / "events.jsonl").exists()
    assert not (session / "events.jsonl.1").exists()


def test_oversized_session_events_respects_soft_limit(tmp_path):
    from app.session.retention import oversized_session_events

    session = tmp_path / ".vibe" / "sessions" / "s-1"
    session.mkdir(parents=True)
    (session / "events.jsonl").write_text("e" * 50, encoding="utf-8")
    assert oversized_session_events(tmp_path, limit=10)[0]["session"] == "s-1"
    assert oversized_session_events(tmp_path, limit=100) == []
    assert oversized_session_events(tmp_path, limit=0) == []
