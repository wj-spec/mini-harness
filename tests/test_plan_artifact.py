from __future__ import annotations

import json

import pytest

from app.runtime.plan import (
    Plan, PlanError, PlanStep, load_plan, plan_path, render_plan, save_plan,
)


def _step(step_id="s1", **overrides):
    data = {"step_id": step_id, "title": f"步骤 {step_id}", "kind": "read"}
    data.update(overrides)
    return data


def _plan(**overrides):
    data = {
        "schema_version": 1, "plan_id": "pl-1", "task_id": "t-20261106-000000-abcdef",
        "objective": "修复问题", "steps": [_step()],
    }
    data.update(overrides)
    return data


def test_plan_roundtrip_and_hash(tmp_path):
    plan = Plan.from_dict(_plan(acceptance_criteria=[
        {"id": "ac1", "text": "测试通过", "verify": "pytest", "confirmed": False},
    ]))
    path = save_plan(tmp_path, plan)
    loaded = load_plan(path)
    assert loaded.plan_id == "pl-1" and loaded.steps[0].step_id == "s1"
    assert loaded.plan_hash() == plan.plan_hash()
    assert loaded.acceptance_criteria[0]["confirmed"] is False
    assert not (tmp_path / "plan" / "plan.v1.json.tmp").exists()


@pytest.mark.parametrize("change", [
    {"objective": ""},
    {"steps": []},
    {"steps": [_step("s1"), _step("s1")]},
    {"steps": [_step("s1", depends_on=["missing"])]},
    {"steps": [_step("s1", files=["/etc/passwd"])]},
    {"steps": [_step("s1", files=["../outside"])]},
    {"steps": [_step("s1", kind="bogus")]},
    {"steps": [_step("s1", risk="bogus")]},
    {"schema_version": 2},
])
def test_invalid_plans_rejected(change):
    with pytest.raises(PlanError):
        Plan.from_dict(_plan(**change))


def test_dependency_cycle_rejected():
    with pytest.raises(PlanError):
        Plan.from_dict(_plan(steps=[
            _step("s1", depends_on=["s2"]),
            _step("s2", depends_on=["s1"]),
        ]))


def test_unknown_schema_and_render(tmp_path):
    path = tmp_path / "plan.v2.json"
    path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    with pytest.raises(PlanError):
        load_plan(path)
    plan = Plan.from_dict(_plan(
        acceptance_criteria=[{"id": "ac1", "text": "通过", "confirmed": True}],
        steps=[_step("s1"), _step("s2", depends_on=["s1"], kind="edit", risk="write")],
    ))
    text = render_plan(plan)
    assert "计划：修复问题" in text
    assert "[已确认]" in text
    assert "s2" in text and "depends=s1" in text


def test_plan_path_layout(tmp_path):
    assert plan_path(tmp_path, 3) == tmp_path / "plan" / "plan.v3.json"


def _accepted_plan():
    return Plan.from_dict(_plan(
        acceptance_criteria=[{"id": "ac1", "text": "通过", "confirmed": False}],
    ))


def test_plan_approval_gate_and_audit(tmp_path):
    from app.runtime.plan import (
        approve_plan, can_execute, content_hash, propose_plan, reject_plan,
    )

    plan = _accepted_plan()
    assert not can_execute(plan)
    propose_plan(tmp_path, plan)
    assert plan.status == "proposed" and not can_execute(plan)
    approve_plan(tmp_path, plan, approved_by="user", approval_id="a-1")
    assert plan.status == "approved" and can_execute(plan)
    assert plan.approval["plan_hash"] == content_hash(plan)
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["plan_proposed", "plan_approved"]
    assert events[-1]["approver"] == "user"

    rejected = _accepted_plan()
    propose_plan(tmp_path, rejected)
    reject_plan(tmp_path, rejected, rejected_by="user", reason="范围太大")
    assert rejected.status == "draft" and not can_execute(rejected)


def test_plan_approval_requires_capability_and_proposed(tmp_path):
    from app.runtime.plan import approve_plan, propose_plan

    plan = _accepted_plan()
    with pytest.raises(PlanError):
        approve_plan(tmp_path, plan, approved_by="user")
    propose_plan(tmp_path, plan)
    with pytest.raises(PlanError):
        approve_plan(tmp_path, plan, approved_by="user", capability="other")


def test_plan_content_changed_after_approval_is_not_executable(tmp_path):
    from app.runtime.plan import approve_plan, can_execute, propose_plan

    plan = _accepted_plan()
    propose_plan(tmp_path, plan)
    approve_plan(tmp_path, plan, approved_by="user")
    plan.objective = "偷偷扩大范围"
    assert not can_execute(plan)


def test_submit_plan_tool_writes_proposed_plan(tmp_path):
    import asyncio

    from app.tools.registry import ToolRegistry
    from core.contracts import ToolCall

    registry = ToolRegistry(tmp_path)
    task_dir = tmp_path / ".vibe" / "sessions" / "s" / "tasks" / "t-20261106-000000-abcdef"
    task_dir.mkdir(parents=True)
    registry.register_plan_tool(task_dir)
    ok, text = asyncio.run(registry.execute_async(ToolCall("submit_plan", {
        "objective": "计划目标",
        "steps": [{"step_id": "s1", "title": "读代码", "kind": "read"}],
    })))
    assert ok and "plan proposed" in text
    plan_file = task_dir / "plan" / "plan.v1.json"
    assert plan_file.exists()
    plan = load_plan(plan_file)
    assert plan.status == "proposed" and plan.task_id == "t-20261106-000000-abcdef"
    assert "plan_proposed" in (task_dir / "audit.jsonl").read_text(encoding="utf-8")
    registry.unregister("submit_plan")


def test_approval_gate_plan_execution_gate_denies():
    import asyncio
    from app.safety.policy import ApprovalGate
    from core.contracts import ToolCall

    gate = ApprovalGate()
    gate.execution_gate = lambda call: "plan_not_approved"
    decision = asyncio.run(gate.request(ToolCall("write_file", {"path": "x", "content": "x"}), "preview"))
    assert decision.allowed is False and decision.scope == "deny"
    assert gate.decisions_for(ToolCall("bash", {"command": "echo x"})) == ["deny"]


def test_find_plan_and_can_execute(tmp_path):
    from app.runtime.plan import find_plan, propose_plan, approve_plan

    task_dir = tmp_path / "tasks" / "t-20261106-000000-abcdef"
    plan = Plan.from_dict(_plan())
    propose_plan(task_dir, plan)
    found = find_plan(tmp_path, plan.plan_id)
    assert found is not None and found[0] == task_dir
    approve_plan(task_dir, found[1], approved_by="user")
    found = find_plan(tmp_path, plan.plan_id, 1)
    assert found is not None
