import asyncio
import json

import pytest

from app.policy import ApprovalDecision, ApprovalGate
from core.models import ToolCall


def run(coro):
    return asyncio.run(coro)


def test_default_fails_closed():
    gate = ApprovalGate()
    decision = run(gate.request(ToolCall("write_file", {"path": "a"}), "diff"))
    assert decision.allowed is False


def test_session_allow_is_keyed_by_tool_and_path():
    calls = []

    async def handler(call, preview):
        calls.append(call)
        return ApprovalDecision(True, "session")

    gate = ApprovalGate(handler=handler)
    run(gate.request(ToolCall("write_file", {"path": "a"}), "d"))
    run(gate.request(ToolCall("write_file", {"path": "a"}), "d"))
    assert len(calls) == 1

    run(gate.request(ToolCall("write_file", {"path": "b"}), "d"))
    assert len(calls) == 2


def test_audit_written(tmp_path):
    async def handler(call, preview):
        return ApprovalDecision(True, "once")

    audit = tmp_path / "audit.jsonl"
    gate = ApprovalGate(handler=handler, audit_path=audit)
    run(gate.request(ToolCall("edit_file", {"path": "a"}), "diff"))

    event = json.loads(audit.read_text().strip())
    assert event["tool"] == "edit_file"
    assert event["decision"] == "once"
    assert "args_hash" in event


def test_session_allow_is_keyed_by_command():
    calls = []

    async def handler(call, preview):
        calls.append(call)
        return ApprovalDecision(True, "session")

    gate = ApprovalGate(handler=handler)
    run(gate.request(ToolCall("bash", {"command": "echo a"}), "p"))
    run(gate.request(ToolCall("bash", {"command": "echo a"}), "p"))
    assert len(calls) == 1

    run(gate.request(ToolCall("bash", {"command": "echo b"}), "p"))
    assert len(calls) == 2


def test_session_rule_is_bound_to_canonical_workspace(tmp_path):
    calls = []

    async def handler(call, preview):
        calls.append(call)
        return ApprovalDecision(True, "session")

    workspace = tmp_path / "project"
    workspace.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(workspace, target_is_directory=True)
    gate = ApprovalGate(handler=handler)
    call = ToolCall("write_file", {"path": "a.py"})
    run(gate.request(call, "diff", workspace=workspace))
    run(gate.request(call, "diff", workspace=alias))
    assert len(calls) == 1
    run(gate.request(call, "diff", workspace=other))
    assert len(calls) == 2


def test_session_file_rule_normalizes_path_but_keeps_tool(tmp_path):
    calls = []

    async def handler(call, preview):
        calls.append(call)
        return ApprovalDecision(True, "session")

    gate = ApprovalGate(handler=handler)
    run(gate.request(ToolCall("write_file", {"path": "a.py"}), "d", tmp_path))
    run(gate.request(ToolCall("write_file", {"path": "./a.py"}), "d", tmp_path))
    run(gate.request(ToolCall("edit_file", {"path": "a.py"}), "d", tmp_path))
    assert len(calls) == 2


def test_session_command_rule_includes_canonical_cwd(tmp_path):
    calls = []

    async def handler(call, preview):
        calls.append(call)
        return ApprovalDecision(True, "session")

    gate = ApprovalGate(handler=handler)
    run(gate.request(ToolCall("bash", {"command": "pwd"}), "p", tmp_path))
    run(gate.request(ToolCall("bash", {"command": "pwd", "cwd": "./", "timeout": 80}), "p", tmp_path))
    assert len(calls) == 1
    run(gate.request(ToolCall("bash", {"command": "pwd", "cwd": "src"}), "p", tmp_path))
    assert len(calls) == 2


@pytest.mark.parametrize("args", [{"query": "one"}, {"path": "a", "content": "one"}])
def test_mcp_session_rule_requires_identical_arguments(tmp_path, args):
    calls = []

    async def handler(call, preview):
        calls.append(call)
        return ApprovalDecision(True, "session")

    gate = ApprovalGate(handler=handler)
    first = ToolCall("mcp_notes_write", args)
    same = ToolCall("mcp_notes_write", dict(reversed(list(args.items()))))
    changed = ToolCall("mcp_notes_write", {**args, "extra": "changed"})
    run(gate.request(first, "p", tmp_path))
    run(gate.request(same, "p", tmp_path))
    assert len(calls) == 1
    run(gate.request(changed, "p", tmp_path))
    assert len(calls) == 2


def test_workspace_grant_covers_only_builtin_writes_and_commands(tmp_path):
    calls = []

    async def handler(call, preview):
        calls.append(call)
        return ApprovalDecision(True, "workspace" if len(calls) == 1 else "once")

    gate = ApprovalGate(handler=handler)
    for tool, args in [
        ("write_file", {"path": "a"}),
        ("edit_file", {"path": "b"}),
        ("bash", {"command": "python tests.py"}),
    ]:
        assert run(gate.request(ToolCall(tool, args), "p", tmp_path)).allowed
    assert len(calls) == 1
    assert run(gate.request(ToolCall("mcp_notes_write", {}), "p", tmp_path)).allowed
    assert len(calls) == 2
    assert gate.permissions(tmp_path) == {"workspace_allowed": True, "session_rules": 0}


def test_workspace_grant_does_not_apply_to_other_workspace_or_restart(tmp_path):
    calls = []

    async def handler(call, preview):
        calls.append(call)
        return ApprovalDecision(True, "workspace")

    gate = ApprovalGate(handler=handler)
    call = ToolCall("bash", {"command": "pwd"})
    run(gate.request(call, "p", tmp_path / "first"))
    run(gate.request(call, "p", tmp_path / "other"))
    assert len(calls) == 2
    restarted = ApprovalGate()
    assert not run(restarted.request(call, "p", tmp_path / "first")).allowed


@pytest.mark.parametrize("workspace", [None, "project"])
def test_workspace_scope_is_rejected_for_mcp(tmp_path, workspace):
    async def handler(call, preview):
        return ApprovalDecision(True, "workspace")

    gate = ApprovalGate(handler=handler)
    root = tmp_path / workspace if workspace else None
    decision = run(gate.request(ToolCall("mcp_notes_write", {}), "p", root))
    assert not decision.allowed
    assert gate.workspace_allowed == set()
    assert not gate.session_allowed


def test_workspace_scope_requires_workspace():
    async def handler(call, preview):
        return ApprovalDecision(True, "workspace")

    gate = ApprovalGate(handler=handler)
    assert not run(gate.request(ToolCall("write_file", {"path": "a"}), "p")).allowed
    assert gate.workspace_allowed == set()


def test_clear_revokes_only_current_workspace_permissions(tmp_path):
    async def handler(call, preview):
        return ApprovalDecision(True, "workspace" if call.name == "bash" else "session")

    gate = ApprovalGate(handler=handler)
    first = tmp_path / "first"
    other = tmp_path / "other"
    for root in (first, other):
        run(gate.request(ToolCall("edit_file", {"path": "a"}), "d", root))
        run(gate.request(ToolCall("bash", {"command": "pwd"}), "p", root))
        assert gate.permissions(root) == {"workspace_allowed": True, "session_rules": 1}
    gate.clear(first / ".")
    assert gate.permissions(first) == {"workspace_allowed": False, "session_rules": 0}
    assert gate.permissions(other) == {"workspace_allowed": True, "session_rules": 1}
    gate.handler = None
    assert not run(gate.request(ToolCall("edit_file", {"path": "a"}), "d", first)).allowed
    assert run(gate.request(ToolCall("edit_file", {"path": "a"}), "d", other)).allowed


def test_workspace_grant_and_cached_use_are_audited(tmp_path):
    async def handler(call, preview):
        return ApprovalDecision(True, "workspace")

    audit = tmp_path / "audit.jsonl"
    workspace = tmp_path / "project"
    gate = ApprovalGate(handler=handler, audit_path=audit)
    run(gate.request(ToolCall("write_file", {"path": "a"}), "d", workspace))
    run(gate.request(ToolCall("bash", {"command": "pwd"}), "p", workspace))
    events = [json.loads(line) for line in audit.read_text().splitlines()]
    assert [event["decision"] for event in events] == ["workspace", "workspace_allow"]
    assert all(event["workspace"] == str(workspace.resolve()) for event in events)
    assert all("args_hash" in event and "preview_hash" in event for event in events)
