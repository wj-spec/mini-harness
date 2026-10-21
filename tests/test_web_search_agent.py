import asyncio
import json

import pytest

from app.agent import Agent
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry
from app.web_search import web_search
from core.models import Response, ToolCall


async def allow_once(call, preview):
    return ApprovalDecision(True, "once")


def search_provider(args=None):
    return FakeProvider([
        Response(tool_calls=[ToolCall("web_search", args or {"query": "Python 文档"}, id="search-1")]),
        Response(content="参考 https://docs.python.org/3/"),
    ])


def test_search_approval_results_and_schema_reach_model(tmp_path):
    registry = ToolRegistry(tmp_path)
    spec = registry._tools["web_search"]
    assert spec.fn is web_search
    assert spec.timeout == 25
    assert spec.risk == "network" and not spec.retryable
    searched, approvals = [], []
    output = json.dumps({"results": [{"url": "https://docs.python.org/3/", "title": "Python"}]})

    async def search(**args):
        searched.append(args)
        return output

    async def approve(call, preview):
        assert searched == []
        approvals.append((call, preview))
        return ApprovalDecision(True, "once")

    registry._tools["web_search"].fn = search
    provider = search_provider()
    agent = Agent(provider, registry, approval=ApprovalGate(approve))

    result = asyncio.run(agent.run("检索 Python 文档"))

    assert result.status == "done" and result.steps == 2
    assert searched == [{"query": "Python 文档"}]
    schema = next(tool["function"] for tool in provider.seen_tools[0]
                  if tool["function"]["name"] == "web_search")
    assert schema["parameters"]["required"] == ["query"]
    assert set(schema["parameters"]["properties"]) == {"query", "max_results", "region", "timelimit"}
    system = "\n".join(m.content for m in provider.seen[0] if m.role == "system")
    assert "web_search" in system and "untrusted" in system.lower()
    assert "cite source urls" in system.lower()
    assert len(approvals) == 1 and approvals[0][0].id == "search-1"
    preview = approvals[0][1]
    assert "Python 文档" in preview and "backend=auto" in preview
    assert "network" in preview and "外部搜索引擎" in preview
    history = [m for m in provider.seen[1] if m.role != "system"]
    assert [m.role for m in history] == ["user", "assistant", "tool"]
    assert history[1].tool_calls[0].id == history[2].tool_call_id == "search-1"
    assert history[2].content == output


@pytest.mark.parametrize("explicit_deny", [False, True])
def test_search_requires_approval_and_respects_denial(tmp_path, explicit_deny):
    searched = []

    async def search(**args):
        searched.append(args)
        return "unexpected search"

    async def deny(call, preview):
        return ApprovalDecision(False, "deny")

    registry = ToolRegistry(tmp_path)
    registry._tools["web_search"].fn = search
    provider = search_provider()
    gate = ApprovalGate(deny) if explicit_deny else None
    agent = Agent(provider, registry, approval=gate)

    result = asyncio.run(agent.run("检索"))

    assert result.status == "done" and searched == []
    message = next(m for m in provider.seen[1] if m.role == "tool")
    assert message.tool_call_id == "search-1"
    assert ("denied_by_user" if explicit_deny else "approval_required") in message.content


def test_workspace_grant_does_not_authorize_search(tmp_path):
    approvals = []

    async def handler(call, preview):
        approvals.append(call.name)
        return ApprovalDecision(call.name == "bash", "workspace" if call.name == "bash" else "deny")

    gate = ApprovalGate(handler)
    assert asyncio.run(gate.request(ToolCall("bash", {"command": "pwd"}), "p", tmp_path)).allowed
    assert str(tmp_path.resolve()) in gate.workspace_allowed
    call = ToolCall("web_search", {"query": "Python 文档"})

    decision = asyncio.run(gate.request(call, ToolRegistry(tmp_path).preview(call), tmp_path))

    assert not decision.allowed
    assert approvals == ["bash", "web_search"]


def test_search_cannot_create_workspace_grant(tmp_path):
    async def handler(call, preview):
        return ApprovalDecision(True, "workspace")

    gate = ApprovalGate(handler)
    call = ToolCall("web_search", {"query": "Python 文档"})

    decision = asyncio.run(gate.request(call, ToolRegistry(tmp_path).preview(call), tmp_path))

    assert not decision.allowed
    assert not gate.workspace_allowed and not gate.session_allowed


@pytest.mark.parametrize("changed", [
    {"query": "Textual 文档"}, {"max_results": 3}, {"region": "cn-zh"}, {"timelimit": "w"},
])
def test_search_session_approval_requires_identical_parameters(tmp_path, changed):
    approvals = []

    async def handler(call, preview):
        approvals.append(call.args)
        return ApprovalDecision(True, "session")

    gate = ApprovalGate(handler)
    registry = ToolRegistry(tmp_path)
    args = {"query": "Python 文档", "max_results": 5, "region": "us-en", "timelimit": None}
    first = ToolCall("web_search", args)
    same = ToolCall("web_search", dict(reversed(list(args.items()))))
    different = ToolCall("web_search", {**args, **changed})
    for call in (first, same):
        assert asyncio.run(gate.request(call, registry.preview(call), tmp_path)).allowed
    assert len(approvals) == 1

    assert asyncio.run(gate.request(different, registry.preview(different), tmp_path)).allowed
    assert len(approvals) == 2


def test_cancel_during_search_approval_never_starts_network(tmp_path):
    async def scenario():
        approving, approval_closed, cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()
        searched = []

        async def search(**args):
            searched.append(args)
            return "unexpected search"

        async def approve(call, preview):
            approving.set()
            try:
                await asyncio.Event().wait()
                return ApprovalDecision(True, "once")
            finally:
                approval_closed.set()

        registry = ToolRegistry(tmp_path)
        registry._tools["web_search"].fn = search
        provider = search_provider()
        agent = Agent(provider, registry, approval=ApprovalGate(approve))
        task = asyncio.create_task(agent.run("检索", cancel=cancel))
        await asyncio.wait_for(approving.wait(), 1)
        cancel.set()
        result = await asyncio.wait_for(task, 1)

        assert result.status == "cancelled" and searched == []
        assert approval_closed.is_set() and len(provider.seen) == 1
        message = next(m for m in agent.history if m.role == "tool")
        assert message.tool_call_id == "search-1" and "not executed" in message.content

    asyncio.run(scenario())


def test_cancel_running_search_cleans_up_before_agent_returns(tmp_path):
    async def scenario():
        started, cleaned, cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def search(**args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        registry = ToolRegistry(tmp_path)
        registry._tools["web_search"].fn = search
        provider = search_provider()
        agent = Agent(provider, registry, approval=ApprovalGate(allow_once))
        task = asyncio.create_task(agent.run("检索", cancel=cancel))
        await asyncio.wait_for(started.wait(), 1)
        cancel.set()
        result = await asyncio.wait_for(task, 1)

        assert result.status == "cancelled" and cleaned.is_set()
        assert len(provider.seen) == 1
        message = next(m for m in agent.history if m.role == "tool")
        assert message.tool_call_id == "search-1" and "cancelled" in message.content

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["ratelimit", "timeout", "deadline"])
def test_search_errors_are_returned_without_automatic_retry(tmp_path, failure):
    attempts, results = [], []

    async def search(**args):
        attempts.append(args)
        if failure == "ratelimit":
            raise RuntimeError("RatelimitException: throttled")
        if failure == "timeout":
            raise TimeoutError("search backend timed out")
        await asyncio.Event().wait()

    registry = ToolRegistry(tmp_path)
    registry._tools["web_search"].fn = search
    if failure == "deadline":
        registry._tools["web_search"].timeout = 0.01
    provider = search_provider()
    agent = Agent(provider, registry, approval=ApprovalGate(allow_once), tool_retries=3)

    result = asyncio.run(agent.run("检索", on_result=lambda call, ok, output: results.append((ok, output))))

    assert result.status == "done" and len(attempts) == 1
    message = next(m for m in provider.seen[1] if m.role == "tool")
    assert message.tool_call_id == "search-1" and message.content.startswith("ERROR")
    assert ("throttled" if failure == "ratelimit" else "timeout") in message.content.lower()
    assert results == [(False, message.content)]
