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


def test_autonomy_matrix():
    from app import permissions

    assert permissions.autonomy_allows("ask", "write", workspace_eligible=True) is False
    assert permissions.autonomy_allows("edits", "write", workspace_eligible=True) is True
    assert permissions.autonomy_allows("edits", "write", workspace_eligible=False) is False
    assert permissions.autonomy_allows("edits", "exec", workspace_eligible=True) is False
    assert permissions.autonomy_allows("workspace", "write", workspace_eligible=True) is True
    assert permissions.autonomy_allows("workspace", "exec", workspace_eligible=True) is True
    assert permissions.autonomy_allows("workspace", "exec", workspace_eligible=False) is False
    assert permissions.autonomy_allows("workspace", "network", workspace_eligible=False) is False
    assert permissions.autonomy_allows("full", "write", workspace_eligible=False) is True
    assert permissions.autonomy_allows("full", "exec", workspace_eligible=False) is True
    assert permissions.autonomy_allows("full", "network", workspace_eligible=False) is True
    assert permissions.autonomy_allows("full", "remote_action", workspace_eligible=False) is False
    assert permissions.strictest_autonomy("full", None) == "full"
    assert permissions.strictest_autonomy("workspace", "edits") == "edits"
    assert permissions.strictest_autonomy(None, None) == "ask"


def test_policy_autonomy_user_and_project_cap(tmp_path):
    user = _write(tmp_path / "user.json", {"version": 1, "autonomy": "full", "rules": []})
    project = _write(tmp_path / "project.json", {"version": 1, "autonomy": "edits", "rules": []})
    policy = PermissionPolicy.from_files(project, user)
    assert policy.autonomy == "full"
    assert policy.autonomy_cap == "edits"
    assert policy.effective_autonomy() == "edits"          # 项目只能更严
    assert policy.effective_autonomy("full") == "edits"
    assert policy.effective_autonomy("ask") == "ask"
    overridden = _write(tmp_path / "user2.json", {"version": 1, "autonomy": "workspace", "rules": []})
    assert PermissionPolicy.from_files(None, overridden).effective_autonomy() == "workspace"
    bad = _write(tmp_path / "bad.json", {"version": 1, "autonomy": "yolo", "rules": []})
    with pytest.raises(PolicyError, match="autonomy"):
        PermissionPolicy.from_files(None, bad)


def _auto_gate(level: str, handler=None) -> ApprovalGate:
    gate = ApprovalGate(handler=handler)
    gate.tier_resolver = lambda call: {"write_file": "write", "edit_file": "write",
                                       "apply_patch": "write", "bash": "exec",
                                       "web_search": "network",
                                       "browser_click": "remote_action"}.get(call.name, "read")
    gate.autonomy = level
    return gate


@pytest.mark.parametrize("level,name,args,auto", [
    ("ask", "write_file", {"path": "a", "content": "b"}, False),
    ("edits", "write_file", {"path": "a", "content": "b"}, True),
    ("edits", "bash", {"command": "true"}, False),
    ("workspace", "bash", {"command": "true"}, True),
    ("workspace", "web_search", {"query": "x"}, False),
    ("full", "web_search", {"query": "x"}, True),
    ("full", "browser_click", {}, False),
])
def test_gate_autonomy_levels_skip_or_ask(tmp_path, level, name, args, auto):
    handler = _Handler()
    gate = _auto_gate(level, handler)
    decision = asyncio.run(gate.request(ToolCall(name, args), "p", workspace=tmp_path))
    assert decision.allowed is True
    assert handler.calls == ([] if auto else [name])


def test_autonomy_does_not_override_policy_deny_or_once(tmp_path):
    handler = _Handler()
    gate = _auto_gate("full", handler)
    gate.policy = _policy(user={"rules": [
        {"tool": "bash", "command_prefix": "rm", "decision": "deny"},
        {"tool": "bash", "command_prefix": "git", "decision": "once"}]})
    denied = asyncio.run(gate.request(ToolCall("bash", {"command": "rm -rf x"}), "p", workspace=tmp_path))
    assert denied.allowed is False and handler.calls == []
    once = asyncio.run(gate.request(ToolCall("bash", {"command": "git clean -fd"}), "p", workspace=tmp_path))
    assert once.allowed is True and handler.calls == ["bash"]  # 显式 once 覆盖 full


def test_agent_autonomy_from_env_and_param(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_AUTONOMY", "workspace")
    agent = Agent(FakeProvider(["ok"]), ToolRegistry(tmp_path))
    assert agent.autonomy == "workspace"
    assert agent.approval is None
    explicit = Agent(FakeProvider(["ok"]), ToolRegistry(tmp_path), autonomy="edits")
    assert explicit.autonomy == "edits"


def test_agent_reloads_autonomy_from_policy(tmp_path, monkeypatch):
    user = tmp_path / "user-permissions.json"
    _write(user, {"version": 1, "autonomy": "edits", "rules": []})
    monkeypatch.setenv("VIBE_PERMISSIONS", str(user))
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("write_file", {"path": "a.txt", "content": "x"}, id="w1")]),
        "done",
    ])
    handler = _Handler()
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(handler))
    first = asyncio.run(agent.run("run1"))
    assert first.status == "done" and agent.autonomy == "edits" and handler.calls == []
    _write(user, {"version": 1, "autonomy": "ask", "rules": []})
    asyncio.run(agent.run("run2"))
    assert agent.autonomy == "ask"


def test_apply_patch_gets_workspace_scope_and_autonomy(tmp_path):
    handler = _Handler(scope="workspace")
    gate = _auto_gate("edits", handler)
    call = ToolCall("apply_patch", {"patch": "--- a\n+++ b\n"})
    # 与 write_file 同级：edits 档位自动放行，decisions 也提供 workspace。
    assert gate.decisions_for(call) == ["once", "session", "workspace", "deny"]
    decision = asyncio.run(gate.request(call, "p", workspace=tmp_path))
    assert decision.allowed is True and handler.calls == []


def test_describe_rules_empty_policy_returns_empty_tuple():
    assert PermissionPolicy().describe_rules() == ()


def test_describe_rules_renders_user_and_project_rules_with_note_and_prefix():
    policy = _policy(
        project={"rules": [
            {"tool": "mcp_*", "tier": "exec", "scopes": ["once"]},
            {"tool": "bash", "command_prefix": "rm -rf", "decision": "once",
             "note": "删除类逐次确认"},
        ]},
        user={"rules": [
            {"tool": "bash", "command_prefix": ["pytest", "npm test"],
             "decision": "allow", "note": "本地测试"},
            {"tool": "bash", "command_regex": r"^curl\s", "decision": "deny"},
            {"tool": "web_search", "scopes": ["once"]},
        ]},
    )
    lines = policy.describe_rules()
    assert isinstance(lines, tuple)
    assert lines == (
        "- [project] mcp_* tier=exec scopes=once",
        "- [project] bash prefix=rm -rf → once（删除类逐次确认）",
        "- [user] bash prefix=pytest, npm test → allow（本地测试）",
        "- [user] bash regex=^curl\\s → deny",
        "- [user] web_search scopes=once",
    )


def test_describe_rules_includes_tier_scopes_decision_together():
    policy = _policy(user={"rules": [
        {"tool": "browser_*", "tier": "remote_action", "scopes": ["once"],
         "decision": "deny", "note": "远端动作"},
    ]})
    (line,) = policy.describe_rules()
    assert line == "- [user] browser_* tier=remote_action scopes=once → deny（远端动作）"

