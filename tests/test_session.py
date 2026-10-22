import json
import os

import pytest

from app.session import SessionStore
from core.models import Message, ToolCall
from core.state import TaskState


def test_session_roundtrip(tmp_path):
    session = SessionStore.new(tmp_path, "model-x")
    session.append_message(Message("user", "hi"))
    session.append_message(
        Message("assistant", "", [ToolCall("read_file", {"path": "a"}, id="c1")])
    )
    session.append_message(
        Message("tool", "ok", name="read_file", tool_call_id="c1")
    )
    session.save_state(TaskState(goal="g", done=["a"]))

    messages = session.load_messages()
    assert [m.role for m in messages] == ["user", "assistant", "tool"]
    assert messages[1].tool_calls[0].id == "c1"
    assert messages[2].tool_call_id == "c1"

    state = session.load_state()
    assert state is not None
    assert state.goal == "g"
    assert state.done == ["a"]

    latest = SessionStore.latest(tmp_path)
    assert latest is not None
    assert latest.id == session.id


def test_latest_skips_newer_sessions_without_conversation(tmp_path):
    previous = SessionStore.new(tmp_path, "m")
    previous.append_message(Message("user", "继续实现 MCP"))
    SessionStore.new(tmp_path, "m")
    broken = SessionStore.new(tmp_path, "m")
    broken.events_path.write_text('null\n{broken\n{"type":"opened"}\n', encoding="utf-8")

    assert SessionStore.latest(tmp_path).id == previous.id


def test_latest_returns_none_when_only_empty_sessions_exist(tmp_path):
    SessionStore.new(tmp_path, "m")

    assert SessionStore.latest(tmp_path) is None


def test_latest_orders_by_message_timestamp_instead_of_directory_mtime(tmp_path):
    older = SessionStore.new(tmp_path, "m")
    older.append_message(Message("user", "早先的任务"))
    newer = SessionStore.new(tmp_path, "m")
    newer.append_message(Message("user", "context smoke"))
    event = json.loads(older.events_path.read_text())
    event["ts"] = "2026-10-20T01:00:00+00:00"
    older.events_path.write_text(json.dumps(event) + "\n")
    event = json.loads(newer.events_path.read_text())
    event["ts"] = "2026-10-21T01:00:00+00:00"
    newer.events_path.write_text(json.dumps(event) + "\n")
    older.append_event("opened")
    os.utime(older.root, (2_000_000_000, 2_000_000_000))

    assert SessionStore.latest(tmp_path).id == newer.id
    assert SessionStore.list_sessions(tmp_path)[0]["id"] == newer.id


def test_session_listing_reports_full_message_count_and_safe_first_user_title(tmp_path):
    session = SessionStore.new(tmp_path, "m")
    session.append_message(Message("system", "private system prompt"))
    session.append_message(Message("user", "修复\x1b[31m接口\t问题\n不要展示第二行"))
    session.append_message(Message("assistant", "private assistant output"))
    session.append_message(Message("tool", "private result", name="bash"))
    session.save_context([Message("user", "压缩后工作上下文")])
    before = session.events_path.read_bytes()

    rows = SessionStore.list_sessions(tmp_path)

    assert len(rows) == 1
    assert set(rows[0]) == {"id", "message_count", "title", "updated_at"}
    assert rows[0]["id"] == session.id and rows[0]["message_count"] == 4
    assert "修复" in rows[0]["title"] and "接口" in rows[0]["title"]
    assert "\x1b" not in rows[0]["title"] and "\t" not in rows[0]["title"]
    assert "不要展示" not in str(rows) and "private" not in str(rows)
    assert rows[0]["updated_at"]
    assert session.events_path.read_bytes() == before


def test_session_listing_skips_empty_and_broken_messages_and_limits_title(tmp_path):
    SessionStore.new(tmp_path, "m")
    session = SessionStore.new(tmp_path, "m")
    session.events_path.write_text('null\n{"type":"message","message":{}}\n')
    session.append_message(Message("user", "任" * 300))

    rows = SessionStore.list_sessions(tmp_path)

    assert len(rows) == 1 and rows[0]["message_count"] == 1
    assert len(rows[0]["title"]) <= 81


def test_latest_handles_legacy_message_without_timestamp_using_event_file_mtime(tmp_path):
    session = SessionStore.new(tmp_path, "m")
    session.events_path.write_text(json.dumps({
        "type": "message", "message": {"role": "user", "content": "旧会话"},
    }) + "\n")

    assert SessionStore.latest(tmp_path).id == session.id
    assert SessionStore.list_sessions(tmp_path)[0]["updated_at"]


@pytest.mark.parametrize("session_id", ["", ".", "..", "../outside", "/tmp/session", "a/b", r"a\b"])
def test_open_existing_rejects_non_single_session_ids(tmp_path, session_id):
    with pytest.raises(ValueError):
        SessionStore.open_existing(tmp_path, session_id)


def test_open_existing_uses_exact_session_without_creating_missing_path(tmp_path):
    older = SessionStore.new(tmp_path, "m")
    older.append_message(Message("user", "真实任务"))
    newer = SessionStore.new(tmp_path, "m")
    newer.append_message(Message("user", "context smoke"))

    assert SessionStore.open_existing(tmp_path, older.id).id == older.id
    assert SessionStore.open_existing(tmp_path, "missing") is None
    assert not (tmp_path / ".vibe" / "sessions" / "missing").exists()


def test_session_selection_and_listing_reject_directory_symlinks(tmp_path):
    outside = tmp_path / "other-workspace"
    session = SessionStore.new(outside, "m")
    session.append_message(Message("user", "另一个工作区"))
    base = tmp_path / ".vibe" / "sessions"
    base.mkdir(parents=True)
    (base / "linked").symlink_to(session.root, target_is_directory=True)

    assert SessionStore.open_existing(tmp_path, "linked") is None
    assert SessionStore.list_sessions(tmp_path) == []


def test_session_selection_does_not_follow_sessions_root_outside_workspace(tmp_path):
    outside = tmp_path / "outside"
    session = SessionStore.new(outside, "m")
    session.append_message(Message("user", "另一个工作区"))
    workspace = tmp_path / "workspace"
    (workspace / ".vibe").mkdir(parents=True)
    (workspace / ".vibe" / "sessions").symlink_to(session.root.parent, target_is_directory=True)

    assert SessionStore.open_existing(workspace, session.id) is None
    assert SessionStore.list_sessions(workspace) == []


def test_session_listing_skips_untrusted_directory_names(tmp_path):
    session = SessionStore.new(tmp_path, "m")
    session.append_message(Message("user", "有效会话"))
    (session.root.parent / "bad\x1b[31m").mkdir()

    assert [row["id"] for row in SessionStore.list_sessions(tmp_path)] == [session.id]


def test_repair_incomplete_tool_calls_is_idempotent(tmp_path):
    session = SessionStore.new(tmp_path, "m")
    session.append_message(Message("user", "q"))
    session.append_message(
        Message("assistant", "", [ToolCall("write_file", {"path": "a"}, id="c1")])
    )

    assert session.repair_incomplete_tool_calls() == 1
    messages = session.load_messages()
    assert messages[-1].role == "tool"
    assert "interrupted" in messages[-1].content

    assert session.repair_incomplete_tool_calls() == 0


def test_cost_summary(tmp_path):
    session = SessionStore.new(tmp_path, "m")
    session.append_trace(
        {"type": "run_end", "prompt_tokens": 10, "completion_tokens": 5,
         "total_tokens": 15, "cost_usd": 0.01, "tool_calls": 2, "status": "done"}
    )
    session.append_trace(
        {"type": "run_end", "prompt_tokens": 20, "completion_tokens": 10,
         "total_tokens": 30, "cost_usd": 0.02, "tool_calls": 3, "status": "cancelled"}
    )
    summary = session.cost_summary()
    assert summary["runs"] == 2
    assert summary["prompt_tokens"] == 30
    assert summary["completion_tokens"] == 15
    assert summary["total_tokens"] == 45
    assert summary["cost_usd"] == 0.03
    assert summary["tool_calls"] == 5
    assert summary["last_status"] == "cancelled"


def test_latest_run_returns_latest_stop_and_ignores_broken_lines(tmp_path):
    session = SessionStore.new(tmp_path, "m")
    session.append_trace({"type": "run_start", "run_id": "a"})
    stop = {
        "type": "run_end", "run_id": "a", "status": "stopped",
        "stop_reason": "token_budget", "total_tokens": 208401,
        "limit_tokens": 200000, "remaining_tokens": 0,
    }
    session.append_trace(stop)
    with session.trace_path.open("a", encoding="utf-8") as stream:
        stream.write("broken json\nnull\n")
    session.append_trace({"type": "llm_end", "run_id": "a", "total_tokens": 5})

    assert session.latest_run() == stop


def test_latest_run_does_not_reuse_old_end_after_new_start(tmp_path):
    session = SessionStore.new(tmp_path, "m")
    session.append_trace({"type": "run_end", "run_id": "a", "status": "stopped"})
    start = {"type": "run_start", "run_id": "b", "task": "continue"}
    session.append_trace(start)
    session.append_trace({"type": "llm_start", "run_id": "b", "step": 1})

    assert session.latest_run() == start


def test_latest_run_handles_missing_and_legacy_trace(tmp_path):
    session = SessionStore.new(tmp_path, "m")
    assert session.latest_run() is None
    legacy_stop = {"type": "run_end", "status": "stopped", "total_tokens": 208401}
    session.append_trace(legacy_stop)
    assert session.latest_run() == legacy_stop
