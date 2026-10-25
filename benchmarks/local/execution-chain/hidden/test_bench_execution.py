import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_stdio import Harness  # noqa: E402

from app.agent import Agent  # noqa: E402
from app.config import Config  # noqa: E402
from app.policy import ApprovalDecision, ApprovalGate  # noqa: E402
from app.providers.fake import FakeProvider  # noqa: E402
from app.tools import ToolRegistry  # noqa: E402
from core.models import Response, ToolCall  # noqa: E402


def _agent(tmp_path):
    async def allow(call, preview):
        return ApprovalDecision(True, "workspace")

    provider = FakeProvider([
        Response(tool_calls=[ToolCall("bash", {"command": "exit 2", "verification": True}, id="c1")]),
        "done",
    ])
    return Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(allow))


def test_bench_agent_tool_message_keeps_execution(tmp_path):
    agent = _agent(tmp_path)
    result = asyncio.run(agent.run("run"))
    tools = [m for m in agent.history if m.role == "tool"]
    assert tools and tools[0].execution == {"status": "failed", "exit_code": 2}
    assert agent.state.command_results[-1]["exit_code"] == 2
    assert "exit=2" in result.receipt


def test_bench_stdio_tool_result_carries_execution(tmp_path):
    agent = _agent(tmp_path)

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path))
        harness.send("run", "r1", text="go")
        request = await harness.until("approval_request")
        harness.send("approval", "a1", approval_id=request["approval_id"], decision="once")
        await harness.until("done", "r1")
        await harness.close()
        return harness

    harness = asyncio.run(scenario())
    results = [event for event in harness.events if event["type"] == "tool_result"]
    assert results and results[0].get("execution") == {"status": "failed", "exit_code": 2}
