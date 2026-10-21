from copy import deepcopy

import pytest

from app.context_view import ContextHistory, context_detail, context_summary


def snapshot(index=1, run_id="run-a", **changes):
    event = {
        "type": "context_snapshot", "schema_version": 1, "run_id": run_id,
        "snapshot_id": f"{run_id}:{index}", "request_id": f"request-{index}",
        "step": index, "attempt": 0, "phase": "request", "reason": "model_call",
        "estimated": True, "count_method": "chars_v1", "model": "fake",
        "context_window": 1000, "input_capacity": 900, "total_tokens": 250,
        "sections": {"system": 100, "memory": 0, "tools": 50, "history": 100},
        "caps": {"system": 100, "memory": 100, "tools": 100,
                 "history": 650, "history_nominal": 600},
        "reserves": {"output": 80, "scratch": 20}, "remaining_tokens": 650,
        "over_budget": False, "delta_tokens": 50,
        "section_deltas": {"history": 50},
        "blocks": [{"id": "system-1", "section": "system", "kind": "system",
                    "label": "instructions", "source": "system", "tokens": 100,
                    "chars": 400, "content_hash": "abc"},
                   {"id": "definition-1", "section": "tools", "kind": "tool_definition",
                    "label": "read_file", "source": "read_file", "tokens": 50,
                    "chars": 200, "content_hash": "abc"},
                   {"id": "tool-1", "section": "history", "kind": "tool_result",
                    "label": "read_file", "source": "src/[red]file.py",
                    "tokens": 100, "chars": 400, "content_hash": "abc"}],
    }
    return {**event, **changes}


def test_context_history_bounds_runs_and_deduplicates_snapshots():
    history = ContextHistory()
    history.accept({"type": "run_start", "run_id": "run-a"})
    for index in range(105):
        history.accept(snapshot(index))
    assert len(history.snapshots) == 100
    assert history.snapshots[0]["step"] == 5
    assert not history.accept(snapshot(104))
    history.accept({"type": "run_start", "run_id": "run-b"})
    assert history.snapshots == []
    assert history.accept(snapshot(1, "run-b"))
    assert history.run_id == "run-b"
    assert not history.accept(snapshot(2, "run-a"))


def test_context_detail_explains_partition_caps_reserves_and_compaction():
    snapshots = [snapshot(), snapshot(2, phase="after_compaction", delta_tokens=-100)]
    text = context_detail(snapshots, 1, width=48).plain
    assert "估算" in text and "2/2" in text
    assert "250 / 900" in text and "27.8%" in text
    assert "history" in text and "名义 600" in text and "有效 650" in text
    assert "输出预留 80" in text and "安全余量 20" in text
    assert "压缩后" in text and "-100" in text
    assert "read_file" in text and "src/[red]file.py" in text
    assert "chars_v1" in text and "request-2" in text
    assert "正文" not in text


def test_context_render_handles_empty_zero_capacity_and_overflow():
    assert "暂无" in context_detail([], 0, width=30).plain
    event = snapshot(input_capacity=0, remaining_tokens=0, over_budget=True)
    detail = context_detail([event], 0, width=30).plain
    assert "超额" in detail and "容量为 0" in detail
    assert "inf" not in detail and "nan" not in detail
    assert "估算" in context_summary(event)
    assert "S 100" in context_summary(event)
    assert "M 0" in context_summary(event)
    assert "T 50" in context_summary(event)
    assert "H 100" in context_summary(event)


def test_context_summary_keeps_four_partitions_readable_in_narrow_terminal():
    event = snapshot(total_tokens=999999, input_capacity=960000,
                     sections={"system": 10000, "memory": 0, "tools": 25000, "history": 964999})
    summary = context_summary(event, width=34)
    assert "估算" in summary
    assert all(marker in summary for marker in ("S ", "M ", "T ", "H "))
    from rich.cells import cell_len
    assert all(cell_len(line) <= 34 for line in summary.splitlines())


@pytest.mark.parametrize("change", [
    lambda event: event.pop("caps"),
    lambda event: event.update(total_tokens=True),
    lambda event: event["sections"].update(history=99),
    lambda event: event["blocks"][0].update(tokens=-1),
    lambda event: event["blocks"][0].update(tokens=99),
    lambda event: event["blocks"][0].pop("source"),
    lambda event: event["reserves"].update(output="80"),
    lambda event: event.update(blocks=["broken"]),
    lambda event: event.update(attempt=False),
    lambda event: event.update(phase=[]),
])
def test_context_history_rejects_malformed_trace_snapshots(change):
    event = deepcopy(snapshot())
    change(event)
    history = ContextHistory()
    assert not history.accept(event)
    assert history.snapshots == []


def test_context_history_bounds_display_blocks_without_losing_usage():
    event = snapshot(
        total_tokens=5000, sections={"system": 0, "memory": 0, "tools": 0, "history": 5000},
        blocks=[{"id": f"block-{i}", "section": "history", "kind": "user", "label": "user",
                 "source": f"message-{i}", "tokens": 1, "chars": 4, "content_hash": "abc"}
                for i in range(5000)],
    )
    history = ContextHistory()
    assert history.accept(event)
    saved = history.snapshots[0]
    assert len(saved["blocks"]) <= 256
    assert sum(block["tokens"] for block in saved["blocks"]) == saved["total_tokens"] == 5000
    assert any(block["kind"] == "aggregate" for block in saved["blocks"])
    assert len(event["blocks"]) == 5000


def test_context_detail_displays_escape_sequences_in_sources_as_text():
    event = snapshot()
    event["blocks"][0]["source"] = "src/\x1b[31mfile.py"
    event["reason"] = "\x1b[2Jreason"
    text = context_detail([event], 0).plain
    assert "\x1b" not in text
    assert "file.py" in text and "reason" in text
