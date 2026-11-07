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
    from app.runtime.notifications import read_notifications
    notices = [item for item in read_notifications(tmp_path) if item["kind"] == "unattended_denied"]
    assert notices and notices[-1]["tool"] == "bash"


def test_relaxed_settings_reports_only_loosened_fields():
    from app.safety.unattended import profile_hash, relaxed_settings

    assert relaxed_settings(UnattendedProfile()) == []
    strict = UnattendedProfile(allow_read=False, allow_write=False, require_worktree=True)
    assert relaxed_settings(strict) == []
    relaxed = UnattendedProfile(allow_exec=True, allow_network=True, require_worktree=False)
    assert relaxed_settings(relaxed) == [
        "allow_exec", "allow_network", "require_worktree=false",
    ]
    assert profile_hash(UnattendedProfile()) == profile_hash(UnattendedProfile())
    assert len(profile_hash(relaxed)) == 64


def test_profile_approval_roundtrip_and_change_detection(tmp_path, monkeypatch):
    from app.safety.unattended import (
        check_profile_approval, profile_approval_required, write_profile_approval,
    )

    path = tmp_path / ".vibe" / "unattended.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"allow_exec": True}), encoding="utf-8")
    profile = load_profile(tmp_path)
    monkeypatch.delenv("VIBE_UNATTENDED_REQUIRE_APPROVAL", raising=False)
    assert profile_approval_required() is False
    approved, reason = check_profile_approval(tmp_path, profile)
    assert approved is False and "missing" in reason
    write_profile_approval(tmp_path, profile, note="test")
    approved, reason = check_profile_approval(tmp_path, profile)
    assert approved is True
    path.write_text(json.dumps({"allow_exec": True, "allow_network": True}), encoding="utf-8")
    changed = load_profile(tmp_path)
    approved, reason = check_profile_approval(tmp_path, changed)
    assert approved is False and "changed" in reason
    monkeypatch.setenv("VIBE_UNATTENDED_REQUIRE_APPROVAL", "1")
    assert profile_approval_required() is True


def test_approve_unattended_profile_cli(tmp_path, monkeypatch, capsys):
    from app import __main__ as entrypoint
    from app.config import Config
    import sys

    (tmp_path / ".vibe").mkdir()
    (tmp_path / ".vibe" / "unattended.json").write_text(
        json.dumps({"allow_exec": True}), encoding="utf-8",
    )
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr(sys, "argv", ["vibe", "--approve-unattended-profile",
                                      "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["relaxed"] == ["allow_exec"]
    assert (tmp_path / ".vibe" / "unattended.approval.json").is_file()
    from app.runtime.notifications import read_notifications
    assert any(item["kind"] == "unattended_profile_approved"
               for item in read_notifications(tmp_path))


def test_approve_unattended_profile_rejects_other_modes(tmp_path, monkeypatch, capsys):
    from app import __main__ as entrypoint
    import sys

    monkeypatch.setattr(sys, "argv", ["vibe", "--approve-unattended-profile",
                                      "--workspace", str(tmp_path), "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--approve-unattended-profile cannot be combined" in capsys.readouterr().err
