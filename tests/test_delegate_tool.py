"""opt-in 只读 delegate 工具测试（P2 委派）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.providers.fake import FakeProvider
from app.runtime.agent import Agent
from app.safety.policy import ApprovalGate
from app.stdio import register_delegate_tool
from app.tools.registry import ToolRegistry


def _agent(tmp_path, *, max_run_tokens=0):
    return Agent(
        FakeProvider(["ok"]), ToolRegistry(tmp_path), approval=ApprovalGate(),
        max_run_tokens=max_run_tokens,
    )


def test_delegate_tool_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("VIBE_SUBAGENT_DELEGATE", raising=False)
    agent = _agent(tmp_path)
    assert register_delegate_tool(agent, tmp_path) is False
    assert "delegate" not in agent.tools.names()


def test_delegate_tool_registers_and_reports_untrusted_claim(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_SUBAGENT_DELEGATE", "1")
    monkeypatch.setenv("VIBE_DELEGATE_MAX_STEPS", "5")
    monkeypatch.setenv("VIBE_DELEGATE_MAX_TOKENS", "9000")
    monkeypatch.setenv("VIBE_DELEGATE_MAX_SECONDS", "45")
    captured = {}

    async def fake_run_subagent(provider, workspace, objective, **kwargs):
        captured.update({"objective": objective, **kwargs})
        return SimpleNamespace(status="ok", summary="只读摘要", task_id="t-sub-1",
                               usage={"total_tokens": 123}, cost_usd=0.001, error=None)

    monkeypatch.setattr("app.runtime.subagents.run_subagent", fake_run_subagent)
    agent = _agent(tmp_path)
    assert register_delegate_tool(agent, tmp_path) is True
    assert "delegate" in agent.tools.names()
    fn = agent.tools._tools["delegate"].fn
    text = asyncio.run(fn(prompt="读 README 并总结"))
    assert "untrusted claim" in text and "status=ok" in text
    assert "tokens=123" in text and "只读摘要" in text
    assert captured["objective"] == "读 README 并总结"
    assert captured["max_steps"] == 5
    assert captured["max_run_tokens"] == 9000
    assert captured["max_run_seconds"] == 45.0
    assert captured["session_root"] == tmp_path
    from app.runtime.notifications import read_notifications

    kinds = [item["kind"] for item in read_notifications(tmp_path)]
    assert "subagent_finished" in kinds


def test_delegate_tool_respects_parent_token_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_SUBAGENT_DELEGATE", "1")
    monkeypatch.setenv("VIBE_DELEGATE_MAX_TOKENS", "200000")
    captured = {}

    async def fake_run_subagent(provider, workspace, objective, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status="ok", summary="x", task_id="t", usage={}, cost_usd=0.0, error=None)

    monkeypatch.setattr("app.runtime.subagents.run_subagent", fake_run_subagent)
    agent = _agent(tmp_path, max_run_tokens=5000)
    register_delegate_tool(agent, tmp_path)
    asyncio.run(agent.tools._tools["delegate"].fn(prompt="查"))
    assert captured["max_run_tokens"] == 5000


def test_delegate_tool_clamps_budget_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_SUBAGENT_DELEGATE", "1")
    monkeypatch.setenv("VIBE_DELEGATE_MAX_STEPS", "99")
    monkeypatch.setenv("VIBE_DELEGATE_MAX_TOKENS", "1")
    monkeypatch.setenv("VIBE_DELEGATE_MAX_SECONDS", "9999")
    captured = {}

    async def fake_run_subagent(provider, workspace, objective, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status="ok", summary="x", task_id="t", usage={}, cost_usd=0.0, error=None)

    monkeypatch.setattr("app.runtime.subagents.run_subagent", fake_run_subagent)
    agent = _agent(tmp_path)
    register_delegate_tool(agent, tmp_path)
    asyncio.run(agent.tools._tools["delegate"].fn(prompt="查"))
    assert captured["max_steps"] == 15
    assert captured["max_run_tokens"] == 500
    assert captured["max_run_seconds"] == 300.0


def test_delegate_tool_skips_when_tools_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_SUBAGENT_DELEGATE", "1")
    agent = _agent(tmp_path)
    agent.tools_enabled = False
    assert register_delegate_tool(agent, tmp_path) is False


def test_delegate_tool_runs_through_agent_loop(tmp_path, monkeypatch):
    from core.contracts import Response, ToolCall

    monkeypatch.setenv("VIBE_SUBAGENT_DELEGATE", "1")
    calls = []

    async def fake_run_subagent(provider, workspace, objective, **kwargs):
        calls.append(objective)
        return SimpleNamespace(status="ok", summary="子 Agent 摘要", task_id="t-sub-9",
                               usage={"total_tokens": 77}, cost_usd=0.0, error=None)

    monkeypatch.setattr("app.runtime.subagents.run_subagent", fake_run_subagent)
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("delegate", {"prompt": "读 README"}, id="c1")]),
        "完成",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate())
    register_delegate_tool(agent, tmp_path)
    result = asyncio.run(agent.run("请使用 delegate 调研"))
    assert result.status == "done"
    assert calls == ["读 README"]
    tool_contents = [message.content for message in agent.history
                     if getattr(message, "role", None) == "tool"]
    assert any("untrusted claim" in content or "子 Agent 摘要" in content
               for content in tool_contents)


def test_delegate_failure_surfaces_as_tool_error(tmp_path, monkeypatch):
    from core.contracts import Response, ToolCall

    monkeypatch.setenv("VIBE_SUBAGENT_DELEGATE", "1")

    async def failing(provider, workspace, objective, **kwargs):
        raise RuntimeError("child exploded")

    monkeypatch.setattr("app.runtime.subagents.run_subagent", failing)
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("delegate", {"prompt": "查"}, id="c1")]),
        "子 Agent 失败已记录，结束。",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate())
    register_delegate_tool(agent, tmp_path)
    result = asyncio.run(agent.run("请委派"))
    assert result.status == "done"
    tool_contents = [message.content for message in agent.history
                     if getattr(message, "role", None) == "tool"]
    assert any("RuntimeError" in content and "child exploded" in content
               for content in tool_contents)
