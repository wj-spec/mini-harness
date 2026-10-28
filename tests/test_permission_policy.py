import asyncio
import json
from pathlib import Path

import pytest

from app.agent import Agent
from app.permission_policy import PermissionPolicy, PolicyError
from app.policy import ApprovalGate, ApprovalDecision
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry
from core.models import Response, ToolCall


def _write(path: Path, document: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _call(name: str = "bash", command: str = "true"):
    return ToolCall(name, {"command": command} if name == "bash" else {"query": "x"})


def _rules(value) -> list:
    if value is None:
        return []
    return value["rules"] if isinstance(value, dict) else value


def _policy(project=None, user=None) -> PermissionPolicy:
    root = Path(__file__).parent / "_policy_tmp"
    root.mkdir(exist_ok=True)
    project_path = _write(root / "project.json", {"version": 1, "rules": _rules(project)}) if project is not None else None
    user_path = _write(root / "user.json", {"version": 1, "rules": _rules(user)}) if user is not None else None
    try:
        return PermissionPolicy.from_files(project_path, user_path)
    finally:
        for path in (project_path, user_path):
            if path is not None:
                path.unlink(missing_ok=True)
        root.rmdir()


def test_parse_rejects_invalid_documents(tmp_path):
    project = _write(tmp_path / "project.json", {"version": 1, "rules": [
        {"tool": "bash", "decision": "allow"}]})
    with pytest.raises(PolicyError, match="project 策略只能收紧"):
        PermissionPolicy.from_files(project, None)
    project2 = _write(tmp_path / "p2.json", {"version": 1, "rules": [
        {"tool": "bash", "decision": "maybe"}]})
    with pytest.raises(PolicyError, match="decision"):
        PermissionPolicy.from_files(project2, None)
    project3 = _write(tmp_path / "p3.json", {"version": 1, "rules": [
        {"tool": "bash", "scopes": ["forever"]}]})
    with pytest.raises(PolicyError, match="scopes"):
        PermissionPolicy.from_files(project3, None)
    project4 = _write(tmp_path / "p4.json", {"version": 1, "rules": [
        {"tool": "bash", "tier": "T9"}]})
    with pytest.raises(PolicyError, match="tier"):
        PermissionPolicy.from_files(project4, None)
    invalid = tmp_path / "bad.json"
    invalid.write_text("{oops", encoding="utf-8")
    with pytest.raises(PolicyError, match="读取失败"):
        PermissionPolicy.from_files(None, invalid)


def test_rules_match_glob_prefix_and_regex():
    policy = _policy(user={"rules": [
        {"tool": "mcp_*", "decision": "deny"},
        {"tool": "bash", "command_prefix": ["rm", "git reset"], "decision": "once"},
        {"tool": "bash", "command_regex": r"^curl\s", "decision": "deny"},
    ]})
    assert policy.evaluate(ToolCall("mcp_browser_click", {}), tier="exec").decision == "deny"
    assert policy.evaluate(_call(command="rm -rf build"), tier="exec").decision == "once"
    assert policy.evaluate(_call(command="git reset --hard"), tier="exec").decision == "once"
    assert policy.evaluate(_call(command="curl https://x"), tier="exec").decision == "deny"
    assert policy.evaluate(_call(command="pytest -q"), tier="exec").decision is None


def test_precedence_project_restricts_and_user_relaxes():
    policy = _policy(
        project={"rules": [{"tool": "bash", "command_prefix": "pytest", "decision": "deny"}]},
        user={"rules": [{"tool": "bash", "command_prefix": "pytest", "decision": "allow"}]},
    )
    assert policy.evaluate(_call(command="pytest -q"), tier="exec").decision == "deny"
    relaxed = _policy(user={"rules": [{"tool": "bash", "command_prefix": "pytest", "decision": "allow"}]})
    assert relaxed.evaluate(_call(command="pytest -q"), tier="exec").decision == "allow"
    assert relaxed.evaluate(_call(command="make test"), tier="exec").decision is None


def test_tier_overrides_and_project_may_only_tighten():
    user = _policy(user={"rules": [{"tool": "bash", "tier": "network"}]})
    assert user.tier_for(_call(), "exec") == "network"
    project = _policy(project={"rules": [{"tool": "bash", "tier": "read"}]})
    assert project.tier_for(_call(), "exec") == "exec"  # project 不能放宽
    tighter = _policy(project={"rules": [{"tool": "bash", "tier": "network"}]})
    assert tighter.tier_for(_call(), "exec") == "network"


class _Handler:
    def __init__(self, scope: str = "once"):
        self.scope = scope
        self.calls: list[str] = []

    async def __call__(self, call, preview):
        self.calls.append(call.name)
        return ApprovalDecision(True, self.scope)


def _gate(policy: PermissionPolicy, handler=None) -> ApprovalGate:
    gate = ApprovalGate(handler=handler)
    gate.tier_resolver = lambda call: "exec" if call.name == "bash" else "network"
    gate.policy = policy
    return gate


def test_gate_policy_allow_skips_the_handler(tmp_path):
    handler = _Handler()
    gate = _gate(_policy(user={"rules": [{"tool": "bash", "command_prefix": "pytest", "decision": "allow"}]}), handler)
    decision = asyncio.run(gate.request(_call(command="pytest -q"), "p", workspace=tmp_path))
    assert decision.allowed is True and decision.scope == "policy"
    assert handler.calls == []


def test_gate_project_deny_beats_user_allow(tmp_path):
    handler = _Handler()
    gate = _gate(_policy(
        project={"rules": [{"tool": "bash", "command_prefix": "pytest", "decision": "deny"}]},
        user={"rules": [{"tool": "bash", "command_prefix": "pytest", "decision": "allow"}]},
    ), handler)
    decision = asyncio.run(gate.request(_call(command="pytest -q"), "p", workspace=tmp_path))
    assert decision.allowed is False and decision.scope == "deny"
    assert handler.calls == []


def test_gate_policy_force_once_and_scope_restriction(tmp_path):
    handler = _Handler(scope="session")
    gate = _gate(_policy(user={"rules": [
        {"tool": "bash", "command_prefix": "rm", "decision": "once"}]}), handler)
    call = _call(command="rm -rf build")
    assert gate.decisions_for(call) == ["once", "deny"]
    assert asyncio.run(gate.request(call, "p", workspace=tmp_path)).allowed is False

    scoped = _gate(_policy(user={"rules": [
        {"tool": "bash", "scopes": ["once"]}]}), _Handler(scope="once"))
    assert scoped.decisions_for(_call()) == ["once", "deny"]


def test_policy_allow_does_not_bypass_remote_action_floor(tmp_path):
    gate = ApprovalGate(handler=_Handler())
    gate.tier_resolver = lambda call: "remote_action"
    gate.policy = _policy(user={"rules": [{"tool": "browser_click", "decision": "allow"}]})
    decision = asyncio.run(gate.request(ToolCall("browser_click", {}), "p", workspace=tmp_path))
    assert decision.allowed is False


def test_agent_reloads_policy_each_run_and_keeps_last_valid(tmp_path, monkeypatch):
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("bash", {"command": "true"}, id="c1")]),
        "done-1",
        Response(tool_calls=[ToolCall("bash", {"command": "true"}, id="c2")]),
        "done-2",
        Response(tool_calls=[ToolCall("bash", {"command": "true"}, id="c3")]),
        "done-3",
    ])
    handler = _Handler(scope="once")
    user = _write(tmp_path / "user-permissions.json", {"version": 1, "rules": [
        {"tool": "bash", "command_prefix": "true", "decision": "allow"}]})
    monkeypatch.setenv("VIBE_PERMISSIONS", str(user))
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(handler))
    first = asyncio.run(agent.run("run1"))
    assert first.status == "done" and handler.calls == []

    # 项目级只能收紧：deny 覆盖用户级 allow。
    project = tmp_path / ".vibe" / "permissions.json"
    _write(project, {"version": 1, "rules": [
        {"tool": "bash", "command_prefix": "true", "decision": "deny"}]})
    second = asyncio.run(agent.run("run2"))
    assert second.status == "done"
    assert any("denied_by_user" in message.content
               for message in agent.history if message.role == "tool")
    assert handler.calls == []

    # 非法策略保留上一份（仍为 deny），并记录事件。
    project.write_text("{broken", encoding="utf-8")
    events = []
    third = asyncio.run(agent.run("run3", trace=events.append))
    assert third.status == "done"
    assert agent.permission_error and "读取失败" in agent.permission_error
    assert any(event.get("type") == "permission_policy_error" for event in events)
    assert handler.calls == []
