import asyncio

import pytest

from app.agent import Agent
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry
from core.models import Response, ToolCall


def test_action_preamble_gets_one_correction_before_approved_write(tmp_path):
    decisions = []

    async def allow(call, preview):
        decisions.append((call.name, preview))
        return ApprovalDecision(True, "once")

    provider = FakeProvider([
        "我会创建文件。",
        Response(tool_calls=[ToolCall(
            "write_file", {"path": "result.txt", "content": "created"}, id="write-1",
        )]),
        "文件已创建。",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), require_tool_use=True,
                  approval=ApprovalGate(allow))
    events = []

    result = asyncio.run(agent.run("创建 result.txt", trace=events.append))

    assert (result.status, result.steps, result.text) == ("done", 3, "文件已创建。")
    assert (tmp_path / "result.txt").read_text() == "created"
    assert [name for name, _ in decisions] == ["write_file"]
    assert "actually execute" in provider.seen[0][0].content
    corrections = [m for m in agent.history if m.name == "action_required"]
    assert len(corrections) == 1 and corrections[0].role == "system"
    assert corrections[0] in provider.seen[1]
    assert [e["type"] for e in events].count("action_required") == 1
    assert not any(e["type"] == "no_action" for e in events)


def test_two_actionless_answers_stop_with_latest_explanation(tmp_path):
    provider = FakeProvider(["准备开始。", "我还没有执行操作。", "不应再请求。"])
    agent = Agent(provider, ToolRegistry(tmp_path), require_tool_use=True)
    events = []

    result = asyncio.run(agent.run("创建 result.txt", trace=events.append))

    assert (result.status, result.stop_reason, result.steps) == ("stopped", "no_action", 2)
    assert result.text == "我还没有执行操作。"
    assert len(provider.seen) == 2
    assert not (tmp_path / "result.txt").exists()
    assert [e["type"] for e in events].count("action_required") == 1
    assert [e["type"] for e in events].count("no_action") == 1
    assert events[-1]["type"] == "run_end"
    assert events[-1]["tool_calls"] == 0


def test_default_tool_question_finishes_without_correction(tmp_path):
    provider = FakeProvider(["这是解释。"])
    agent = Agent(provider, ToolRegistry(tmp_path))

    result = asyncio.run(agent.run("解释这个概念"))

    assert (result.status, result.text) == ("done", "这是解释。")
    assert len(provider.seen) == 1
    assert not any(m.name == "action_required" for m in agent.history)


def test_budget_exhaustion_prevents_action_correction_request(tmp_path):
    provider = FakeProvider([
        Response("准备开始。", usage={"prompt_tokens": 6000, "completion_tokens": 4000}),
        "不应再请求。",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), require_tool_use=True, max_run_tokens=10000)
    events = []

    result = asyncio.run(agent.run("创建 result.txt", trace=events.append))

    assert (result.status, result.stop_reason) == ("stopped", "token_budget")
    assert len(provider.seen) == 1
    assert not any(e["type"] == "action_required" for e in events)


def test_denied_action_does_not_force_another_attempt(tmp_path):
    decisions = []

    async def deny(call, preview):
        decisions.append(call.name)
        return ApprovalDecision(False, "deny")

    provider = FakeProvider([
        Response(tool_calls=[ToolCall(
            "write_file", {"path": "result.txt", "content": "created"}, id="write-1",
        )]),
        "审批拒绝，未写入。",
        "不应再请求。",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), require_tool_use=True,
                  approval=ApprovalGate(deny))
    events = []

    result = asyncio.run(agent.run("创建 result.txt", trace=events.append))

    assert result.text == "审批拒绝，未写入。"
    assert decisions == ["write_file"] and len(provider.seen) == 2
    assert not (tmp_path / "result.txt").exists()
    assert not any(e["type"] == "action_required" for e in events)
    assert events[-1]["tool_calls"] == 1


def test_action_requirement_rejects_chat_only_mode(tmp_path):
    with pytest.raises(ValueError, match="require_tool_use.*tools_enabled"):
        Agent(FakeProvider([]), ToolRegistry(tmp_path), tools_enabled=False,
              require_tool_use=True)


def test_action_correction_respects_existing_step_limit(tmp_path):
    provider = FakeProvider(["准备开始。", "不应再请求。"])
    agent = Agent(provider, ToolRegistry(tmp_path), require_tool_use=True, max_steps=1)

    result = asyncio.run(agent.run("创建 result.txt"))

    assert (result.status, result.stop_reason, result.steps) == ("stopped", "max_steps", 1)
    assert len(provider.seen) == 1


def test_cancel_after_preamble_prevents_correction_request(tmp_path):
    provider = FakeProvider(["准备开始。", "不应再请求。"])
    agent = Agent(provider, ToolRegistry(tmp_path), require_tool_use=True)
    cancel = asyncio.Event()

    def cancel_on_answer(message):
        if message.role == "assistant":
            cancel.set()

    result = asyncio.run(agent.run("创建 result.txt", on_message=cancel_on_answer, cancel=cancel))

    assert result.status == "cancelled"
    assert len(provider.seen) == 1
