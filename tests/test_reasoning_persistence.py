import json
from dataclasses import asdict

import pytest

from app.session import SessionStore
from core.context import ContextBuilder, message_tokens, total_tokens
from core.models import Message, Response, ToolCall, message_from_dict


def test_reasoning_metadata_is_optional_without_changing_positional_fields():
    call = ToolCall("read_file", {"path": "a.py"}, "read-1")
    message = Message("assistant", "Reading", [call], {"total_tokens": 4}, "tool_calls")
    response = Response("Reading", [call], {"total_tokens": 4}, "tool_calls")

    assert message.reasoning_content is None
    assert response.reasoning_content is None
    assert message.content == response.content == "Reading"
    assert message.finish_reason == response.finish_reason == "tool_calls"


@pytest.mark.parametrize("reasoning", [None, "", "Inspect the failing parser first."])
def test_session_message_and_checkpoint_preserve_reasoning(tmp_path, reasoning):
    session = SessionStore.new(tmp_path, "reasoning-model")
    message = Message(
        "assistant", "Reading", [ToolCall("read_file", {}, "read-1")],
        reasoning_content=reasoning,
    )
    session.append_message(Message("user", "Fix parser"))
    session.append_message(message)
    session.save_context([Message("user", "Fix parser"), message])

    assert session.load_messages()[-1] == message
    assert session.load_context()[-1] == message
    assert session.load_context()[-1].content == "Reading"
    payloads = [json.loads(line) for line in session.events_path.read_text().splitlines()]
    assert payloads[1]["message"]["reasoning_content"] == reasoning
    assert payloads[2]["messages"][-1]["reasoning_content"] == reasoning


@pytest.mark.parametrize("loader", [message_from_dict])
def test_legacy_message_without_reasoning_loads_as_none(loader):
    message = loader({"role": "assistant", "content": "legacy"})

    assert message.reasoning_content is None


@pytest.mark.parametrize("loader", [message_from_dict])
@pytest.mark.parametrize("reasoning", [1, False, [], {}])
def test_loading_invalid_reasoning_metadata_is_rejected(loader, reasoning):
    with pytest.raises(ValueError, match="reasoning_content"):
        loader({"role": "assistant", "content": "answer", "reasoning_content": reasoning})


def test_token_budget_includes_reasoning_without_changing_visible_content():
    message = Message("assistant", "reply", reasoning_content="private reasoning")

    assert message_tokens(message, estimator=len) == len("replyprivate reasoning")
    assert total_tokens([message], estimator=len) == len("replyprivate reasoning")
    assert message.content == "reply"


def test_context_compaction_preserves_recent_and_archived_reasoning(tmp_path):
    old = Message(
        "assistant", "Inspect source", [ToolCall("read_file", {"path": "a.py"}, "read-1")],
        reasoning_content="PRIVATE_OLD_REASONING " * 80,
    )
    tool = Message("tool", "source contents", name="read_file", tool_call_id="read-1")
    recent = Message("assistant", "Now fix parser", reasoning_content="PRIVATE_RECENT_REASONING")
    messages = [Message("user", "Fix parser"), old, tool, recent]
    result = ContextBuilder(offload_dir=tmp_path).compact_history(
        messages, history_budget=200, keep_recent_groups=1,
    )

    assert result.summarized
    assert result.messages[-1] == recent
    summary = next(message for message in result.messages if message.name == "history_summary_l2")
    assert summary.reasoning_content is None
    assert "PRIVATE_" not in summary.content
    archived = ContextBuilder(offload_dir=tmp_path).resolve(result.offloaded[-1])
    assert archived == [old, tool]
    assert archived[0].reasoning_content == old.reasoning_content


def test_automatic_offload_counts_and_restores_reasoning(tmp_path):
    old = Message("assistant", "inspect", reasoning_content="private " * 200)
    recent = Message("assistant", "continue", reasoning_content="next")
    builder = ContextBuilder(
        max_tokens=100, reserve_output=10, offload_dir=tmp_path, keep_recent_groups=1,
    )

    result = builder.build([Message("user", "Fix parser"), old, recent])

    assert result.offloaded
    assert result.messages[-1] == recent
    assert ContextBuilder(offload_dir=tmp_path).resolve(result.offloaded[0]) == [old]


def test_invalid_checkpoint_reasoning_does_not_replace_valid_history(tmp_path):
    session = SessionStore.new(tmp_path, "reasoning-model")
    user = Message("user", "Fix parser")
    assistant = Message("assistant", "Read parser", reasoning_content="inspect before editing")
    session.append_message(user)
    session.append_message(assistant)
    invalid = asdict(assistant)
    invalid["reasoning_content"] = {"unexpected": "object"}
    session.append_event("context_checkpoint", version=1, source_message_count=2, messages=[invalid])

    assert session.load_context() == [user, assistant]
