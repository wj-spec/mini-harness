import json

import pytest

from app.session import SessionStore
from core.models import Message


def conversation(tmp_path, purpose="user", *, stamp="2026-10-20T01:00:00+00:00"):
    session = SessionStore.new(tmp_path, "model-x", purpose=purpose)
    session.append_message(Message("user", f"{purpose} 会话"))
    event = json.loads(session.events_path.read_text(encoding="utf-8"))
    event["ts"] = stamp
    session.events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    return session


def test_new_sessions_persist_explicit_purpose_and_default_to_user(tmp_path):
    user = SessionStore.new(tmp_path, "model-x")
    test = SessionStore.new(tmp_path, "model-x", purpose="test")

    assert json.loads(user.meta_path.read_text())["purpose"] == "user"
    assert json.loads(test.meta_path.read_text())["purpose"] == "test"
    assert user.purpose == "user"
    assert test.purpose == "test"


def test_normal_scope_never_lists_or_opens_test_sessions_even_by_exact_id(tmp_path):
    test = conversation(tmp_path, "test")
    before = test.meta_path.read_bytes(), test.events_path.read_bytes()

    assert SessionStore.latest(tmp_path) is None
    assert SessionStore.list_sessions(tmp_path) == []
    assert SessionStore.open_existing(tmp_path, test.id) is None
    assert (test.meta_path.read_bytes(), test.events_path.read_bytes()) == before


def test_newer_test_conversation_does_not_replace_the_latest_user_conversation(tmp_path):
    user = conversation(tmp_path)
    test = conversation(tmp_path, "test", stamp="2026-10-21T01:00:00+00:00")

    assert SessionStore.latest(tmp_path).id == user.id
    assert [row["id"] for row in SessionStore.list_sessions(tmp_path)] == [user.id]
    assert SessionStore.open_existing(tmp_path, user.id).id == user.id
    assert SessionStore.open_existing(tmp_path, test.id) is None


def test_test_scope_can_only_load_test_sessions(tmp_path):
    test = conversation(tmp_path, "test")
    user = conversation(tmp_path, stamp="2026-10-21T01:00:00+00:00")

    assert SessionStore.latest(tmp_path, purpose="test").id == test.id
    assert [row["id"] for row in SessionStore.list_sessions(tmp_path, purpose="test")] == [test.id]
    assert SessionStore.open_existing(tmp_path, test.id, purpose="test").id == test.id
    assert SessionStore.open_existing(tmp_path, user.id, purpose="test") is None


@pytest.mark.parametrize("missing_meta", [False, True])
def test_legacy_sessions_without_purpose_are_normal_user_sessions(tmp_path, missing_meta):
    session = conversation(tmp_path)
    if missing_meta:
        session.meta_path.unlink()
    else:
        meta = json.loads(session.meta_path.read_text())
        del meta["purpose"]
        session.meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert session.purpose == "user"
    assert SessionStore.latest(tmp_path).id == session.id
    assert SessionStore.open_existing(tmp_path, session.id).id == session.id
    assert SessionStore.latest(tmp_path, purpose="test") is None
    assert SessionStore.open_existing(tmp_path, session.id, purpose="test") is None


@pytest.mark.parametrize("content", [
    "broken json", "null", "[]", '{"purpose":"unknown"}',
    '{"purpose":null}', '{"purpose":false}', '{"purpose":{}}',
])
def test_invalid_session_metadata_is_never_assumed_to_be_a_user_session(tmp_path, content):
    session = conversation(tmp_path)
    session.meta_path.write_text(content, encoding="utf-8")

    assert session.purpose is None
    for purpose in ("user", "test"):
        assert SessionStore.open_existing(tmp_path, session.id, purpose=purpose) is None
        assert SessionStore.list_sessions(tmp_path, purpose=purpose) == []
        assert SessionStore.latest(tmp_path, purpose=purpose) is None


def test_metadata_symlinks_cannot_change_a_session_scope(tmp_path):
    session = conversation(tmp_path, "test")
    outside = tmp_path / "outside-meta.json"
    outside.write_text('{"purpose":"user"}', encoding="utf-8")
    session.meta_path.unlink()
    session.meta_path.symlink_to(outside)

    assert session.purpose is None
    assert SessionStore.open_existing(tmp_path, session.id) is None
    assert SessionStore.open_existing(tmp_path, session.id, purpose="test") is None


@pytest.mark.parametrize("purpose", ["unknown", "", None, False, []])
def test_invalid_requested_scope_is_rejected_without_creating_sessions(tmp_path, purpose):
    with pytest.raises(ValueError, match="purpose"):
        SessionStore.new(tmp_path, "model-x", purpose=purpose)
    assert not (tmp_path / ".vibe").exists()

    with pytest.raises(ValueError, match="purpose"):
        SessionStore.open_existing(tmp_path, "missing", purpose=purpose)
    with pytest.raises(ValueError, match="purpose"):
        SessionStore.list_sessions(tmp_path, purpose=purpose)
    with pytest.raises(ValueError, match="purpose"):
        SessionStore.latest(tmp_path, purpose=purpose)
