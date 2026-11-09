"""P9：用户级持久策略规则的权威撤销。"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.config import Config
from app.runtime.permission_view import (
    PermissionRuleNotRevocable,
    PermissionRuleNotFound,
    permission_overview,
    revoke_rule,
)
from core.contracts import ToolCall
from tests.test_stdio import Harness, agent_for


def _write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def _setup(tmp_path, monkeypatch, *, user=True):
    project = tmp_path / ".vibe" / "permissions.json"
    _write(project, {"version": 1, "rules": [{"tool": "bash", "command_prefix": "rm", "decision": "once"}]})
    user_path = tmp_path / "user-permissions.json"
    if user:
        _write(user_path, {"version": 1, "autonomy": "edits", "rules": [
            {"tool": "bash", "command_prefix": "pytest", "decision": "allow", "note": "本地测试"},
            {"tool": "web_search", "scopes": ["once"]},
        ]})
        monkeypatch.setenv("VIBE_PERMISSIONS", str(user_path))
    else:
        monkeypatch.setenv("VIBE_PERMISSIONS", "")
    agent = agent_for(tmp_path, [])
    agent._reload_permission_policy()
    return project, user_path, agent


def test_overview_exposes_rule_ids_and_only_user_rules_are_revocable(tmp_path, monkeypatch):
    project, user_path, agent = _setup(tmp_path, monkeypatch)
    before = user_path.read_text(encoding="utf-8")
    overview = permission_overview(agent, tmp_path)
    assert overview["revoked_rule"] is None
    user_entries = [entry for entry in overview["persistent"] if entry["source"] == "user"]
    project_entries = [entry for entry in overview["persistent"] if entry["source"] == "project"]
    assert len(user_entries) == 2 and len(project_entries) == 1
    assert all(entry["id"].startswith("ur-") and entry["revocable"] is True for entry in user_entries)
    assert all(entry["path"] == str(user_path) and "撤销" in entry["reason"] for entry in user_entries)
    assert project_entries[0]["id"].startswith("pr-") and project_entries[0]["revocable"] is False
    assert "只能收紧" in project_entries[0]["reason"]
    assert user_path.read_text(encoding="utf-8") == before


def test_revoke_user_rule_writes_atomically_preserves_other_fields_and_reloads(tmp_path, monkeypatch):
    project, user_path, agent = _setup(tmp_path, monkeypatch)
    target = next(entry for entry in permission_overview(agent, tmp_path)["persistent"]
                  if entry["source"] == "user" and "pytest" in entry["rule"])
    assert agent.permission_policy.evaluate(ToolCall("bash", {"command": "pytest -q"}, "c1"), tier="exec").decision == "allow"

    result = revoke_rule(agent, tmp_path, target["id"])
    assert result["revoked_rule"]["id"] == target["id"] and result["revoked_rule"]["source"] == "user"
    assert "pytest" in result["revoked_rule"]["rule"] and result["revoked_rule"]["path"] == str(user_path)
    document = json.loads(user_path.read_text(encoding="utf-8"))
    assert document["autonomy"] == "edits" and document["version"] == 1
    assert [rule["tool"] for rule in document["rules"]] == ["web_search"]
    remaining = [entry["id"] for entry in result["persistent"] if entry["source"] == "user"]
    assert target["id"] not in remaining and len(remaining) == 1
    assert agent.permission_policy.evaluate(ToolCall("bash", {"command": "pytest -q"}, "c2"), tier="exec").decision is None
    assert not (user_path.parent / (user_path.name + ".tmp")).exists()


def test_revoke_rule_rejects_project_unknown_disabled_and_broken_user_policy(tmp_path, monkeypatch):
    project, user_path, agent = _setup(tmp_path, monkeypatch)
    overview = permission_overview(agent, tmp_path)
    project_id = next(entry["id"] for entry in overview["persistent"] if entry["source"] == "project")
    with pytest.raises(PermissionRuleNotRevocable):
        revoke_rule(agent, tmp_path, project_id)
    with pytest.raises(PermissionRuleNotFound):
        revoke_rule(agent, tmp_path, "ur-" + "0" * 12)
    with pytest.raises(PermissionRuleNotFound):
        revoke_rule(agent, tmp_path, project_id.replace("pr-", "ur-"))

    disabled_project, _, disabled_agent = _setup(tmp_path / "disabled", monkeypatch, user=False)
    with pytest.raises(PermissionRuleNotRevocable):
        revoke_rule(disabled_agent, tmp_path / "disabled", "ur-" + "1" * 12)

    existing_user_id = next(entry["id"] for entry in overview["persistent"] if entry["source"] == "user")
    user_path.write_text('{broken', encoding="utf-8")
    before = user_path.read_text(encoding="utf-8")
    with pytest.raises(PermissionRuleNotRevocable):
        revoke_rule(agent, tmp_path, existing_user_id)
    assert user_path.read_text(encoding="utf-8") == before


def test_stdio_revoke_rule_returns_remaining_rules_and_error_codes(tmp_path, monkeypatch):
    project, user_path, agent = _setup(tmp_path, monkeypatch)

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path))
        await harness.until("ready")
        harness.send("command", "p1", command="permissions", args={"action": "list"})
        overview = await harness.until("permission_overview", "p1")
        user_ids = [entry["id"] for entry in overview["persistent"] if entry["source"] == "user"]
        project_id = next(entry["id"] for entry in overview["persistent"] if entry["source"] == "project")
        harness.send("command", "p2", command="permissions", args={"action": "revoke-rule", "rule_id": user_ids[0]})
        revoked = await harness.until("permission_overview", "p2")
        assert revoked["revoked_rule"]["id"] == user_ids[0]
        assert len([entry for entry in revoked["persistent"] if entry["source"] == "user"]) == 1
        harness.send("command", "p3", command="permissions", args={"action": "revoke-rule", "rule_id": project_id})
        assert (await harness.until("error", "p3"))["code"] == "permission_rule_not_revocable"
        harness.send("command", "p4", command="permissions", args={"action": "revoke-rule", "rule_id": "ur-" + "3" * 12})
        assert (await harness.until("error", "p4"))["code"] == "permission_rule_not_found"
        harness.send("command", "p5", command="permissions", args={"action": "revoke-rule"})
        assert (await harness.until("error", "p5"))["code"] == "invalid_request"
        await harness.close()

    asyncio.run(scenario())
