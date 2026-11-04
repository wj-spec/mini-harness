"""ApprovalInbox P0 测试（spec: docs/specs/approval-inbox.md）。"""

from __future__ import annotations

import json

import pytest

from app.runtime.approvals import (
    ApprovalInbox,
    ApprovalMismatch,
    ApprovalNotFound,
    redact_preview,
)
from app.runtime.tasks import TaskStore


def _inbox(tmp_path):
    session = tmp_path / "session"
    store = TaskStore(session)
    task = store.create(kind="goal", objective="审批测试", workspace=str(tmp_path), mode="goal")
    return session, ApprovalInbox(session), task


def test_submit_pending_and_resolve_once(tmp_path):
    session, inbox, task = _inbox(tmp_path)
    requested = inbox.submit(task.task_id, tool="bash", tier="exec",
                             decisions=["once", "deny"], preview="secret sk-abcdefgh12345678",
                             preview_ref="messages.jsonl#step=1", requester="parent")
    assert requested["preview_hash"]
    assert requested["preview_redacted"] is None
    pending = inbox.pending(task.task_id)
    assert [item["approval_id"] for item in pending] == [requested["approval_id"]]

    resolved = inbox.resolve(task.task_id, requested["approval_id"], decision="once",
                             scope="once", decided_by="user", decider="user")
    assert resolved["status"] == "resolved"
    assert inbox.pending(task.task_id) == []
    with pytest.raises(ApprovalMismatch):
        inbox.resolve(task.task_id, requested["approval_id"], decision="once")
    with pytest.raises(ApprovalNotFound):
        inbox.resolve(task.task_id, "a-missing", decision="once")


def test_expire_is_idempotent(tmp_path):
    session, inbox, task = _inbox(tmp_path)
    first = inbox.submit(task.task_id, tool="write_file")
    inbox.submit(task.task_id, tool="bash")
    expired = inbox.expire(task.task_id, reason="approval_timeout")
    assert len(expired) == 2
    assert all(item["status"] == "expired" for item in expired)
    assert inbox.expire(task.task_id) == []
    items = inbox.items(task.task_id)
    assert {item["approval_id"] for item in items} == {
        first["approval_id"], items[0]["approval_id"], items[1]["approval_id"],
    }


def test_redaction_and_preview_body_cap(tmp_path):
    assert "sk-" not in redact_preview("key sk-abcdefgh12345678 done")
    assert "[REDACTED]" in redact_preview("AWS_SECRET=abc123")
    assert "~" in redact_preview("/home/u/private", home="/home/u")
    session, inbox, task = _inbox(tmp_path)
    entry = inbox.submit(task.task_id, tool="bash", preview="sk-abcdefgh12345678\n" + "x" * 20000,
                         include_preview=True)
    assert entry["preview_redacted"] is not None
    assert "sk-" not in entry["preview_redacted"]
    assert len(entry["preview_redacted"].encode("utf-8")) <= 8 * 1024


def test_task_isolation_and_corrupt_lines(tmp_path):
    session, inbox, first = _inbox(tmp_path)
    second = TaskStore(session).create(kind="goal", objective="第二个", workspace=str(tmp_path), mode="goal")
    inbox.submit(first.task_id, tool="bash")
    inbox.submit(second.task_id, tool="write_file")
    assert len(inbox.pending(first.task_id)) == 1
    assert len(inbox.pending(second.task_id)) == 1
    path = inbox.path_for(first.task_id)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{bad\n")
    assert len(inbox.pending(first.task_id)) == 1
    assert len(inbox.items()) == 2
