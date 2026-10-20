from core.models import Message, ToolCall
from core.budget import BudgetPolicy
from core.context import ContextBuilder, estimate_tokens, stable_prefix_hash


def test_under_budget_unchanged():
    messages = [
        Message("system", "sys"),
        Message("user", "hi"),
        Message("assistant", "hello"),
    ]
    builder = ContextBuilder(max_tokens=100, reserve_output=10)
    result = builder.build(messages)
    assert result.messages == messages
    assert result.tokens <= builder.budget
    assert result.notes == []


def test_pinned_over_budget_is_reported_not_truncated():
    messages = [Message("system", "x" * 200), Message("user", "hi")]
    builder = ContextBuilder(max_tokens=20, reserve_output=5)
    result = builder.build(messages)
    assert result.over_budget is True
    assert result.messages == messages
    assert "pinned_over_budget" in result.notes


def test_tool_group_offloaded_and_resolvable():
    messages = [
        Message("system", "sys"),
        Message("user", "q"),
        Message("assistant", "", [ToolCall("add", {"a": 1, "b": 2})]),
        Message("tool", "3" * 200, name="add"),
        Message("assistant", "done"),
    ]
    builder = ContextBuilder(max_tokens=30, reserve_output=5, keep_recent_groups=1)
    result = builder.build(messages)

    assert result.offloaded, "tool group should be offloaded"
    ref = result.offloaded[0]
    original = builder.resolve(ref)
    assert [m.role for m in original] == ["assistant", "tool"]
    assert original[1].content == "3" * 200
    assert any("[offloaded:" in m.content for m in result.messages)
    assert any("[offloaded:" in note or "offloaded:tool" in note for note in result.notes)


def test_offload_persists_to_disk(tmp_path):
    messages = [
        Message("system", "sys"),
        Message("user", "q"),
        Message("assistant", "", [ToolCall("add", {"a": 1, "b": 2})]),
        Message("tool", "3" * 200, name="add"),
        Message("assistant", "done"),
    ]
    builder = ContextBuilder(
        max_tokens=30, reserve_output=5, keep_recent_groups=1, offload_dir=tmp_path
    )
    result = builder.build(messages)
    ref = result.offloaded[0]

    fresh = ContextBuilder(offload_dir=tmp_path)
    original = fresh.resolve(ref)
    assert original[1].content == "3" * 200


def test_recent_groups_are_kept():
    messages = [
        Message("system", "sys"),
        Message("user", "u" * 40),
        Message("assistant", "old" * 40),
        Message("user", "u2" * 40),
        Message("assistant", "recent"),
    ]
    builder = ContextBuilder(max_tokens=30, reserve_output=10, keep_recent_groups=1)
    result = builder.build(messages)
    assert result.messages[-1].content == "recent"


def test_summary_when_offload_is_not_enough():
    messages = [Message("system", "sys")]
    messages += [Message("user", f"u{i}" + "x" * 40) for i in range(20)]
    messages.append(Message("assistant", "recent"))

    builder = ContextBuilder(
        max_tokens=40,
        reserve_output=10,
        keep_recent_groups=1,
        summarizer=lambda msgs: "S",
    )
    result = builder.build(messages)
    assert result.summarized is True
    assert any("[summary]" in m.content for m in result.messages)


def test_summarizer_failure_falls_back():
    messages = [Message("system", "sys")]
    messages += [Message("user", f"u{i}" + "x" * 40) for i in range(20)]
    messages.append(Message("assistant", "recent"))

    def boom(msgs):
        raise ValueError("no summary")

    builder = ContextBuilder(
        max_tokens=40,
        reserve_output=10,
        keep_recent_groups=1,
        summarizer=boom,
    )
    result = builder.build(messages)
    assert result.summarized is False
    assert sum(1 for note in result.notes if "summary_failed:ValueError" in note) == 1


def test_stable_prefix_hash_ignores_history():
    a = [Message("system", "sys"), Message("user", "a")]
    b = [Message("system", "sys"), Message("user", "b" * 100)]
    assert stable_prefix_hash(a) == stable_prefix_hash(b)
    assert stable_prefix_hash(a) != stable_prefix_hash([Message("system", "other")])


def test_empty_messages():
    builder = ContextBuilder(max_tokens=100, reserve_output=10)
    result = builder.build([])
    assert result.messages == []
    assert result.tokens == 0


def test_zero_budget_keeps_pinned_and_reports():
    messages = [Message("system", "sys"), Message("user", "hi")]
    builder = ContextBuilder(max_tokens=10, reserve_output=10)
    result = builder.build(messages)
    assert result.over_budget is True
    assert result.messages == messages


def test_estimate_tokens_counts_cjk():
    assert estimate_tokens("你好世界") == 4
    assert estimate_tokens("hello") == 1
    assert estimate_tokens("hi 你好") == 2


def test_sectioned_budget_counts_tools_and_memory():
    policy = BudgetPolicy(
        context_window=1000, output_cap=100, system_cap=100, memory_cap=200,
        tools_cap=300, history_cap=400, scratch_cap=10,
    )
    builder = ContextBuilder(policy=policy, keep_recent_groups=1)
    messages = [
        Message("system", "sys"),
        Message("user", "task"),
        Message("assistant", "old" * 50),
    ]
    result = builder.build(messages, tool_tokens=50, memory_tokens=20)

    assert result.sections["system"] == 1
    assert result.sections["memory"] == 20
    assert result.sections["tools"] == 50
    assert result.tokens == sum(result.sections.values())
    assert result.caps["history"] == 400
    assert result.over_budget is False


def test_sectioned_system_over_cap_is_reported():
    policy = BudgetPolicy(
        context_window=1000, output_cap=100, system_cap=10, memory_cap=10,
        tools_cap=10, history_cap=100, scratch_cap=5,
    )
    builder = ContextBuilder(policy=policy)
    result = builder.build([Message("system", "x" * 100), Message("user", "hi")])
    assert result.over_budget is True
    assert "system_over_cap" in result.notes


def test_sectioned_history_is_capped():
    policy = BudgetPolicy(
        context_window=200, output_cap=50, system_cap=100, memory_cap=100,
        tools_cap=100, history_cap=20, scratch_cap=5,
    )
    builder = ContextBuilder(policy=policy, keep_recent_groups=0)
    messages = [Message("system", "sys"), Message("user", "task")]
    messages += [Message("assistant", "x" * 100) for _ in range(5)]
    result = builder.build(messages)
    assert result.sections["history"] <= 20


def test_display_budget_uses_policy():
    policy = BudgetPolicy(context_window=1000, output_cap=100)
    assert ContextBuilder(policy=policy).display_budget == 900
    assert ContextBuilder(max_tokens=100, reserve_output=10).display_budget == 90
