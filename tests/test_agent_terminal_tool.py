from __future__ import annotations

import asyncio

from core.contracts import Response, ToolCall

from app.providers.fake import FakeProvider
from app.runtime.agent import Agent
from app.tools.registry import ToolRegistry, ToolSpec


def _tool(name, fn):
    return ToolSpec(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}, "additionalProperties": True},
        fn=fn,
    )


def test_terminal_tool_stops_run_and_skips_rest_of_batch(tmp_path):
    executed: list[str] = []

    def submit_plan(**payload):
        executed.append("submit_plan")
        return "plan proposed: pl-1 v1"

    def probe(**payload):
        executed.append("probe")
        return "should not run"

    registry = ToolRegistry(tmp_path)
    registry.register(_tool("submit_plan", submit_plan))
    registry.register(_tool("probe", probe))
    provider = FakeProvider([
        Response(tool_calls=[
            ToolCall("submit_plan", {"objective": "目标"}, id="c1"),
            ToolCall("probe", {}, id="c2"),
        ]),
    ])
    agent = Agent(provider, registry, stop_after_tools={"submit_plan"})

    result = asyncio.run(agent.run("规划"))

    assert result.status == "done"
    assert result.stop_reason == "plan_submitted"
    assert result.text == "plan proposed: pl-1 v1"
    assert executed == ["submit_plan"]
    # 剩余工具调用要留下跳过记录，历史保持成对。
    tool_messages = [message for message in agent.history if message.role == "tool"]
    assert any(
        message.tool_call_id == "c2" and "not_executed" in message.content
        for message in tool_messages
    )


def test_terminal_tool_failure_does_not_stop_run(tmp_path):
    def submit_plan(**payload):
        raise ValueError("bad plan")

    registry = ToolRegistry(tmp_path)
    registry.register(_tool("submit_plan", submit_plan))
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("submit_plan", {}, id="c1")]),
        Response(content="recovered"),
    ])
    agent = Agent(provider, registry, stop_after_tools={"submit_plan"})

    result = asyncio.run(agent.run("规划"))

    assert result.status == "done"
    assert result.text == "recovered"
