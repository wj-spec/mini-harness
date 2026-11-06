from __future__ import annotations

import pytest

from app.runtime.plan import Plan
from app.runtime.plan_execution import (
    ExecutionError, StepLedger, load_ledger, save_ledger,
)


def _plan(verification=None):
    return Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-1", "task_id": "t-20261106-000000-abcdef",
        "objective": "执行计划",
        "steps": [
            {"step_id": "s1", "title": "读代码", "kind": "read"},
            {"step_id": "s2", "title": "跑测试", "kind": "verify",
             "depends_on": ["s1"], "verification": {"command": "pytest"}},
        ],
    })


def test_ledger_roundtrip_and_completion():
    plan = _plan()
    ledger = StepLedger.from_plan(plan)
    assert ledger.start_step("s1").status == "in_progress"
    ledger.add_evidence("s1", kind="file_change", ref="app/x.py")
    ledger.complete_step(plan, "s1")
    assert ledger.can_execute_step(plan, "s2")
    assert not ledger.plan_complete(plan)

    ledger.start_step("s2")
    with pytest.raises(ExecutionError):
        ledger.complete_step(plan, "s2")
    ledger.add_evidence("s2", kind="verification", ref="verifications[0]", ok=True)
    ledger.complete_step(plan, "s2")
    assert ledger.plan_complete(plan)


def test_ledger_rejects_unknown_and_invalid_transitions():
    plan = _plan()
    ledger = StepLedger.from_plan(plan)
    with pytest.raises(ExecutionError):
        ledger.start_step("missing")
    with pytest.raises(ExecutionError):
        ledger.complete_step(plan, "s1")
    ledger.start_step("s1")
    with pytest.raises(ExecutionError):
        ledger.complete_step(plan, "s2")
    with pytest.raises(ExecutionError):
        ledger.add_evidence("s1", kind="bogus", ref="x")


def test_deviation_blocks_completion():
    plan = _plan()
    ledger = StepLedger.from_plan(plan)
    ledger.start_step("s1")
    ledger.mark_deviated("s1", ["outside.py"])
    assert not ledger.plan_complete(plan)


def test_ledger_atomic_roundtrip(tmp_path):
    plan = _plan()
    ledger = StepLedger.from_plan(plan)
    ledger.start_step("s1")
    save_ledger(tmp_path, ledger)
    loaded = load_ledger(tmp_path, 1)
    assert loaded.steps["s1"].status == "in_progress"
    assert not (tmp_path / "plan" / "execution.v1.json.tmp").exists()
