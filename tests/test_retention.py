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
    assert (session / "trace.jsonl.1").exists()
