import asyncio

import pytest

from app import permissions
from app.policy import ApprovalDecision, ApprovalGate
from core.models import ToolCall


def test_tier_mapping_and_fail_closed_default():
    assert permissions.tier_for("read") == "read"
    assert permissions.tier_for("write") == "write"
    assert permissions.tier_for("exec") == "exec"
    assert permissions.tier_for("network") == "network"
    assert permissions.tier_for("remote_action") == "remote_action"
    assert permissions.tier_for("") == "exec"
    assert permissions.tier_for("unknown-risk") == "exec"
    assert permissions.label("write") == "T1 工作区写入"
    assert permissions.label("nope") == "nope"


def test_scope_matrix_follows_side_effect_strength():
    assert permissions.scopes_for("write", workspace_eligible=True) == [
        "once", "session", "workspace", "deny"]
    assert permissions.scopes_for("write", workspace_eligible=False) == [
        "once", "session", "deny"]
    assert permissions.scopes_for("network", workspace_eligible=True) == [
        "once", "session", "deny"]
    assert permissions.scopes_for("remote_action") == ["once", "deny"]
    assert permissions.scopes_for("read") == ["deny"]
    assert permissions.scope_allowed("network", "session") is True
    assert permissions.scope_allowed("network", "workspace") is False
    assert permissions.scope_allowed("remote_action", "session") is False
    assert permissions.scope_allowed("exec", "workspace", workspace_eligible=False) is False


def _gate(risk_by_name: dict[str, str], handler=None) -> ApprovalGate:
    gate = ApprovalGate(handler=handler)
    gate.tier_resolver = lambda call: risk_by_name.get(call.name, "read")
    return gate


def test_decisions_reflect_tiers(tmp_path):
    gate = _gate({"web_search": "network", "bash": "exec", "apply_patch": "write",
                  "write_file": "write", "browser_click": "write"})
    gate.require_once("browser_click", lambda call, decision: None)
    assert gate.decisions_for(ToolCall("web_search", {"query": "x"})) == [
        "once", "session", "deny"]
    assert gate.decisions_for(ToolCall("apply_patch", {"patch": "x"})) == [
        "once", "session", "deny"]
    assert gate.decisions_for(ToolCall("bash", {"command": "true"})) == [
        "once", "session", "workspace", "deny"]
    assert gate.decisions_for(ToolCall("write_file", {"path": "a", "content": "b"})) == [
        "once", "session", "workspace", "deny"]
    assert gate.decisions_for(ToolCall("browser_click", {"element": "x"})) == [
        "once", "deny"]


@pytest.mark.parametrize("name,risk,scope,allowed", [
    ("bash", "exec", "workspace", True),
    ("web_search", "network", "session", True),
    ("web_search", "network", "workspace", False),
    ("browser_click", "remote_action", "session", False),
])
def test_request_rejects_scopes_the_tier_does_not_allow(tmp_path, name, risk, scope, allowed):
    async def handler(call, preview):
        return ApprovalDecision(True, scope)

    gate = _gate({name: risk}, handler)
    call = ToolCall(name, {"query": "x"} if name == "web_search" else {"command": "true"})
    decision = asyncio.run(gate.request(call, "preview", workspace=tmp_path))
    assert decision.allowed is allowed
    assert decision.scope == (scope if allowed else "deny")


def test_workspace_grant_is_not_remembered_for_network_tier(tmp_path):
    async def handler(call, preview):
        return ApprovalDecision(True, "workspace")

    gate = _gate({"web_search": "network"}, handler)
    call = ToolCall("web_search", {"query": "x"})
    assert asyncio.run(gate.request(call, "preview", workspace=tmp_path)).allowed is False
    assert gate.permissions(tmp_path)["workspace_allowed"] is False
