from __future__ import annotations

import asyncio

from app.runtime.acceptance import PlanAcceptance
from app.runtime.verification import command_digest, run_verification_plan, trusted_commands
from app.safety.policy import ApprovalDecision, ApprovalGate


def _acceptance(commands):
    return PlanAcceptance.from_dict({
        "schema_version": 1, "task_id": "t-20261106-000000-abcdef",
        "plan_id": "pl-1", "plan_version": 1,
        "acceptance_criteria": [
            {"id": "ac1", "text": "命令成功", "confirmed": True},
        ],
        "verification_plan": commands,
        "trusted_source": "project_profile",
    })


def _allow_gate():
    async def handler(call, preview):
        return ApprovalDecision(True, "once")
    return ApprovalGate(handler=handler)


def test_run_verification_plan_success_and_failure(tmp_path):
    acceptance = _acceptance([
        {"id": "v1", "covers": ["ac1"], "command": "true", "source": "project_profile"},
    ])
    result = asyncio.run(run_verification_plan(acceptance, tmp_path, _allow_gate()))
    assert result["status"] is True
    assert result["results"]["v1"]["ok"] is True
    assert result["results"]["v1"]["digest"]

    failing = _acceptance([
        {"id": "v1", "covers": ["ac1"], "command": "false", "source": "project_profile"},
    ])
    result = asyncio.run(run_verification_plan(failing, tmp_path, _allow_gate()))
    assert result["status"] is False
    assert result["results"]["v1"]["exit_code"] != 0


def test_untrusted_command_not_executed(tmp_path):
    acceptance = _acceptance([
        {"id": "v1", "covers": ["ac1"], "command": "touch should_not_exist", "source": "model"},
    ])
    result = asyncio.run(run_verification_plan(acceptance, tmp_path, _allow_gate()))
    assert result["results"]["v1"]["reason"] == "untrusted_command"
    assert not (tmp_path / "should_not_exist").exists()


def test_approval_denied_blocks_command(tmp_path):
    async def deny(call, preview):
        return ApprovalDecision(False, "deny")
    acceptance = _acceptance([
        {"id": "v1", "covers": ["ac1"], "command": "touch blocked", "source": "project_profile"},
    ])
    result = asyncio.run(run_verification_plan(acceptance, tmp_path, ApprovalGate(handler=deny)))
    assert result["results"]["v1"]["reason"] == "approval_denied"
    assert not (tmp_path / "blocked").exists()


def test_trusted_commands_and_digest():
    acceptance = _acceptance([
        {"id": "v1", "covers": ["ac1"], "command": "true", "source": "project_profile"},
        {"id": "v2", "covers": ["ac1"], "command": "x", "source": "model"},
    ])
    assert [item.id for item in trusted_commands(acceptance)] == ["v1"]
    assert command_digest("true", 0, "x") == command_digest("true", 0, "x")


def test_verification_retries_flaky_command(tmp_path):
    async def allow(call, preview):
        return ApprovalDecision(True, "once")

    acceptance = _acceptance([
        {"id": "v1", "covers": ["ac1"], "source": "project_profile",
         "command": "test -f marker || { touch marker; exit 1; }"},
    ])
    result = asyncio.run(run_verification_plan(
        acceptance, tmp_path, ApprovalGate(handler=allow), retries=1,
    ))
    assert result["status"] is True
    assert result["results"]["v1"]["attempts"] == 2
    assert (tmp_path / "marker").exists()


def test_acceptance_result_persists(tmp_path):
    from app.runtime.acceptance import load_acceptance_result, save_acceptance_result

    result = {"results": {"v1": {"ok": True}}, "status": True, "codes": []}
    path = save_acceptance_result(tmp_path, 2, result)
    assert path.name == "acceptance.v2.json"
    assert load_acceptance_result(tmp_path, 2) == {"schema_version": 1, "plan_version": 2, **result}
