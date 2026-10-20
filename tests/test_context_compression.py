import re

from core.context import ContextBuilder, total_tokens
from core.models import Message, ToolCall
from core.summarize import summarize_messages


def _history(count=20):
    return [Message("system", "system"), Message("user", "original goal")] + [
        Message("assistant", f"step {index}: " + "detail " * 80)
        for index in range(count)
    ]


def _refs(messages):
    return re.findall(r"\[offloaded: ([^\]]+)\]", "\n".join(m.content for m in messages))


def test_explicit_compaction_builds_layers_before_history_cap(tmp_path):
    messages = _history()
    calls = []

    def summarize(batch):
        calls.append(batch)
        return "summary " + ", ".join(m.content[:20] for m in batch)

    builder = ContextBuilder(offload_dir=tmp_path, summarizer=summarize)
    result = builder.compact_history(messages, history_budget=20_000)

    assert result.tokens < total_tokens(messages)
    assert result.messages[:2] == messages[:2]
    assert result.messages[-2:] == messages[-2:]
    summary = result.messages[2]
    assert summary.name == "history_summary_l2"
    assert len(calls) >= 3
    assert all(m.name == "history_summary_l1" for m in calls[-1])
    assert any("step 0" in m.content for batch in calls[:-1] for m in batch)
    assert result.summarized
    assert result.sections["history_before"] > result.sections["history_after"]
    assert result.sections["recent_groups"] == 2
    assert result.sections["summary_layers"] == 2
    assert result.caps["history"] == 20_000


def test_compaction_slides_recent_window_without_splitting_tools(tmp_path):
    messages = _history(3) + [
        Message("assistant", "inspect", [ToolCall("read", {"path": "x"}, "call-x")]),
        Message("tool", "large output " * 500, name="read", tool_call_id="call-x"),
        Message("assistant", "latest result"),
    ]
    builder = ContextBuilder(offload_dir=tmp_path, keep_recent_groups=3)
    result = builder.compact_history(messages, history_budget=160)

    assert result.sections["history"] <= 160
    assert result.messages[-1] == messages[-1]
    assert result.sections["recent_groups"] == 1
    assert not any(m.role == "tool" for m in result.messages)
    archived = ContextBuilder(offload_dir=tmp_path).resolve(result.offloaded[-1])
    assert messages[-3] in archived and messages[-2] in archived


def test_compaction_persists_original_messages_and_protocol_ids(tmp_path):
    call = Message(
        "assistant", "reading", [ToolCall("read", {"path": "a.py"}, "provider-call-42")],
        usage={"input_tokens": 12}, finish_reason="tool_calls",
    )
    tool = Message("tool", "source " * 100, name="read", tool_call_id="provider-call-42")
    messages = [Message("system", "sys"), Message("user", "goal"), call, tool,
                Message("assistant", "recent")]
    result = ContextBuilder(offload_dir=tmp_path).compact_history(
        messages, history_budget=200, keep_recent_groups=1,
    )

    archived = ContextBuilder(offload_dir=tmp_path).resolve(result.offloaded[-1])
    assert archived == [call, tool]


def test_repeated_compaction_reuses_summary_and_resolves_older_archives(tmp_path):
    calls = []

    def summarize(batch):
        calls.append(batch)
        return summarize_messages(batch)

    builder = ContextBuilder(offload_dir=tmp_path, summarizer=summarize)
    first = builder.compact_history(_history(15), history_budget=600)
    first_calls = len(calls)
    repeated = builder.compact_history(first.messages, history_budget=600)
    assert repeated.messages == first.messages
    assert len(calls) == first_calls

    second_input = first.messages + [Message("assistant", "a new result")]
    second = builder.compact_history(second_input, history_budget=600)
    assert len(_refs(second.messages)) == 1
    assert calls[first_calls][0] == first.messages[-2]
    assert not any(m.name == "history_summary_l2" for m in calls[first_calls])
    assert any(m.name == "history_summary_l2" for m in calls[-1])
    archived = ContextBuilder(offload_dir=tmp_path).resolve(second.offloaded[-1])
    assert archived == _history(15)[2:-1]
    assert second.sections["summary_layers"] == 2


def test_compaction_summarizer_failure_uses_deterministic_fallback(tmp_path):
    def fail(batch):
        raise RuntimeError("unavailable")

    result = ContextBuilder(offload_dir=tmp_path, summarizer=fail).compact_history(
        _history(), history_budget=600,
    )
    assert result.summarized
    assert any("summary_failed:RuntimeError" in note for note in result.notes)
    assert "summary_fallback:deterministic" in result.notes
    assert result.sections["history"] <= 600
    assert _refs(result.messages)


def test_compaction_can_override_external_summarizer(tmp_path):
    def external(batch):
        raise AssertionError("external summarizer must not be called")

    result = ContextBuilder(offload_dir=tmp_path, summarizer=external).compact_history(
        _history(), history_budget=600, summarizer=summarize_messages,
    )
    assert result.summarized
    assert not any("summary_failed" in note for note in result.notes)


def test_compaction_offload_failure_retains_visible_originals(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("blocked")
    messages = _history()
    result = ContextBuilder(offload_dir=blocked).compact_history(messages, history_budget=100)
    assert result.messages == messages
    assert result.over_budget
    assert not result.summarized
    assert any("offload_failed" in note for note in result.notes)


def test_compaction_without_persistent_storage_retains_originals():
    messages = _history()
    result = ContextBuilder().compact_history(messages, history_budget=100)
    assert result.messages == messages
    assert "offload_unavailable" in result.notes
    assert result.over_budget


def test_compaction_caps_even_an_oversized_summary(tmp_path):
    builder = ContextBuilder(offload_dir=tmp_path, summarizer=lambda batch: "huge " * 50_000)
    result = builder.compact_history(_history(), history_budget=150, keep_recent_groups=0)
    assert result.sections["history"] <= 150
    assert not result.over_budget
    assert _refs(result.messages)
    assert "summary_truncated" in result.notes


def test_compaction_keeps_build_offload_references(tmp_path):
    builder = ContextBuilder(
        max_tokens=100, reserve_output=10, offload_dir=tmp_path, keep_recent_groups=1,
    )
    messages = _history(4) + [Message("assistant", "latest")]
    prepared = builder.build(messages)
    refs = _refs(prepared.messages)
    assert refs
    result = builder.compact_history(prepared.messages, history_budget=200, keep_recent_groups=0)
    archive = ContextBuilder(offload_dir=tmp_path).resolve(result.offloaded[-1])
    assert set(refs).issubset(set(_refs(archive)))
    for ref in refs:
        assert ContextBuilder(offload_dir=tmp_path).resolve(ref)


def test_compaction_always_pins_original_goal_and_reports_impossible_budget(tmp_path):
    messages = [Message("system", "system"), Message("user", "goal " * 100)]
    builder = ContextBuilder(offload_dir=tmp_path, pin_first_user=False)
    result = builder.compact_history(messages, history_budget=10)
    assert result.messages == messages
    assert result.over_budget
    assert "pinned_over_budget" in result.notes


def test_compaction_empty_history(tmp_path):
    result = ContextBuilder(offload_dir=tmp_path).compact_history([], history_budget=100)
    assert result.messages == []
    assert result.tokens == 0
    assert not result.summarized
    assert not result.over_budget


def test_compaction_keeps_latest_user_correction_in_chronological_order(tmp_path):
    correction = Message("user", "Important correction: only change the parser.")
    pair = [
        Message("assistant", "inspect", [ToolCall("read", {}, "read-latest")]),
        Message("tool", "large output " * 800, name="read", tool_call_id="read-latest"),
    ]
    final = Message("assistant", "latest progress")
    messages = _history(4) + [correction] + pair + [final]
    result = ContextBuilder(offload_dir=tmp_path).compact_history(
        messages, history_budget=200, keep_recent_groups=1,
    )
    assert correction in result.messages
    assert final in result.messages
    assert result.messages.index(correction) < result.messages.index(final)
    assert result.messages[1] == messages[1]
    assert result.sections["history"] <= 200


def test_latest_user_is_not_truncated_even_when_pins_exceed_budget(tmp_path):
    correction = Message("user", "correction " * 100)
    messages = _history(4) + [correction, Message("assistant", "progress")]
    result = ContextBuilder(offload_dir=tmp_path).compact_history(messages, history_budget=20)
    assert result.messages == messages
    assert result.over_budget
    assert "pinned_over_budget" in result.notes


def test_deterministic_layers_keep_facts_at_both_ends_without_nested_prefixes(tmp_path):
    messages = _history(24)
    result = ContextBuilder(offload_dir=tmp_path).compact_history(messages, history_budget=600)
    summary = next(m for m in result.messages if m.name == "history_summary_l2")
    assert "step 0:" in summary.content
    assert "step 21:" in summary.content
    assert "- assistant: - assistant:" not in summary.content
