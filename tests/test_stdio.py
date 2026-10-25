import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent import Agent
from app.config import Config
from app.events import StreamDone, TextDelta
from app.policy import ApprovalGate
from app.providers.fake import FakeProvider
from app.session import SessionStore
from app.stdio import MAX_FRAME_BYTES, _Connection, serve
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
