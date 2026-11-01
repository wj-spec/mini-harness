import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from app import permissions
from app.agent import Agent
from app.config import Config
from app.events import StreamDone, TextDelta
from app.permission_policy import PermissionPolicy, Rule
from app.policy import ApprovalGate
from app.providers.fake import FakeProvider
from app.session import SessionStore
from app.stdio import MAX_FRAME_BYTES, _Connection, _completion_rejected_message, approval_rule_context, serve
from app.tools import ToolRegistry
from core.context import ContextBuilder
from core.models import Response, ToolCall


class Harness:
    def __init__(self, agent, config, session=None):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.events = []

        async def requests():
            while (frame := await self.incoming.get()) is not None:
                yield frame

        def emit(event):
            self.events.append(event)
            self.outgoing.put_nowait(event)

        self.task = asyncio.create_task(serve(
            agent, config, session=session, requests=requests(), emit=emit,
        ))

    def send(self, kind, request_id, **data):
        self.incoming.put_nowait(json.dumps({"v": 1, "type": kind, "id": request_id, **data}))

    async def until(self, kind, request_id=None):
        async with asyncio.timeout(3):
            while True:
                event = await self.outgoing.get()
                if event["type"] == kind and (request_id is None or event["id"] == request_id):
                    return event

    async def close(self):
        self.incoming.put_nowait(None)
        await asyncio.wait_for(self.task, 3)


def agent_for(tmp_path, script):
    return Agent(FakeProvider(script), ToolRegistry(tmp_path), approval=ApprovalGate())


def test_project_background_reloads_before_each_run(tmp_path):
    profile = tmp_path / ".vibe" / "project-profile.json"
    profile.parent.mkdir(parents=True)

    def write_background(text):
        profile.write_text(
            json.dumps({"version": 1, "entries": [{"text": text, "source": "test"}]}),
            encoding="utf-8",
        )

    write_background("背景-A")
    agent = agent_for(tmp_path, ["one", "two"])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path))
        harness.send("run", "r1", text="first")
        await harness.until("done", "r1")
        assert "背景-A" in agent.system

        write_background("背景-B")
        harness.send("run", "r2", text="second")
        await harness.until("done", "r2")
        assert "背景-B" in agent.system
        assert "背景-A" not in agent.system
        await harness.close()

    asyncio.run(scenario())


def test_cancelled_run_records_observed_tool_count(tmp_path, monkeypatch):
    async def fake_run(text, **callbacks):
        callbacks["on_result"](ToolCall("read_file", {"path": "a.txt"}, id="c1"), True, "ok")
        raise asyncio.CancelledError

    agent = agent_for(tmp_path, [])
    monkeypatch.setattr(agent, "run", fake_run)
    session = SessionStore.new(tmp_path, "fake", purpose="test")

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session=session)
        harness.send("run", "r1", text="go")
        done = await harness.until("done", "r1")
        assert done["result"]["status"] == "cancelled"
        await harness.close()

    asyncio.run(scenario())

    events = [json.loads(line) for line in session.trace_path.read_text().splitlines()]
    ending = next(event for event in events if event["type"] == "run_end")
    assert ending["tool_calls"] == 1


def test_stream_read_tool_feedback_and_session_persistence(tmp_path):
    async def scenario():
        (tmp_path / "hello.txt").write_text("hello 编码\n")
        agent = agent_for(tmp_path, [
            Response(content="先读取", tool_calls=[ToolCall("read_file", {"path": "hello.txt"}, "t1")]),
            "已读取 hello 编码",
        ])
        session = SessionStore.new(tmp_path, "fake")
        h = Harness(agent, Config(workspace=tmp_path, model="fake"), session)
        ready = await h.until("ready")
        assert ready["workspace"] == str(tmp_path) and ready["session_id"] == session.id
        h.send("run", "r1", text="读取文件")
        done = await h.until("done", "r1")
        assert done["result"]["status"] == "done"
        assert "hello 编码" in agent.provider.seen[-1][-1].content
        assert any(e["type"] == "text_delta" and e["delta"] for e in h.events)
        result = next(e for e in h.events if e["type"] == "tool_result")
        assert result["ok"] and "hello 编码" in result["output"]
        assert len(session.load_messages()) == 4
        assert session.load_context() == agent.history
        assert session.load_state().goal == "读取文件"
        assert session.latest_run()["status"] == "done"
        assert all(e["v"] == 1 and "id" in e for e in h.events)
        await h.close()

    asyncio.run(scenario())


def test_context_builder_events_are_saved_without_breaking_run(tmp_path):
    async def scenario():
        context = ContextBuilder(max_tokens=8000)
        agent = Agent(FakeProvider(["ok"]), ToolRegistry(tmp_path), context=context)
        session = SessionStore.new(tmp_path, "fake")
        h = Harness(agent, Config(workspace=tmp_path), session)
        h.send("run", "r1", text="上下文")
        done = await h.until("done", "r1")
        assert done["result"]["status"] == "done"
        events = [json.loads(line) for line in session.events_path.read_text().splitlines()]
        assert any(e["type"] == "context" and e["total_tokens"] > 0 for e in events)
        await h.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("decision, exists", [("once", True), ("deny", False)])
def test_approval_is_required_and_one_time(tmp_path, decision, exists):
    async def scenario():
        agent = agent_for(tmp_path, [
            Response(tool_calls=[ToolCall("write_file", {"path": "out.txt", "content": "ok"}, "write1")]),
            "结束",
        ])
        h = Harness(agent, Config(workspace=tmp_path))
        h.send("run", "r1", text="写入")
        request = await h.until("approval_request")
        assert not (tmp_path / "out.txt").exists()
        h.send("approval", "a1", approval_id=request["approval_id"], decision=decision)
        assert (await h.until("ack", "a1"))["action"] == "approval"
        await h.until("done", "r1")
        assert (tmp_path / "out.txt").exists() is exists
        assert any(e["type"] == "approval_resolved" and e["decision"] == decision for e in h.events)
        assert not agent.approval.session_allowed and not agent.approval.workspace_allowed
        h.send("approval", "late", approval_id=request["approval_id"], decision="once")
        assert (await h.until("error", "late"))["code"] == "approval_mismatch"
        await h.close()

    asyncio.run(scenario())


def test_eof_during_approval_cancels_without_write(tmp_path):
    async def scenario():
        agent = agent_for(tmp_path, [Response(tool_calls=[
            ToolCall("write_file", {"path": "out.txt", "content": "no"}, "write1"),
        ])])
        h = Harness(agent, Config(workspace=tmp_path))
        h.send("run", "r1", text="写入")
        await h.until("approval_request")
        await h.close()
        assert not (tmp_path / "out.txt").exists()
        done = [e for e in h.events if e["type"] == "done"]
        assert len(done) == 1 and done[0]["result"]["status"] == "cancelled"
        assert any(e["type"] == "approval_resolved" and e["decision"] == "deny" for e in h.events)

    asyncio.run(scenario())


def test_wrong_approval_id_cannot_release_gate(tmp_path):
    async def scenario():
        h = Harness(agent_for(tmp_path, [Response(tool_calls=[
            ToolCall("write_file", {"path": "out.txt", "content": "no"}, "write1"),
        ])]), Config(workspace=tmp_path))
        h.send("run", "r1", text="写入")
        await h.until("approval_request")
        h.send("approval", "a1", approval_id="another-request", decision="once")
        assert (await h.until("error", "a1"))["code"] == "approval_mismatch"
        assert not (tmp_path / "out.txt").exists()
        h.send("cancel", "c1", target="r1")
        assert (await h.until("done", "r1"))["result"]["status"] == "cancelled"
        await h.close()

    asyncio.run(scenario())


def test_busy_duplicate_ids_and_cancel_target_cannot_affect_next_run(tmp_path):
    class BlockingProvider:
        def __init__(self):
            self.calls = 0

        async def stream(self, messages, tools):
            self.calls += 1
            yield TextDelta("运行中")
            if self.calls == 1:
                await asyncio.Event().wait()
            yield StreamDone()

    async def scenario():
        provider = BlockingProvider()
        h = Harness(Agent(provider, ToolRegistry(tmp_path)), Config(workspace=tmp_path))
        h.send("run", "r1", text="first")
        await h.until("text_delta", "r1")
        h.send("run", "busy", text="second")
        assert (await h.until("error", "busy"))["code"] == "busy"
        h.send("cancel", "wrong", target="missing")
        assert (await h.until("error", "wrong"))["code"] == "run_mismatch"
        h.send("cancel", "c1", target="r1")
        await h.until("ack", "c1")
        assert (await h.until("done", "r1"))["result"]["status"] == "cancelled"
        h.send("run", "r1", text="duplicate")
        assert (await h.until("error", "r1"))["code"] == "duplicate_id"
        h.send("run", "r2", text="second")
        h.send("cancel", "late", target="r1")
        assert (await h.until("error", "late"))["code"] == "run_mismatch"
        assert (await h.until("done", "r2"))["result"]["status"] == "done"
        assert provider.calls == 2
        await h.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("frame", [
    "not json", "[]", '{"v":2,"type":"run","id":"bad","text":"x"}',
    '{"v":1,"type":"run","id":null,"text":"x"}',
    '{"v":1,"type":"run","id":"bad","text":" "}',
    '{"v":1,"type":"approval","id":"bad","approval_id":"x","decision":"forever"}',
    '{"v":1,"type":"run","id":"bad","text":"\\ud800"}',
    '{"v":1,"type":"run","id":"bad","text":"x"}'.encode("utf-16"),
])
def test_invalid_requests_recover_without_execution(tmp_path, frame):
    async def scenario():
        agent = agent_for(tmp_path, ["ok"])
        h = Harness(agent, Config(workspace=tmp_path))
        h.incoming.put_nowait(frame)
        assert (await h.until("error"))["code"] == "invalid_request"
        assert not agent.provider.seen
        h.send("run", "valid", text="继续")
        assert (await h.until("done", "valid"))["result"]["status"] == "done"
        await h.close()

    asyncio.run(scenario())


def test_oversized_frame_recovers_and_shutdown_is_explicit(tmp_path):
    async def scenario():
        h = Harness(agent_for(tmp_path, ["ok"]), Config(workspace=tmp_path))
        h.incoming.put_nowait(b"x" * (MAX_FRAME_BYTES + 1))
        assert (await h.until("error"))["code"] == "frame_too_large"
        h.send("run", "valid", text="继续")
        await h.until("done", "valid")
        h.send("shutdown", "s1")
        assert (await h.until("closed", "s1"))["id"] == "s1"
        await asyncio.wait_for(h.task, 3)

    asyncio.run(scenario())


def test_unexpected_agent_error_has_exactly_one_done(tmp_path):
    async def scenario():
        agent = agent_for(tmp_path, [])

        async def broken(*args, **kwargs):
            raise RuntimeError("backend failed")

        agent.run = broken
        h = Harness(agent, Config(workspace=tmp_path))
        h.send("run", "r1", text="任务")
        assert (await h.until("done", "r1"))["result"]["status"] == "error"
        assert len([e for e in h.events if e["type"] == "done"]) == 1
        await h.close()

    asyncio.run(scenario())


def test_oversized_output_is_explicit_and_closes_connection(tmp_path):
    class LargeProvider:
        async def stream(self, messages, tools):
            yield TextDelta("x" * MAX_FRAME_BYTES)
            yield StreamDone()

    async def scenario():
        h = Harness(Agent(LargeProvider(), ToolRegistry(tmp_path)), Config(workspace=tmp_path))
        h.send("run", "r1", text="输出")
        error = await h.until("error", "r1")
        assert error["code"] == "frame_too_large" and "output" in error["message"]
        await asyncio.wait_for(h.task, 3)
        assert all(len(json.dumps(e, ensure_ascii=False).encode("utf-8")) <= MAX_FRAME_BYTES for e in h.events)
        assert len([e for e in h.events if e["type"] == "done"]) == 1
        assert h.events[-1]["type"] == "closed"

    asyncio.run(scenario())


def test_output_frame_limit_excludes_newline(tmp_path):
    async def scenario():
        events = []
        connection = _Connection(
            agent_for(tmp_path, ["ok"]), Config(workspace=tmp_path),
            SessionStore.new(tmp_path, "fake"), events.append,
        )
        envelope = {"v": 1, "type": "text_delta", "id": "r1", "delta": ""}
        overhead = len(json.dumps(envelope, ensure_ascii=False).encode("utf-8"))
        connection.send("text_delta", "r1", delta="x" * (MAX_FRAME_BYTES - overhead))
        assert events[-1]["type"] == "text_delta"
        assert len(json.dumps(events[-1], ensure_ascii=False).encode("utf-8")) == MAX_FRAME_BYTES
        assert not connection.stop.is_set()
        connection.send("text_delta", "r1", delta="x" * (MAX_FRAME_BYTES - overhead + 1))
        assert events[-1]["type"] == "error" and events[-1]["code"] == "frame_too_large"
        assert connection.stop.is_set()

    asyncio.run(scenario())


def test_oversized_final_result_keeps_small_error_terminal_event(tmp_path):
    class LargeProvider:
        async def stream(self, messages, tools):
            for _ in range(4):
                yield TextDelta("x" * (MAX_FRAME_BYTES // 3))
            yield StreamDone()

    async def scenario():
        h = Harness(Agent(LargeProvider(), ToolRegistry(tmp_path)), Config(workspace=tmp_path))
        h.send("run", "r1", text="输出")
        done = await h.until("done", "r1")
        assert done["result"]["status"] == "error"
        assert "output" in done["result"]["text"]
        await asyncio.wait_for(h.task, 3)
        assert any(e["type"] == "error" and e["code"] == "frame_too_large" for e in h.events)

    asyncio.run(scenario())


def test_real_stdin_frames_stdout_json_and_eof_cleanup(tmp_path):
    code = """
from pathlib import Path
import sys
from app.agent import Agent
from app.config import Config
from app.permission_policy import PermissionPolicy, Rule
from app.policy import ApprovalGate
from app.providers.fake import FakeProvider
from app.stdio import run_stdio
from app.tools import ToolRegistry
root = Path(sys.argv[1])
class NoisyProvider(FakeProvider):
    async def stream(self, messages, tools):
        print('provider diagnostic')
        async for event in super().stream(messages, tools):
            yield event
run_stdio(Agent(NoisyProvider(['hello']), ToolRegistry(root), approval=ApprovalGate()), Config(workspace=root))
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    try:
        assert json.loads(proc.stdout.readline())["type"] == "ready"
        proc.stdin.write("x" * (MAX_FRAME_BYTES + 2) + "\n")
        proc.stdin.write(json.dumps({"v": 1, "type": "run", "id": "r1", "text": "hello"}) + "\n")
        proc.stdin.flush()
        events = []
        while not any(e["type"] == "done" for e in events):
            events.append(json.loads(proc.stdout.readline()))
        assert any(e["type"] == "error" and e["code"] == "frame_too_large" for e in events)
        assert any(e["type"] == "text_delta" for e in events)
        proc.stdin.close()
        proc.wait(timeout=3)
        rest = [json.loads(line) for line in proc.stdout]
        assert any(e["type"] == "closed" for e in rest)
        assert proc.returncode == 0 and proc.stderr.read() == "provider diagnostic\n"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.mark.parametrize("close_mode", ["eof", "shutdown", "signal", "hup"])
def test_real_process_approval_wait_closes_without_writing(tmp_path, close_mode):
    code = """
from pathlib import Path
import sys
from app.agent import Agent
from app.config import Config
from app.permission_policy import PermissionPolicy, Rule
from app.policy import ApprovalGate
from app.providers.fake import FakeProvider
from app.stdio import run_stdio
from app.tools import ToolRegistry
from core.models import Response, ToolCall
root = Path(sys.argv[1])
provider = FakeProvider([Response(tool_calls=[ToolCall('write_file', {'path':'out.txt','content':'no'}, 'write1')])])
run_stdio(Agent(provider, ToolRegistry(root), approval=ApprovalGate()), Config(workspace=root))
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    try:
        assert json.loads(proc.stdout.readline())["type"] == "ready"
        proc.stdin.write('{"v":1,"type":"run","id":"r1","text":"write"}\n')
        proc.stdin.flush()
        while json.loads(proc.stdout.readline())["type"] != "approval_request":
            pass
        if close_mode == "eof":
            proc.stdin.close()
        elif close_mode == "shutdown":
            proc.stdin.write('{"v":1,"type":"shutdown","id":"s1"}\n')
            proc.stdin.flush()
        elif close_mode == "signal":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGHUP)
        proc.wait(timeout=3)
        events = [json.loads(line) for line in proc.stdout]
        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1 and done[0]["result"]["status"] == "cancelled"
        assert events[-1]["type"] == "closed"
        assert not (tmp_path / "out.txt").exists()
        assert proc.returncode == 0 and not proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_ready_and_skills_command_expose_structured_catalog(tmp_path):
    skill = tmp_path / ".vibe" / "skills" / "alpha" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("name: alpha\ndescription: 第一个\n\n步骤 A\n", encoding="utf-8")
    agent = agent_for(tmp_path, [])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path))
        ready = await harness.until("ready")
        assert ready["skills"] == [{"name": "alpha", "summary": "第一个", "active": False,
                                    "source": str(skill)}]
        harness.send("command", "c1", command="skills")
        result = await harness.until("command_result", "c1")
        assert "skills: 1 registered" in result["text"]
        assert result["skills"][0]["name"] == "alpha"
        harness.send("command", "c2", command="skills", args={"action": "activate", "name": "alpha"})
        activated = await harness.until("command_result", "c2")
        assert activated["skills"][0]["active"] is True
        await harness.close()

    asyncio.run(scenario())


def test_ready_exposes_autonomy_and_label(tmp_path):
    async def scenario():
        agent = Agent(
            FakeProvider([]), ToolRegistry(tmp_path), approval=ApprovalGate(), autonomy="edits",
        )
        harness = Harness(agent, Config(workspace=tmp_path))
        ready = await harness.until("ready")
        assert ready["autonomy"] == "edits"
        assert ready["autonomy_label"] == permissions.AUTONOMY_LABELS["edits"]
        await harness.close()

    asyncio.run(scenario())


def test_autonomy_command_switches_level_and_reports_effective(tmp_path):
    async def scenario():
        agent = agent_for(tmp_path, [])
        harness = Harness(agent, Config(workspace=tmp_path))
        await harness.until("ready")
        harness.send("command", "c1", command="autonomy", args={"level": "edits"})
        result = await harness.until("command_result", "c1")
        assert result["command"] == "autonomy"
        assert result["autonomy"] == "edits"
        assert agent.autonomy == "edits"
        assert agent._cli_autonomy == "edits"
        assert agent.approval.autonomy == "edits"
        assert "授权档位已切换：edits" in result["text"]
        assert permissions.AUTONOMY_LABELS["edits"] in result["text"]
        await harness.close()

    asyncio.run(scenario())


def test_autonomy_command_respects_project_cap(tmp_path):
    policy_file = tmp_path / ".vibe" / "permissions.json"
    policy_file.parent.mkdir(parents=True)
    policy_file.write_text(
        json.dumps({"version": 1, "autonomy": "ask", "rules": []}), encoding="utf-8",
    )

    async def scenario():
        agent = agent_for(tmp_path, [])
        assert agent.permission_policy.autonomy_cap == "ask"
        harness = Harness(agent, Config(workspace=tmp_path))
        await harness.until("ready")
        harness.send("command", "c1", command="autonomy", args={"level": "full"})
        result = await harness.until("command_result", "c1")
        assert result["autonomy"] == "ask"
        assert agent.autonomy == "ask"
        assert "项目上限" in result["text"]
        await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("frame", [
    '{"v":1,"type":"command","id":"bad","command":"autonomy"}',
    '{"v":1,"type":"command","id":"bad","command":"autonomy","args":null}',
    '{"v":1,"type":"command","id":"bad","command":"autonomy","args":{}}',
    '{"v":1,"type":"command","id":"bad","command":"autonomy","args":{"level":"yolo"}}',
    '{"v":1,"type":"command","id":"bad","command":"autonomy","args":{"level":"edits","extra":1}}',
    '{"v":1,"type":"command","id":"bad","command":"autonomy","args":{"level":1}}',
])
def test_invalid_autonomy_args_are_rejected(tmp_path, frame):
    async def scenario():
        agent = agent_for(tmp_path, [])
        initial = agent.autonomy
        harness = Harness(agent, Config(workspace=tmp_path))
        harness.incoming.put_nowait(frame)
        assert (await harness.until("error"))["code"] == "invalid_request"
        assert agent.autonomy == initial
        await harness.close()

    asyncio.run(scenario())


def test_evidence_command_without_rounds(tmp_path):
    async def scenario():
        harness = Harness(agent_for(tmp_path, []), Config(workspace=tmp_path))
        harness.send("command", "c1", command="evidence")
        result = await harness.until("command_result", "c1")
        assert result["command"] == "evidence"
        assert result["text"] == "当前会话还没有轮次记录。完成一次任务后再查看。"
        await harness.close()

    asyncio.run(scenario())


async def _run_approving_once(harness, request_id, text):
    """跑一次任务：stdio 会接管 approval.handler，因此按协议批准一次。"""
    harness.send("run", request_id, text=text)
    approval = await harness.until("approval_request")
    harness.send("approval", f"a-{request_id}", approval_id=approval["approval_id"],
                 decision="once")
    await harness.until("ack", f"a-{request_id}")
    return await harness.until("done", request_id)


def test_evidence_command_renders_latest_round(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    provider = FakeProvider([
        Response(tool_calls=[ToolCall(
            "write_file", {"path": "notes.txt", "content": "new"}, id="w1",
        )]),
        "done without verification",
        "still done",
    ])
    agent = Agent(
        provider, ToolRegistry(tmp_path), approval=ApprovalGate(),
        completion_guard=True, workspace_audit=True, on_round=session.save_round,
    )

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session=session)
        await harness.until("ready")
        done = await _run_approving_once(harness, "r1", "修改 notes.txt")
        assert done["result"]["verified"] is False
        harness.send("command", "c1", command="evidence")
        result = await harness.until("command_result", "c1")
        text = result["text"]
        assert "最近一轮证据" in text
        assert "完成判定" in text and "verified=false" in text
        assert "环境审计" in text and "coverage=limited" in text
        assert "notes.txt" in text
        await harness.close()

    asyncio.run(scenario())


def test_permissions_text_lists_policy_rules(tmp_path):
    policy_file = tmp_path / ".vibe" / "permissions.json"
    policy_file.parent.mkdir(parents=True)
    policy_file.write_text(json.dumps({
        "version": 1,
        "rules": [{"tool": "bash", "command_prefix": "rm", "decision": "once",
                   "note": "删除逐次确认"}],
    }, ensure_ascii=False), encoding="utf-8")

    async def scenario():
        agent = agent_for(tmp_path, [])
        harness = Harness(agent, Config(workspace=tmp_path))
        await harness.until("ready")
        harness.send("command", "c1", command="permissions")
        result = await harness.until("command_result", "c1")
        text = result["text"]
        assert "策略规则明细" in text
        assert "- [project] bash prefix=rm → once（删除逐次确认）" in text
        assert result["autonomy"] == agent.autonomy
        harness.send("command", "c2", command="permissions_reset")
        reset = await harness.until("command_result", "c2")
        assert reset["autonomy"] == agent.autonomy
        await harness.close()

    asyncio.run(scenario())


def test_completion_rejected_emits_notice_with_guard_reason(tmp_path):
    provider = FakeProvider([
        Response(tool_calls=[ToolCall(
            "write_file", {"path": "a.txt", "content": "new"}, id="w1",
        )]),
        "done without verification",
        "still done",
    ])
    agent = Agent(
        provider, ToolRegistry(tmp_path), approval=ApprovalGate(),
        completion_guard=True, workspace_audit=False,
    )

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path))
        done = await _run_approving_once(harness, "r1", "修改 a.txt")
        assert done["result"]["verified"] is False
        notices = [event for event in harness.events
                   if event["type"] == "notice" and "完成守卫" in event["message"]]
        assert len(notices) == 1
        assert notices[0]["id"] is None
        assert "有文件改动但没有验证命令" in notices[0]["message"]
        assert "已要求模型补验证" in notices[0]["message"]
        await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("code,explanation", [
    ("no_verification", "有文件改动但没有验证命令"),
    ("mutation_after_verification", "最后一次改动发生在验证之后"),
    ("last_verification_failed", "最后一次验证命令失败"),
])
def test_completion_rejected_notice_explains_codes(code, explanation):
    message = _completion_rejected_message({"codes": [code]})
    assert message.startswith("完成守卫：")
    assert explanation in message
    assert "已要求模型补验证" in message


def test_completion_rejected_notice_keeps_unknown_code():
    message = _completion_rejected_message({"codes": ["no_verification", "future_code"]})
    assert "有文件改动但没有验证命令" in message
    assert "future_code" in message


def test_diff_command_approval_preview_includes_tier(tmp_path):
    async def scenario():
        agent = agent_for(tmp_path, [])
        harness = Harness(agent, Config(workspace=tmp_path))
        harness.send("command", "c1", command="diff")
        request = await harness.until("approval_request")
        assert request["call"]["name"] == "bash"
        assert "[审批层] T2 本地执行" in request["preview"]
        harness.send("approval", "a1", approval_id=request["approval_id"], decision="deny")
        result = await harness.until("command_result", "c1")
        assert "未获授权" in result["text"]
        await harness.close()

    asyncio.run(scenario())


def test_approval_rule_context_reports_tier_and_matched_rule(tmp_path):
    agent = agent_for(tmp_path, [])
    agent.permission_policy = PermissionPolicy((
        Rule(source="user", tool="bash", decision="once",
             command_prefix=("pytest",), note="本地测试"),
    ))
    matched = approval_rule_context(agent, ToolCall("bash", {"command": "pytest -q"}))
    assert matched["tier"] == "exec"
    assert matched["tier_label"] == "T2 本地执行"
    assert matched["rule"]["source"] == "user"
    assert matched["rule"]["decision"] == "once"
    assert matched["rule"]["note"] == "本地测试"

    unmatched = approval_rule_context(agent, ToolCall("read_file", {"path": "a.txt"}))
    assert unmatched["tier"] == "read"
    assert "rule" not in unmatched
