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


def test_webhook_url_requires_valid_config(monkeypatch):
    from app.runtime.notifications import webhook_url_for

    monkeypatch.delenv("VIBE_NOTIFY_WEBHOOK", raising=False)
    assert webhook_url_for("worker_started") is None
    monkeypatch.setenv("VIBE_NOTIFY_WEBHOOK", "file:///tmp/hook")
    assert webhook_url_for("worker_started") is None
    monkeypatch.setenv("VIBE_NOTIFY_WEBHOOK", "http://127.0.0.1:1/hook")
    assert webhook_url_for("worker_started") == "http://127.0.0.1:1/hook"
    monkeypatch.setenv("VIBE_NOTIFY_WEBHOOK_KINDS", "approval_timeout,goal_stopped")
    assert webhook_url_for("worker_started") is None
    assert webhook_url_for("approval_timeout") == "http://127.0.0.1:1/hook"
    monkeypatch.setenv("VIBE_NOTIFY_WEBHOOK_KINDS", "*")
    assert webhook_url_for("anything") == "http://127.0.0.1:1/hook"


def test_notify_dispatches_webhook_for_matching_kind(tmp_path, monkeypatch):
    import threading

    from app.runtime import notifications as module

    calls: list[tuple[str, dict]] = []
    done = threading.Event()

    def fake_post(url, payload):
        calls.append((url, payload))
        done.set()
        return True

    monkeypatch.setattr(module, "post_webhook", fake_post)
    monkeypatch.setenv("VIBE_NOTIFY_WEBHOOK", "http://127.0.0.1:9/hook")
    monkeypatch.setenv("VIBE_NOTIFY_WEBHOOK_KINDS", "approval_timeout")
    assert notify(tmp_path, "worker_started", task_id="t") is True
    assert calls == []  # kind 不匹配：不调度 webhook
    assert notify(tmp_path, "approval_timeout", task_id="t", approval_id="a-1") is True
    assert done.wait(3), "webhook was not dispatched"
    url, payload = calls[0]
    assert url == "http://127.0.0.1:9/hook"
    assert payload["kind"] == "approval_timeout"
    assert payload["task_id"] == "t" and payload["approval_id"] == "a-1"


def test_notify_webhook_payload_truncates_oversized(tmp_path, monkeypatch):
    import threading

    from app.runtime import notifications as module

    calls: list[dict] = []
    done = threading.Event()

    def fake_post(url, payload):
        calls.append(payload)
        done.set()
        return True

    monkeypatch.setattr(module, "post_webhook", fake_post)
    monkeypatch.setenv("VIBE_NOTIFY_WEBHOOK", "http://127.0.0.1:9/hook")
    assert notify(tmp_path, "worker_exited", task_id="t", data="x" * (70 * 1024)) is True
    assert done.wait(3)
    assert calls[0]["truncated"] is True and calls[0]["kind"] == "worker_exited"


def test_notify_webhook_failure_does_not_break_local_log(tmp_path, monkeypatch):
    from app.runtime.notifications import notify_path

    monkeypatch.setenv("VIBE_NOTIFY_WEBHOOK", "http://127.0.0.1:1/hook")
    assert notify(tmp_path, "worker_started", task_id="t") is True
    assert notify_path(tmp_path).is_file()


def test_post_webhook_retries_and_requires_2xx(monkeypatch):
    import sys
    import types

    from app.runtime import notifications as module

    responses: list[object] = []

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

    def fake_post(url, json, timeout):
        return responses.pop(0)

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(post=fake_post))
    responses.extend([Response(500), Response(204)])
    assert module.post_webhook("http://example.invalid", {}) is True

    responses.extend([Exception("network down"), Response(200)])
    assert module.post_webhook("http://example.invalid", {}) is True

    responses.extend([Response(500), Response(500)])
    assert module.post_webhook("http://example.invalid", {}) is False
