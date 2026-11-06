"""worker attach controller/observer 握手测试。"""

from __future__ import annotations

import asyncio
import json

import pytest
import os
import socket
import tempfile
from pathlib import Path

from app.runtime.approvals import ApprovalInbox
from app.runtime.tasks import TaskStore
from app.runtime.worker import Worker
from app.session.store import SessionStore


def _worker(tmp_path):
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="attach", workspace=str(tmp_path), mode="goal")
    worker = Worker(
        session_root=session.root, task_id=task.task_id, workspace=tmp_path,
        child_args=[], child_env={},
    )
    return session, store, task, worker


async def _connect(path: str, mode: str, *, take_control: bool = False,
                   capabilities: list[str] | None = None):
    reader, writer = await asyncio.open_unix_connection(path)
    caps = ["task_snapshot", "approval_list", "approval_decide", "control_lease"] if capabilities is None else capabilities
    writer.write((json.dumps({"v": 1, "type": "attach", "id": "h", "mode": mode,
                              "take_control": take_control,
                              "capabilities": caps}) + "\n").encode())
    await writer.drain()
    reply = json.loads((await asyncio.wait_for(reader.readline(), 2)).decode())
    if reply.get("type") == "attach_ok" and "task_snapshot" in caps:
        snapshot = json.loads((await asyncio.wait_for(reader.readline(), 2)).decode())
        assert snapshot["type"] == "task_snapshot"
    return reader, writer, reply


def test_controller_observer_and_takeover(tmp_path):
    session, store, task, worker = _worker(tmp_path)
    path = str(Path(tempfile.mkdtemp(prefix="vibe-attach-")) / "ctrl.sock")

    async def scenario():
        server = await asyncio.start_unix_server(worker._client, path=path)
        try:
            first_reader, first_writer, first = await _connect(path, "control")
            assert first["type"] == "attach_ok" and first["mode"] == "control"

            _, denied_writer, denied = await _connect(path, "control")
            assert denied["type"] == "error" and denied["code"] == "attach_denied"
            denied_writer.close()

            observer_reader, observer_writer, observer = await _connect(path, "observe")
            assert observer["type"] == "attach_ok" and observer["mode"] == "observe"
            observer_writer.write((json.dumps({"v": 1, "type": "cancel", "id": "c",
                                               "target": "run"}) + "\n").encode())
            await observer_writer.drain()
            error = json.loads((await asyncio.wait_for(observer_reader.readline(), 2)).decode())
            assert error["code"] == "observer_readonly"
            observer_writer.write((json.dumps({"v": 1, "type": "approval_decide", "id": "d",
                                               "approval_id": "a1", "decision": "once"}) + "\n").encode())
            await observer_writer.drain()
            error = json.loads((await asyncio.wait_for(observer_reader.readline(), 2)).decode())
            assert error["code"] == "observer_readonly"
            observer_writer.close()

            second_reader, second_writer, second = await _connect(path, "control", take_control=True)
            assert second["type"] == "attach_ok"
            taken = json.loads((await asyncio.wait_for(first_reader.readline(), 2)).decode())
            assert taken["code"] == "control_taken"
            first_writer.close()
            second_writer.close()
            await asyncio.sleep(0.05)
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_controller_can_resolve_approval_and_forward_frame(tmp_path):
    session, store, task, worker = _worker(tmp_path)
    path = str(Path(tempfile.mkdtemp(prefix="vibe-attach-")) / "ctrl.sock")
    inbox = ApprovalInbox(session.root)
    requested = inbox.submit(task.task_id, tool="bash")

    async def scenario():
        server = await asyncio.start_unix_server(worker._client, path=path)
        try:
            reader, writer, reply = await _connect(path, "control")
            assert reply["type"] == "attach_ok"
            frame = {"v": 1, "type": "approval", "id": "a1",
                     "approval_id": requested["approval_id"], "decision": "once"}
            writer.write((json.dumps(frame) + "\n").encode())
            await writer.drain()
            await asyncio.sleep(0.05)
            assert inbox.items(task.task_id)[0]["status"] == "resolved"
            assert not worker.queue.empty()
            writer.close()
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_controller_heartbeat_expiry(tmp_path):
    import time
    session, store, task, worker = _worker(tmp_path)
    assert not worker.controller_expired(ttl=5)
    worker.controller = object()
    worker.controller_heartbeat_at = time.monotonic() - 10
    assert worker.controller_expired(ttl=5)
    assert not worker.controller_expired(ttl=60)

def test_attach_without_snapshot_capability_gets_no_snapshot(tmp_path):
    session, store, task, worker = _worker(tmp_path)
    path = str(Path(tempfile.mkdtemp(prefix="vibe-attach-")) / "ctrl.sock")

    async def scenario():
        server = await asyncio.start_unix_server(worker._client, path=path)
        try:
            reader, writer, reply = await _connect(path, "observe", capabilities=[])
            assert reply["type"] == "attach_ok"
            assert reply["capabilities"] == []
            import asyncio as _asyncio
            with pytest.raises(_asyncio.TimeoutError):
                await _asyncio.wait_for(reader.readline(), 0.2)
            writer.close()
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_v2_task_snapshot_approval_list_and_decide(tmp_path):
    session, store, task, worker = _worker(tmp_path)
    path = str(Path(tempfile.mkdtemp(prefix="vibe-attach-")) / "ctrl.sock")
    inbox = ApprovalInbox(session.root)
    requested = inbox.submit(task.task_id, tool="bash")

    async def scenario():
        server = await asyncio.start_unix_server(worker._client, path=path)
        try:
            reader, writer, reply = await _connect(path, "control")
            assert reply["type"] == "attach_ok"
            assert "approval_decide" in reply["capabilities"]
            writer.write((json.dumps({"v": 1, "type": "approval_list_request", "id": "l1"}) + "\n").encode())
            await writer.drain()
            listed = json.loads((await asyncio.wait_for(reader.readline(), 2)).decode())
            assert listed["type"] == "approval_list"
            assert listed["id"] == "l1" and listed["approvals"][0]["approval_id"] == requested["approval_id"]

            writer.write((json.dumps({"v": 1, "type": "approval_decide", "id": "d1",
                                      "approval_id": requested["approval_id"], "decision": "once"}) + "\n").encode())
            await writer.drain()
            await asyncio.sleep(0.05)
            assert inbox.items(task.task_id)[0]["status"] == "resolved"
            forwarded = json.loads(worker.queue.get_nowait())
            assert forwarded["type"] == "approval" and forwarded["decision"] == "once"
            writer.close()
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_attach_shutdown_frame_detaches_without_stopping_child(tmp_path):
    session, store, task, worker = _worker(tmp_path)
    path = str(Path(tempfile.mkdtemp(prefix="vibe-attach-")) / "ctrl.sock")

    async def scenario():
        server = await asyncio.start_unix_server(worker._client, path=path)
        try:
            reader, writer, reply = await _connect(path, "control")
            writer.write((json.dumps({"v": 1, "type": "shutdown", "id": "s1"}) + "\n").encode())
            await writer.drain()
            await asyncio.sleep(0.1)
            assert worker.queue.empty()
            writer.close()
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
