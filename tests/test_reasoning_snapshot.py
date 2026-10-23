import json

import pytest

from app.context_snapshot_display import snapshot_for_display
from core.context import estimate_tokens, total_tokens
from core.context_snapshot import build_context_snapshot
from core.models import Message


def test_snapshot_counts_reasoning_in_history_and_capacity_without_exposing_it():
    secret = "PRIVATE_REASONING_" + "推理" * 40
    messages = [Message("assistant", "ok", reasoning_content=secret)]
    snapshot = build_context_snapshot(messages, [], max_tokens=100, reserve_output=30)

    assert snapshot["sections"]["history"] == total_tokens(messages)
    assert snapshot["total_tokens"] == total_tokens(messages) + estimate_tokens("[]")
    assert snapshot["over_budget"] is True
    assert snapshot["remaining_tokens"] == 0
    reasoning = next(block for block in snapshot["blocks"] if block["kind"] == "reasoning")
    assert reasoning["section"] == "history"
    assert reasoning["tokens"] == estimate_tokens(secret)
    assert reasoning["chars"] == len(secret)
    assert secret not in json.dumps(snapshot, ensure_ascii=False)
    assert messages[0].content == "ok"
    assert messages[0].reasoning_content == secret


@pytest.mark.parametrize("reasoning", [None, ""])
def test_empty_reasoning_adds_no_snapshot_block_or_token(reasoning):
    message = Message("assistant", "ok", reasoning_content=reasoning)
    snapshot = build_context_snapshot([message], [])

    assert snapshot["total_tokens"] == total_tokens([message]) + estimate_tokens("[]")
    assert not any(block["kind"] == "reasoning" for block in snapshot["blocks"])


def test_snapshot_view_and_bounded_display_keep_reasoning_totals_without_body():
    secret = "PRIVATE_REASONING_" + "x" * 400
    messages = [Message("assistant", "reply", reasoning_content=secret)]
    messages.extend(Message("user", "continue") for _ in range(10))
    snapshot = build_context_snapshot(messages, [])
    snapshot.update(
        type="context_snapshot", snapshot_id="snapshot-1", request_id="request-1",
        run_id="run-1", phase="request", reason="model_call", step=1, attempt=0,
        delta_tokens=snapshot["total_tokens"], section_deltas=snapshot["sections"],
    )
    display = snapshot_for_display(snapshot, max_blocks=4)

    assert display["total_tokens"] == total_tokens(messages) + estimate_tokens("[]")
    assert display["sections"] == snapshot["sections"]
    for section, tokens in display["sections"].items():
        assert sum(block["tokens"] for block in display["blocks"]
                   if block["section"] == section) == tokens
    assert secret not in json.dumps(display)
