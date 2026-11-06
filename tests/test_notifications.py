from __future__ import annotations

from app.runtime.notifications import notify, read_notifications


def test_notify_appends_and_reads(tmp_path):
    assert notify(tmp_path, "worker_started", task_id="t")
    assert notify(tmp_path, "worker_exited", task_id="t", status="exited")
    items = read_notifications(tmp_path)
    assert [item["kind"] for item in items] == ["worker_started", "worker_exited"]
    assert items[1]["status"] == "exited"
    assert not notify(tmp_path, "")


def test_notifications_rotate_when_large(monkeypatch, tmp_path):
    import json
    from app.runtime.notifications import notify_path

    path = notify_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("x" * 100 + "\n", encoding="utf-8")
    monkeypatch.setenv("VIBE_NOTIFICATIONS_MAX_BYTES", "10")
    assert notify(tmp_path, "worker_started", task_id="t")
    assert (tmp_path / ".vibe" / "notifications.jsonl.1").exists()
    # 第一次写入时旧文件已超限，轮转到 .1
    assert path.read_text(encoding="utf-8").count("\n") == 1
