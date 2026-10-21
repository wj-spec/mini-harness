import asyncio
import json
import re

import pytest

from app.agent import Agent
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.session import SessionStore
from core.context import ContextBuilder
from core.models import Message, Response, ToolCall


def test_agent_message_persists_execution_and_plain_output(tmp_path):
    session = SessionStore.new(tmp_path, "fake")

    async def approve(call, preview):
        return ApprovalDecision(True, "once")

    from app.tools import ToolRegistry
    agent = Agent(FakeProvider([
        Response(tool_calls=[ToolCall("bash", {"command": "echo actual; exit 7"}, id="c")]), "done",
    ]), ToolRegistry(tmp_path), approval=ApprovalGate(approve))
    asyncio.run(agent.run("check", on_message=session.append_message))
    original = next(message for message in agent.history if message.role == "tool")
    restored = next(message for message in session.load_messages() if message.role == "tool")
    assert type(original.content) is str
    assert restored.execution == original.execution == {"status": "failed", "exit_code": 7}
    assert restored.content == original.content and "actual" in restored.content


@pytest.mark.parametrize("status,exit_code", [
    ("succeeded", 0), ("failed", 3), ("timed_out", -9), ("cancelled", 0), ("unknown", None),
])
def test_checkpoint_retains_execution_metadata(tmp_path, status, exit_code):
    session = SessionStore.new(tmp_path, "fake")
    execution = {"status": status, "exit_code": exit_code}
    tool = Message("tool", "actual output", name="bash", tool_call_id="c", execution=execution)
    session.append_message(Message("user", "check"))
    session.append_message(tool)
    session.save_context([Message("assistant", "[history summary L1]"), tool])
    restored = session.load_context()
    assert restored[-1].execution == execution
    session.append_message(Message("user", "continue"))
    assert session.load_context()[-2].execution == execution


def test_compaction_checkpoint_retains_execution_in_disk_archive(tmp_path):
    execution = {"status": "failed", "exit_code": 1}
    tool = Message("tool", "failed test " * 100, name="bash", tool_call_id="c", execution=execution)
    messages = [Message("user", "fix tests"),
                Message("assistant", "", [ToolCall("bash", {"command": "pytest"}, id="c")]),
                tool, Message("assistant", "recent")]
    session = SessionStore.new(tmp_path, "fake")
    for message in messages:
        session.append_message(message)
    archive_dir = session.root / "offload"
    result = ContextBuilder(offload_dir=archive_dir).compact_history(
        messages, history_budget=200, keep_recent_groups=1,
    )
    assert result.summarized
    session.save_context(result.messages)
    restored = SessionStore(session.root).load_context()
    refs = re.findall(r"\[offloaded: ([^\]]+)\]", "\n".join(message.content for message in restored))
    assert refs
    archived = ContextBuilder(offload_dir=archive_dir).resolve(refs[-1])
    assert next(message for message in archived if message.role == "tool").execution == execution


@pytest.mark.parametrize("execution", [
    {}, [], "succeeded", {"status": "done", "exit_code": 0},
    {"status": "succeeded", "exit_code": True}, {"status": "failed", "exit_code": 0},
    {"status": "succeeded", "exit_code": 1}, {"status": "failed", "exit_code": None},
    {"status": "unknown", "exit_code": 1}, {"status": "failed", "exit_code": 1.0},
    {"status": "failed", "exit_code": "1"}, {"status": "failed", "exit_code": 2**60},
    {"status": "succeeded", "exit_code": 0, "trusted": True},
])
def test_invalid_execution_metadata_cannot_replace_checkpoint(tmp_path, execution):
    session = SessionStore.new(tmp_path, "fake")
    session.append_message(Message("user", "original"))
    invalid = {"role": "tool", "content": "invalid", "name": "bash", "execution": execution}
    session.append_event("message", message=invalid)
    session.append_event("context_checkpoint", version=1, source_message_count=1, messages=[invalid])
    assert [message.content for message in session.load_context()] == ["original"]
    assert [message.content for message in session.load_messages()] == ["original"]


def test_non_tool_message_cannot_claim_execution_evidence(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    session.append_event("message", message={"role": "assistant", "content": "claimed passed",
                         "execution": {"status": "succeeded", "exit_code": 0}})
    assert session.load_messages() == []


def test_legacy_output_is_not_promoted_to_execution_metadata(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    session.append_event("message", message={"role": "tool", "name": "bash", "content": "exit=0 passed"})
    assert session.load_messages()[0].execution is None
    session.save_context(session.load_messages())
    assert session.load_context()[0].execution is None


def test_execution_metadata_is_not_sent_as_provider_field():
    from app.providers.openai_compat import _to_openai
    message = Message("tool", "exit=1", name="bash", tool_call_id="c",
                      execution={"status": "failed", "exit_code": 1})
    data = _to_openai(message)
    assert data == {"role": "tool", "content": "exit=1", "name": "bash", "tool_call_id": "c"}
    assert "execution" not in json.dumps(data)
