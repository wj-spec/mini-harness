from __future__ import annotations

import json

import pytest

from app.safety.unattended import UnattendedProfile, UnattendedProfileError, load_profile


def test_conservative_profile_denies_exec_network_mcp():
    profile = UnattendedProfile()
    assert profile.allows_tool("read_file")
    assert profile.allows_tool("write_file", worktree=True)
    assert not profile.allows_tool("write_file", worktree=False)
    assert not profile.allows_tool("bash")
    assert not profile.allows_tool("web_search")
    assert not profile.allows_tool("mcp_filesystem_read")
    assert not profile.allows_tool("browser_click")
    assert "exec disabled" in (profile.denial_reason("bash") or "")


def test_profile_file_overrides_and_unknown_keys_rejected(tmp_path):
    path = tmp_path / ".vibe" / "unattended.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"allow_exec": True, "allow_network": True}), encoding="utf-8")
    profile = load_profile(tmp_path)
    assert profile.allows_tool("bash")
    assert profile.allows_tool("web_search")
    path.write_text(json.dumps({"unknown": 1}), encoding="utf-8")
    with pytest.raises(UnattendedProfileError):
        load_profile(tmp_path)


def test_profile_missing_uses_conservative(tmp_path):
    profile = load_profile(tmp_path)
    assert not profile.allow_exec
    assert profile.profile_id == "unattended-conservative"


def test_worker_auto_denies_exec_approval_without_inbox(tmp_path):
    import asyncio

    from app.runtime.tasks import TaskStore
    from app.runtime.worker import Worker
    from app.session.store import SessionStore

    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="unattended", workspace=str(tmp_path), mode="goal")
    worker = Worker(
        session_root=session.root, task_id=task.task_id, workspace=tmp_path,
        child_args=[], child_env={},
    )
    worker.profile = UnattendedProfile()
    asyncio.run(worker._record_approval({
        "approval_id": "a-1", "call": {"name": "bash", "args": {}},
        "preview": "rm -rf build", "tier": "exec", "decisions": ["once", "deny"],
    }))
    assert worker.inbox.items(task.task_id) == []
    frame = json.loads(worker.queue.get_nowait())
    assert frame["type"] == "approval" and frame["decision"] == "deny"
    events = (store.task_dir(task.task_id) / "events.jsonl").read_text(encoding="utf-8")
    assert "unattended_denied" in events
