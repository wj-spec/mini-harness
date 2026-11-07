"""--approvals 只读 CLI 测试。"""

from __future__ import annotations

import json

import pytest

from app import __main__ as entrypoint
from app.config import Config
from app.runtime.approvals import ApprovalInbox
from app.runtime.tasks import TaskStore
from app.session.store import SessionStore


def _seed(tmp_path):
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    store = TaskStore(session.root)
    task = store.create(kind="run", objective="审批任务", workspace=str(tmp_path), mode="act")
    other = store.create(kind="run", objective="另一个任务", workspace=str(tmp_path), mode="act")
    inbox = ApprovalInbox(session.root)
    write = inbox.submit(task.task_id, tool="write_file", tier="workspace_write",
                         decisions=["once", "deny"], preview="x")
    inbox.submit(other.task_id, tool="bash", tier="exec", decisions=["once", "deny"])
    resolved = inbox.submit(task.task_id, tool="read_file", decisions=["once", "deny"])
    inbox.resolve(task.task_id, resolved["approval_id"], decision="once")
    return session, task, write


def test_approvals_lists_pending(tmp_path, monkeypatch, capsys):
    session, task, write = _seed(tmp_path)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--approvals", "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    tools = [item["tool"] for item in payload["approvals"]]
    assert tools == ["write_file", "bash"]
    row = payload["approvals"][0]
    assert row["session"] == session.id
    assert row["task_id"] == task.task_id
    assert row["approval_id"] == write["approval_id"]
    assert row["tier"] == "workspace_write"
    assert not any(item["tool"] == "read_file" for item in payload["approvals"])


def test_approvals_empty_read_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--approvals"])
    entrypoint.main()
    assert json.loads(capsys.readouterr().out) == {"approvals": [], "count": 0, "skipped_approval_records": 0}
    assert not (tmp_path / ".vibe").exists()


@pytest.mark.parametrize("extra", ["--stdio", "--notifications", "--plans"])
def test_approvals_rejects_other_modes(monkeypatch, capsys, extra):
    monkeypatch.setattr("sys.argv", ["vibe", "--approvals", extra])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--approvals cannot be combined" in capsys.readouterr().err


def test_decide_unknown_approval_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--decide-approval", "a-missing", "--workspace", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "pending approval not found" in capsys.readouterr().err


def test_decide_without_running_worker_exits_1(tmp_path, monkeypatch, capsys):
    session, task, write = _seed(tmp_path)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--decide-approval", write["approval_id"],
                                      "--workspace", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 1
    assert "worker is not running" in capsys.readouterr().err


def test_decide_rejects_other_modes(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["vibe", "--decide-approval", "a-1", "--workspace", "/tmp", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--decide-approval cannot be combined" in capsys.readouterr().err


def test_decide_approval_via_worker_attach(tmp_path, monkeypatch, capsys):
    import asyncio
    import threading

    from app.runtime.worker import Worker

    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="approval", workspace=str(tmp_path), mode="goal")
    inbox = ApprovalInbox(session.root)
    requested = inbox.submit(task.task_id, tool="bash", tier="exec",
                             decisions=["once", "deny"], preview="ls")
    socket_dir = tmp_path / "sock"
    socket_dir.mkdir()
    monkeypatch.setenv("VIBE_WORKER_SOCKET_DIR", str(socket_dir))
    worker = Worker(session_root=session.root, task_id=task.task_id, workspace=tmp_path,
                    child_args=[], child_env={})
    from app.runtime.worker import socket_path
    sock = socket_path(store.task_dir(task.task_id), task.task_id)
    ready, stop = threading.Event(), threading.Event()

    def serve():
        async def scenario():
            server = await asyncio.start_unix_server(worker._client, path=str(sock))
            ready.set()
            try:
                while not stop.is_set():
                    await asyncio.sleep(0.02)
            finally:
                server.close()
                await server.wait_closed()

        asyncio.run(scenario())

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        assert ready.wait(3), "worker test server did not start"
        monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
        monkeypatch.setattr("sys.argv", ["vibe", "--decide-approval", requested["approval_id"],
                                         "--approval-decision", "deny", "--workspace", str(tmp_path)])
        entrypoint.main()
        payload = json.loads(capsys.readouterr().out)
        assert payload["approval_id"] == requested["approval_id"]
        assert payload["task_id"] == task.task_id
        assert payload["decision"] == "deny"
        assert payload["status"] == "resolved"
        item = next(entry for entry in inbox.items(task.task_id)
                    if entry["approval_id"] == requested["approval_id"])
        assert item["status"] == "resolved" and item["decision"] == "deny"
    finally:
        stop.set()
        thread.join(3)


def test_approvals_reports_corrupt_records(tmp_path, monkeypatch, capsys):
    session, task, _write = _seed(tmp_path)
    path = ApprovalInbox(session.root).path_for(task.task_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{bad json\n")
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--approvals", "--workspace", str(tmp_path)])
    entrypoint.main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["skipped_approval_records"] == 1
    assert payload["count"] == 2
    assert "skipped 1 corrupt approval records" in captured.err
