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
