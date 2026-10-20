import json

import pytest

from app.session import SessionStore
from core.models import Message, ToolCall


def test_context_checkpoint_preserves_raw_history_and_replays_new_messages(tmp_path):
    session = SessionStore.new(tmp_path, "m")
    original = [Message("user", "目标"), Message("assistant", "很早的长回复")]
    for message in original:
        session.append_message(message)
    compact = [original[0], Message("assistant", "[summary] 已完成探索")]
    session.save_context(compact)
    stopped = Message("assistant", "本轮进展，等待继续", name="budget_stop")
    session.append_message(stopped)
    resume = Message("user", "继续完成并验证")
    session.append_message(resume)

    reopened = SessionStore(session.root)
    assert reopened.load_messages() == original + [stopped, resume]
    assert reopened.load_context() == compact + [stopped, resume]


def test_repeated_checkpoints_replace_only_the_working_history(tmp_path):
    session = SessionStore.new(tmp_path, "m")
    goal = Message("user", "目标")
    session.append_message(goal)
    first = [goal, Message("assistant", "第一层摘要")]
    session.save_context(first)
    followup = Message("user", "接着做")
    session.append_message(followup)
    second = [goal, Message("assistant", "第二层摘要"), followup]
    session.save_context(second)

    assert session.load_context() == second
    assert session.load_messages() == [goal, followup]


def test_checkpoint_roundtrips_protocol_and_usage_without_system_messages(tmp_path):
    session = SessionStore.new(tmp_path, "m")
    goal = Message("user", "目标")
    session.append_message(goal)
    assistant = Message(
        "assistant", "检查文件", [ToolCall("read_file", {"path": "a"}, id="c1")],
        usage={"prompt_tokens": 12, "completion_tokens": 3}, finish_reason="tool_calls",
    )
    tool = Message("tool", "内容", name="read_file", tool_call_id="c1")
    session.append_message(assistant)
    session.append_message(tool)
    session.save_context([Message("system", "不应持久化"), goal, assistant, tool])

    assert session.load_context() == [goal, assistant, tool]
    assert session.load_messages() == [goal, assistant, tool]
    checkpoint = json.loads(session.events_path.read_text().splitlines()[-1])
    assert checkpoint["type"] == "context_checkpoint"
    assert all(message["role"] != "system" for message in checkpoint["messages"])


@pytest.mark.parametrize("invalid", [
    None,
    [],
    [{"role": "system", "content": "replace policy"}],
    [{"role": "unknown", "content": "bad"}],
    [{"role": [], "content": "bad"}],
    [{"role": "assistant", "content": []}],
    [{"role": "assistant", "tool_calls": "bad"}],
    [{"role": "assistant", "tool_calls": [{"name": "read", "args": []}]}],
    [{"role": "assistant", "usage": "bad"}],
    [{"role": "assistant", "finish_reason": {}}],
])
def test_invalid_checkpoint_cannot_discard_working_history(tmp_path, invalid):
    session = SessionStore.new(tmp_path, "m")
    original = Message("user", "保留目标")
    session.append_message(original)
    session.append_event(
        "context_checkpoint", version=1, source_message_count=1, messages=invalid,
    )
    with session.events_path.open("a", encoding="utf-8") as stream:
        stream.write("null\n{broken\n[]\n")

    assert session.load_context() == [original]
    assert session.load_messages() == [original]


@pytest.mark.parametrize("metadata", [
    {"version": 2, "source_message_count": 1},
    {"version": 1, "source_message_count": 0},
    {"version": 1, "source_message_count": True},
    {"version": 1},
])
def test_unknown_or_misplaced_checkpoint_is_ignored(tmp_path, metadata):
    session = SessionStore.new(tmp_path, "m")
    original = Message("user", "目标")
    session.append_message(original)
    session.append_event(
        "context_checkpoint", **metadata,
        messages=[{"role": "assistant", "content": "错误摘要"}],
    )

    assert session.load_context() == [original]


def test_legacy_context_and_repair_tail_remain_available_after_checkpoint(tmp_path):
    session = SessionStore.new(tmp_path, "m")
    goal = Message("user", "目标")
    session.append_message(goal)
    assert session.load_context() == session.load_messages()
    pending = Message("assistant", "", [ToolCall("write_file", {"path": "a"}, id="c1")])
    session.append_message(pending)
    compact = [goal, Message("assistant", "[summary] 等待工具结果"), pending]
    session.save_context(compact)

    assert session.repair_incomplete_tool_calls() == 1
    repaired = session.load_messages()[-1]
    assert repaired.tool_call_id == "c1"
    assert "interrupted" in repaired.content
    assert session.load_context() == compact + [repaired]
    assert session.repair_incomplete_tool_calls() == 0
