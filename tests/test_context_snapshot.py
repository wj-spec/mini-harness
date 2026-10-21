import json
from collections import defaultdict

import pytest

from core.budget import BudgetPolicy
from core.context import estimate_tokens, total_tokens
from core.context_snapshot import build_context_snapshot
from core.models import Message, ToolCall


def assert_balanced(snapshot, messages, specs):
    sections = defaultdict(int)
    for block in snapshot["blocks"]:
        sections[block["section"]] += block["tokens"]
        assert block["tokens"] >= 0
    assert {key: sections[key] for key in snapshot["sections"]} == snapshot["sections"]
    assert sum(snapshot["sections"].values()) == snapshot["total_tokens"]
    assert snapshot["total_tokens"] == total_tokens(messages) + estimate_tokens(
        json.dumps(specs, ensure_ascii=False)
    )


def test_snapshot_has_hand_counted_mutually_exclusive_sections():
    messages = [Message("system", "中文abcd"), Message("user", "12345678"), Message("assistant", "ok")]
    snapshot = build_context_snapshot(messages, [])

    assert snapshot["schema_version"] == 1
    assert snapshot["estimated"] is True
    assert snapshot["count_method"] == "heuristic_cjk_v1"
    assert snapshot["sections"] == {"system": 3, "memory": 0, "tools": 1, "history": 3}
    assert snapshot["total_tokens"] == 7
    assert snapshot["context_window"] == 800
    assert snapshot["input_capacity"] == 600
    assert snapshot["reserves"] == {"output": 200, "scratch": 0}
    assert snapshot["remaining_tokens"] == 593
    assert snapshot["over_budget"] is False
    assert_balanced(snapshot, messages, [])


def test_tool_definitions_and_tool_results_use_different_sections():
    messages = [
        Message("assistant", "", [ToolCall("run", {"cmd": "pwd"}, id="call-1")]),
        Message("tool", "目录abcd", name="run", tool_call_id="call-1"),
    ]
    specs = [{"name": "run"}]
    snapshot = build_context_snapshot(messages, specs)

    assert snapshot["sections"] == {"system": 0, "memory": 0, "tools": 4, "history": 8}
    blocks = {block["kind"]: block for block in snapshot["blocks"]}
    assert blocks["tool_definition"]["source"] == "run"
    assert blocks["tool_definition"]["section"] == "tools"
    assert blocks["tool_result"]["source"] == "call-1"
    assert blocks["tool_result"]["section"] == "history"
    assert blocks["tool_call"]["tokens"] == 4
    assert blocks["assistant"]["tokens"] == 1
    assert_balanced(snapshot, messages, specs)


def test_tool_result_source_resolves_file_path_without_recording_other_arguments():
    messages = [
        Message("assistant", "", [ToolCall("read_file", {"path": "core/context.py", "private": "SECRET_ARG"}, id="call-1")]),
        Message("tool", "SECRET_FILE_BODY", name="read_file", tool_call_id="call-1"),
    ]
    snapshot = build_context_snapshot(messages, [])
    result = next(block for block in snapshot["blocks"] if block["kind"] == "tool_result")

    assert result["source"] == "core/context.py"
    assert "SECRET_ARG" not in json.dumps(snapshot)
    assert "SECRET_FILE_BODY" not in json.dumps(snapshot)
    assert_balanced(snapshot, messages, [])


def test_summary_and_offload_references_stay_in_history():
    messages = [
        Message("assistant", "[history summary L2]\n已读文件\n[offloaded: off-123]", name="history_summary_l2"),
        Message("tool", "[offloaded: off-456]", name="read_file"),
        Message("assistant", "[summary] 旧摘要"),
    ]
    snapshot = build_context_snapshot(messages, [])

    assert [block["kind"] for block in snapshot["blocks"] if block["section"] == "history"] == [
        "history_summary", "offload_reference", "history_summary",
    ]
    assert [block["source"] for block in snapshot["blocks"][:2]] == ["off-123", "off-456"]
    assert set(snapshot["sections"]) == {"system", "memory", "tools", "history"}
    assert_balanced(snapshot, messages, [])


def test_policy_keeps_nominal_and_borrowed_history_capacity():
    policy = BudgetPolicy(
        context_window=1000, output_cap=100, system_cap=100, memory_cap=200,
        tools_cap=100, scratch_cap=50,
    )
    snapshot = build_context_snapshot([Message("system", "abcd")], [], policy=policy)

    assert snapshot["context_window"] == 1000
    assert snapshot["input_capacity"] == 850
    assert snapshot["caps"]["history_nominal"] == 450
    assert snapshot["caps"]["history"] == 848
    assert snapshot["caps"]["prompt"] == 900
    assert snapshot["reserves"] == {"output": 100, "scratch": 50}
    assert snapshot["remaining_tokens"] == 848


def test_policy_explicit_history_cap_and_total_over_budget():
    policy = BudgetPolicy(
        context_window=100, output_cap=20, system_cap=1, memory_cap=1,
        tools_cap=1, history_cap=30, scratch_cap=10,
    )
    snapshot = build_context_snapshot([Message("user", "中" * 71)], [], policy=policy)

    assert snapshot["caps"]["history"] == 30
    assert snapshot["caps"]["history_nominal"] == 30
    assert snapshot["input_capacity"] == 70
    assert snapshot["over_budget"] is True
    assert snapshot["remaining_tokens"] == 0


def test_exceeding_nominal_partition_does_not_mean_input_over_budget():
    policy = BudgetPolicy(
        context_window=100, output_cap=10, system_cap=1, memory_cap=0,
        tools_cap=1, scratch_cap=0,
    )
    snapshot = build_context_snapshot([Message("system", "中文")], [], policy=policy)

    assert snapshot["sections"]["system"] > snapshot["caps"]["system"]
    assert snapshot["over_budget"] is False


def test_matching_system_sources_split_with_exact_incremental_accounting():
    parts = [
        {"kind": "base_instructions", "label": "基础", "source": "base", "content": "a"},
        {"kind": "project_instructions", "label": "规则", "source": "AGENTS.md", "content": "b"},
        {"kind": "skills", "label": "技能", "source": "skills", "content": "中文cd"},
    ]
    messages = [Message("system", "ab中文cd"), Message("system", "进度", name="task_state")]
    snapshot = build_context_snapshot(messages, [], system_parts=parts)
    blocks = [block for block in snapshot["blocks"] if block["section"] == "system"]

    assert [block["kind"] for block in blocks] == ["base_instructions", "project_instructions", "skills", "task_state"]
    assert [block["tokens"] for block in blocks] == [1, 0, 2, 2]
    assert [block["chars"] for block in blocks] == [1, 1, 4, 2]
    assert blocks[1]["source"] == "AGENTS.md"
    assert_balanced(snapshot, messages, [])


@pytest.mark.parametrize("parts", [
    [{"kind": "base_instructions", "label": "基础", "source": "base", "content": "wrong"}],
    [],
    [{"kind": "base_instructions", "label": "基础", "source": "base"}],
])
def test_mismatched_system_sources_fall_back_to_whole_message(parts):
    messages = [Message("system", "actual")]
    snapshot = build_context_snapshot(messages, [], system_parts=parts)

    blocks = [block for block in snapshot["blocks"] if block["section"] == "system"]
    assert len(blocks) == 1
    assert blocks[0]["kind"] == "system"
    assert_balanced(snapshot, messages, [])


def test_tool_list_serialization_overhead_is_attributed_without_double_counting():
    specs = [{"name": "a", "description": "中"}, {"name": "b"}, {"function": {"name": "c"}}]
    snapshot = build_context_snapshot([], specs)

    blocks = [block for block in snapshot["blocks"] if block["section"] == "tools"]
    assert [block["source"] for block in blocks] == ["a", "b", "c"]
    assert len(blocks) == 3
    assert_balanced(snapshot, [], specs)


def test_empty_tools_have_explicit_envelope_block():
    snapshot = build_context_snapshot([], [])

    assert snapshot["sections"] == {"system": 0, "memory": 0, "tools": 1, "history": 0}
    assert snapshot["blocks"][0]["kind"] == "tools_envelope"
    assert snapshot["blocks"][0]["tokens"] == 1


def test_block_identity_survives_unrelated_message_and_duplicate_addition():
    original = [Message("user", "same"), Message("user", "same"), Message("assistant", "answer")]
    updated = [Message("system", "new"), *original, Message("user", "same")]
    before = build_context_snapshot(original, [])
    after = build_context_snapshot(updated, [])
    old_ids = [block["id"] for block in before["blocks"] if block["section"] == "history"]
    new_ids = [block["id"] for block in after["blocks"] if block["section"] == "history"]

    assert len(set(old_ids)) == len(old_ids)
    assert new_ids[:len(old_ids)] == old_ids
    assert len(set(new_ids)) == len(new_ids)


def test_snapshot_does_not_store_content_or_previews():
    secret = "SECRET_PAYLOAD_0123456789"
    messages = [Message("system", secret), Message("user", secret), Message("assistant", "", [ToolCall("run", {"secret": secret})])]
    specs = [{"name": "run", "description": secret}]
    snapshot = build_context_snapshot(messages, specs)

    assert secret not in json.dumps(snapshot)
    assert all(set(block) == {"id", "section", "kind", "label", "source", "tokens", "chars", "content_hash"} for block in snapshot["blocks"])
    assert all(len(block["content_hash"]) == 64 for block in snapshot["blocks"])
    assert_balanced(snapshot, messages, specs)


def test_block_metadata_is_bounded_and_inputs_are_unchanged():
    long_name = "tool-" + "a" * 5000
    messages = [Message("tool", "result", name=long_name)]
    snapshot = build_context_snapshot(messages, [{"name": long_name}])

    assert all(len(value) <= 256 for block in snapshot["blocks"] for value in block.values() if isinstance(value, str))
    assert messages[0].name == long_name


def test_zero_capacity_reports_over_budget_without_negative_remaining():
    snapshot = build_context_snapshot([], [], max_tokens=10, reserve_output=20)

    assert snapshot["input_capacity"] == 0
    assert snapshot["remaining_tokens"] == 0
    assert snapshot["over_budget"] is True
