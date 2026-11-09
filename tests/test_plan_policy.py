from __future__ import annotations

import pytest

from app.runtime.plan_policy import (
    DEFAULT_PLAN_POLICY,
    PLAN_POLICY_ENV,
    PLAN_POLICY_FILE_ENV,
    PlanPolicyError,
    applied_plan_policy,
    plan_policy,
    plan_policy_part,
)


class FakeAgent:
    def __init__(self):
        self.system_parts = [{
            "kind": "base_instructions", "label": "基础指令",
            "source": "agent.system", "content": "base",
        }]
        self.system = "base"

    def add_system_part(self, *, kind, label, source, content):
        self.system_parts.append({
            "kind": kind, "label": label, "source": source, "content": content,
        })
        self.system = "".join(part["content"] for part in self.system_parts)


def test_default_policy_covers_plan_contract():
    text = plan_policy({})
    assert text == DEFAULT_PLAN_POLICY.strip()
    assert "plan mode" in text
    assert "read-only" in text
    assert "submit_plan" in text and "final tool call" in text
    assert "decision-complete" in text
    assert "approves nothing" in text


def test_env_text_override_and_part():
    text = plan_policy({PLAN_POLICY_ENV: "custom plan rules"})
    assert text == "custom plan rules"
    part = plan_policy_part({PLAN_POLICY_ENV: "custom plan rules"})
    assert part["kind"] == "plan_policy"
    assert "[PLAN MODE]" in part["content"]
    assert "custom plan rules" in part["content"]


def test_file_override_and_text_precedence(tmp_path):
    path = tmp_path / "policy.md"
    path.write_text("file policy", encoding="utf-8")
    assert plan_policy({PLAN_POLICY_FILE_ENV: str(path)}) == "file policy"
    assert plan_policy({
        PLAN_POLICY_ENV: "text policy",
        PLAN_POLICY_FILE_ENV: str(path),
    }) == "text policy"


def test_missing_or_empty_file_fails_closed(tmp_path):
    with pytest.raises(PlanPolicyError):
        plan_policy({PLAN_POLICY_FILE_ENV: str(tmp_path / "missing.md")})
    empty = tmp_path / "empty.md"
    empty.write_text("  \n", encoding="utf-8")
    with pytest.raises(PlanPolicyError):
        plan_policy({PLAN_POLICY_FILE_ENV: str(empty)})


def test_scope_applies_and_restores_agent_prompt():
    agent = FakeAgent()
    with applied_plan_policy(agent, {PLAN_POLICY_ENV: "scope policy"}):
        assert "[PLAN MODE]" in agent.system
        assert "scope policy" in agent.system
        assert agent.system_parts[-1]["kind"] == "plan_policy"
    assert agent.system == "base"
    assert len(agent.system_parts) == 1
