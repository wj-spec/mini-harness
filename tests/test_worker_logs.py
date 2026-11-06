from __future__ import annotations

from pathlib import Path

from app.runtime.worker import _rotate_log


def test_worker_events_rotation(monkeypatch, tmp_path):
    path = tmp_path / "worker-events.jsonl"
    path.write_text("x" * 100, encoding="utf-8")
    monkeypatch.setenv("VIBE_WORKER_EVENTS_MAX_BYTES", "10")
    monkeypatch.setenv("VIBE_WORKER_EVENTS_BACKUPS", "2")
    _rotate_log(path, max_env="VIBE_WORKER_EVENTS_MAX_BYTES",
                backups_env="VIBE_WORKER_EVENTS_BACKUPS")
    assert not path.exists()
    assert (tmp_path / "worker-events.jsonl.1").exists()
