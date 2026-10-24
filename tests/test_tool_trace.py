import asyncio

from app.agent import Agent
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.trace import TraceRecorder
from app.tools import ToolRegistry
from core.models import Response, ToolCall


def run(coro):
    return asyncio.run(coro)


def test_tool_spans_link_run_step_and_call_id(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n")
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("read_file", {"path": "a.txt"}, id="c1")]),
        "done",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path))
    events = []
    result = run(agent.run("读一下", trace=events.append))
    assert result.status == "done"
    starts = [e for e in events if e["type"] == "tool_start"]
    ends = [e for e in events if e["type"] == "tool_end"]
    assert [e["tool_call_id"] for e in starts] == ["c1"]
    assert [e["tool_call_id"] for e in ends] == ["c1"]
    for event in starts + ends:
        assert event["run_id"] == result.run_id
        assert event["step"] == 1
    assert ends[0]["status"] == "succeeded"
    assert isinstance(ends[0]["duration_ms"], float)
    assert TraceRecorder.anomaly(events) is None


def test_denied_tool_records_approval_and_terminal_state(tmp_path):
    async def deny(call, preview):
        return ApprovalDecision(False, "deny")

    provider = FakeProvider([
        Response(tool_calls=[ToolCall("write_file", {"path": "x.txt", "content": "y"}, id="c1")]),
        "已说明阻碍",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(deny))
    events = []
    result = run(agent.run("创建 x.txt", trace=events.append))
    assert result.status == "done"
    approvals = [e for e in events if e["type"] == "approval"]
    assert approvals and approvals[0]["tool_call_id"] == "c1" and approvals[0]["allowed"] is False
    ends = [e for e in events if e["type"] == "tool_end"]
    assert ends and ends[0]["status"] == "denied" and "denied" in ends[0]["error"]
    anomaly = TraceRecorder.anomaly(events)
    assert anomaly and anomaly["status"] == "denied"


def test_cancelled_batch_gives_every_call_a_terminal_state(tmp_path):
    cancel = asyncio.Event()

    async def block_then_cancel(call, preview):
        cancel.set()
        await asyncio.sleep(0.01)
        return ApprovalDecision(True, "once")

    provider = FakeProvider([
        Response(tool_calls=[
            ToolCall("write_file", {"path": "a.txt", "content": "1"}, id="c1"),
            ToolCall("write_file", {"path": "b.txt", "content": "2"}, id="c2"),
        ]),
        "x",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(block_then_cancel))
    events = []
    result = run(agent.run("写两个文件", trace=events.append, cancel=cancel))
    assert result.status == "cancelled"
    terminals = [e for e in events if e["type"] in ("tool_end", "tool_not_executed")]
    assert {e["tool_call_id"] for e in terminals} == {"c1", "c2"}
    for event in terminals:
        assert event.get("status") in ("cancelled", "not_executed") or event["type"] == "tool_not_executed"


def test_timeout_marks_command_terminal_state(tmp_path):
    async def allow(call, preview):
        return ApprovalDecision(True, "workspace")

    provider = FakeProvider([
        Response(tool_calls=[ToolCall("bash", {"command": "sleep 2", "timeout": 0.1}, id="c1")]),
        "超时说明",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(allow))
    events = []
    result = run(agent.run("运行命令", trace=events.append))
    ends = [e for e in events if e["type"] == "tool_end"]
    assert ends and ends[0]["status"] == "timed_out"
    assert result.status == "done"


def test_anomaly_locator_finds_missing_and_unexecuted():
    clean = [
        {"type": "tool_start", "tool_call_id": "c1", "tool": "read_file", "step": 1},
        {"type": "tool_end", "tool_call_id": "c1", "ok": True, "status": "succeeded"},
    ]
    assert TraceRecorder.anomaly(clean) is None
    missing = TraceRecorder.anomaly([{"type": "tool_start", "tool_call_id": "c1", "tool": "bash", "step": 2}])
    assert missing and missing["type"] == "missing_tool_end" and missing["tool_call_id"] == "c1"
    unexecuted = TraceRecorder.anomaly([
        {"type": "tool_start", "tool_call_id": "c1", "tool": "bash", "step": 1},
        {"type": "tool_not_executed", "tool_call_id": "c1", "tool": "bash", "reason": "cancelled"},
    ])
    assert unexecuted and unexecuted["type"] == "tool_not_executed"
