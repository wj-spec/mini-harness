"""BudgetLedger P0 测试（spec: docs/specs/budget-ledger.md）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.runtime.budget import BudgetError, BudgetLedger


def _ledger(tmp_path):
    return BudgetLedger(tmp_path / "session")


def test_append_and_totals(tmp_path):
    ledger = _ledger(tmp_path)
    base = dict(task_id="t-1", goal_id="g-1", parent_id=None, node_kind="goal", segment=1, run_id="r1")
    ledger.on_trace(**base, event={"type": "llm_end", "prompt_tokens": 100, "completion_tokens": 20,
                                  "cost_usd": 0.01, "usage_estimated": False})
    ledger.on_trace(**base, event={"type": "tool_end", "name": "bash"})
    ledger.on_trace(**base, event={"type": "llm_end", "prompt_tokens": 10, "completion_tokens": 5,
                                  "cost_usd": 0.002, "usage_estimated": True})
    totals = ledger.totals(task_id="t-1")
    assert totals["tokens_in"] == 110 and totals["tokens_out"] == 25
    assert totals["tokens_total"] == 135
    assert totals["tool_calls"] == 1
    assert totals["cost_usd"] == pytest.approx(0.012)


def test_corrupt_line_is_skipped(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.append({"task_id": "t-1", "entry_kind": "llm", "tokens_in": 1, "tokens_out": 0})
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write("{bad\n")
    assert len(ledger.entries()) == 1


def test_close_segment_and_task(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.close_segment(task_id="t-1", goal_id=None, parent_id=None, node_kind="goal",
                        segment=1, run_id="r1", status="done", verified=True)
    ledger.close_task(task_id="t-1", goal_id=None, parent_id=None, node_kind="goal",
                     status="completed", reason=None)
    assert ledger.task_terminal("t-1") == "completed"
    with pytest.raises(BudgetError):
        ledger.close_task(task_id="t-1", goal_id=None, parent_id=None, node_kind="goal",
                         status="paused")


def test_task_runtime_hooks_ledger(tmp_path):
    from app.runtime.task_runtime import TaskRuntime
    from app.runtime.tasks import TaskStore

    runtime = TaskRuntime(TaskStore(tmp_path / "session"), workspace=tmp_path)
    run = runtime.prepare_run("run-1", {"kind": "goal", "objective": "预算测试"})
    runtime.record_trace(run, {"type": "llm_end", "prompt_tokens": 30, "completion_tokens": 10,
                               "cost_usd": 0.004, "usage_estimated": False})
    runtime.finish_run(run, SimpleNamespace(status="done", verified=True, stop_reason=""))

    budget = runtime.budget
    totals = budget.totals(task_id=run.task_id)
    assert totals["tokens_total"] == 40
    assert totals["cost_usd"] == pytest.approx(0.004)
    assert budget.task_terminal(run.task_id) == "completed"
    kinds = [entry["entry_kind"] for entry in budget.entries(task_id=run.task_id)]
    assert kinds.count("segment_close") == 1
    assert kinds.count("task_close") == 1
