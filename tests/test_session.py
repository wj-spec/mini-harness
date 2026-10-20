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
