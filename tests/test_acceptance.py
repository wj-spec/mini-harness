from __future__ import annotations

import pytest

from app.runtime.acceptance import AcceptanceError, PlanAcceptance


def _data(**overrides):
    data = {
        "schema_version": 1, "task_id": "t-20261106-000000-abcdef",
        "plan_id": "pl-1", "plan_version": 1,
        "acceptance_criteria": [
            {"id": "ac1", "text": "测试通过", "required": True, "confirmed": True,
             "source": "user+model_proposed"},
        ],
        "verification_plan": [
            {"id": "v1", "covers": ["ac1"], "command": "pytest", "expect": {"exit": 0}},
        ],
        "trusted_source": "project_profile",
    }
    data.update(overrides)
    return data


def test_acceptance_true_only_with_confirmed_required_and_ok_results():
    acceptance = PlanAcceptance.from_dict(_data())
    assert acceptance.status({"v1": {"ok": True}}) == (True, [])
    assert acceptance.status({"v1": {"ok": False}})[0] is False


def test_unconfirmed_and_no_criteria_yield_null():
    unconfirmed = PlanAcceptance.from_dict(_data(acceptance_criteria=[
        {"id": "ac1", "text": "测试通过", "confirmed": False},
    ]))
    assert unconfirmed.status()[0] is None
    assert unconfirmed.status()[1] == ["no_confirmed_criteria"]


def test_uncovered_and_manual_criteria():
    uncovered = PlanAcceptance.from_dict(_data(verification_plan=[]))
    status, codes = uncovered.status()
    assert status is False and codes[0] == "acceptance_uncovered"
    manual = PlanAcceptance.from_dict(_data(acceptance_criteria=[
        {"id": "ac1", "text": "人工确认", "confirmed": True, "manual": True},
    ], verification_plan=[]))
    status, codes = manual.status()
    assert status is None and "manual_pending" in codes


def test_untrusted_source_and_invalid_schema():
    data = _data(trusted_source="model_generated")
    with pytest.raises(AcceptanceError):
        PlanAcceptance.from_dict(data)
    with pytest.raises(AcceptanceError):
        PlanAcceptance.from_dict(_data(schema_version=2))
    with pytest.raises(AcceptanceError):
        PlanAcceptance.from_dict(_data(verification_plan=[
            {"id": "v1", "covers": [], "command": "pytest"},
        ]))
