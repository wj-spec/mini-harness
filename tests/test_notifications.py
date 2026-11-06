from __future__ import annotations

from app.runtime.notifications import notify, read_notifications


def test_notify_appends_and_reads(tmp_path):
    assert notify(tmp_path, "worker_started", task_id="t")
    assert notify(tmp_path, "worker_exited", task_id="t", status="exited")
    items = read_notifications(tmp_path)
    assert [item["kind"] for item in items] == ["worker_started", "worker_exited"]
    assert items[1]["status"] == "exited"
    assert not notify(tmp_path, "")
