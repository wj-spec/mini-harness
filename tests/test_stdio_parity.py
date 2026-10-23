import asyncio
import json
import os
import shlex
import sys
from types import SimpleNamespace

import pytest

from app.agent import Agent
from app.events import StreamDone, TextDelta, ToolCallsReady
from app.config import Config
from app.mcp import McpManager
from app.policy import ApprovalGate
from app.providers.fake import FakeProvider
from app.session import SessionStore
from app.stdio import MAX_FRAME_BYTES, serve
from app.tools import ToolRegistry, ToolSpec
from core.context import ContextBuilder
from core.models import Message, Response, ToolCall
from core.state import TaskState


class Connection:
    def __init__(self, agent, root, session=None, **options):
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
            agent, Config(workspace=root), session=session, requests=requests(), emit=emit,
            **options,
        ))

    def send(self, kind, request_id, **data):
        self.incoming.put_nowait(json.dumps({"v": 1, "type": kind, "id": request_id, **data}))

    async def until(self, kind, request_id=None, timeout=3):
        async with asyncio.timeout(timeout):
            while True:
                event = await self.outgoing.get()
                if event["type"] == kind and (request_id is None or event["id"] == request_id):
                    return event

    async def close(self):
        self.incoming.put_nowait(None)
        await asyncio.wait_for(self.task, 3)


def make_agent(root, script=(), history=None, state=None):
    return Agent(FakeProvider(list(script)), ToolRegistry(root, state=state),
                 approval=ApprovalGate(), history=history, state=state)


def test_restored_transcript_keeps_originals_while_model_resumes_from_checkpoint(tmp_path):
    async def scenario():
        session = SessionStore.new(tmp_path, "fake")
        goal = Message("user", "实现接口")
        summary = Message("assistant", "已定位入口，未修改。", name="history_summary_l2")
        session.append_message(goal)
        session.append_message(Message("assistant", "很早的原文"))
        session.save_context([goal, summary])
        session.append_trace({"type": "run_end", "status": "stopped", "stop_reason": "token_budget"})
        state = TaskState(goal="实现接口", done=["调研"], open=["实现"])
        session.save_state(state)
        agent = make_agent(tmp_path, ["完成"], session.load_context(), state)
        c = Connection(agent, tmp_path, session, resumed=True)
        assert (await c.until("ready"))["resumed"] is True
        await c.until("status")
        restored = [event["message"] for event in c.events if event["type"] == "history_message"]
        assert [m["content"] for m in restored] == [goal.content, "很早的原文"]
        assert agent.history == [goal, summary]
        assert not agent.provider.seen
        notices = "\n".join(e["message"] for e in c.events if e["type"] == "notice")
        assert all(word in notices for word in ("实现接口", "调研", "实现", "token_budget", "/resume"))
        c.send("command", "resume1", command="resume")
        assert (await c.until("done", "resume1"))["result"]["status"] == "done"
        request = agent.provider.seen[0]
        assert summary in request
        assert not any(m.content == "很早的原文" for m in request)
        assert "先核对" in request[-1].content and "结果未知" in request[-1].content
        assert c.events[-2]["type"] == "done"
        await c.close()

    asyncio.run(scenario())


def test_restore_unknown_tool_never_replays_without_explicit_resume(tmp_path):
    async def scenario():
        session = SessionStore.new(tmp_path, "fake")
        session.append_message(Message("user", "创建文件"))
        session.append_message(Message("assistant", tool_calls=[
            ToolCall("write_file", {"path": "out.txt", "content": "x"}, "write1"),
        ]))
        session.repair_incomplete_tool_calls()
        agent = make_agent(tmp_path, ["先核对文件"], session.load_context())
        c = Connection(agent, tmp_path, session, resumed=True)
        await c.until("status")
        assert not agent.provider.seen and not (tmp_path / "out.txt").exists()
        assert any(e["type"] == "notice" and "结果未知" in e["message"] for e in c.events)
        await c.close()

    asyncio.run(scenario())


def test_large_restored_message_is_explicitly_shortened_only_for_display(tmp_path):
    async def scenario():
        original = Message("user", "汉" * MAX_FRAME_BYTES)
        session = SessionStore.new(tmp_path, "fake")
        session.append_message(original)
        agent = make_agent(tmp_path, history=[original])
        c = Connection(agent, tmp_path, session, resumed=True)
        await c.until("status")
        assert not any(e["type"] == "error" for e in c.events)
        assert any(e["type"] == "notice" and "截断" in e["message"] for e in c.events)
        assert all(len(json.dumps(e, ensure_ascii=False).encode()) <= MAX_FRAME_BYTES for e in c.events)
        assert agent.history == [original]
        await c.close()

    asyncio.run(scenario())


def test_resume_without_history_has_terminal_done(tmp_path):
    async def scenario():
        agent = make_agent(tmp_path)
        c = Connection(agent, tmp_path)
        c.send("command", "resume1", command="resume")
        done = await c.until("done", "resume1")
        assert done["result"]["status"] == "error"
        assert "没有可继续" in done["result"]["text"]
        assert not agent.provider.seen
        await c.close()

    asyncio.run(scenario())


def test_resume_starts_fresh_budget_but_keeps_session_cost(tmp_path):
    async def scenario():
        session = SessionStore.new(tmp_path, "fake")
        session.append_message(Message("user", "暂停任务"))
        session.append_trace({"type": "run_end", "status": "stopped", "stop_reason": "token_budget",
                              "total_tokens": 1000000})
        agent = make_agent(tmp_path, [Response(content="done", usage={"total_tokens": 7})],
                           history=session.load_context())
        agent.max_run_tokens = 1000000
        c = Connection(agent, tmp_path, session, resumed=True)
        await c.until("status")
        c.send("command", "resume1", command="resume")
        done = await c.until("done", "resume1")
        assert done["result"]["usage"]["total_tokens"] == 7
        metrics = [e for e in c.events if e["type"] == "metrics" and e["id"] == "resume1"]
        assert metrics[0]["used_tokens"] == 0 and metrics[-1]["used_tokens"] == 7
        assert session.cost_summary()["total_tokens"] == 1000007
        await c.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("scope", ["session", "workspace"])
def test_scoped_approval_and_permissions_reset(tmp_path, scope):
    async def scenario():
        write = ToolCall("write_file", {"path": "out.txt", "content": "x"}, "write1")
        agent = make_agent(tmp_path, [Response(tool_calls=[write]), "一", Response(tool_calls=[write]), "二",
                                      Response(tool_calls=[write]), "三"])
        c = Connection(agent, tmp_path)
        c.send("run", "r1", text="创建")
        approval = await c.until("approval_request", "r1")
        assert approval["decisions"] == ["once", "session", "workspace", "deny"]
        c.send("approval", "a1", approval_id=approval["approval_id"], decision=scope)
        await c.until("done", "r1")
        c.send("run", "r2", text="重复")
        await c.until("done", "r2")
        assert len([e for e in c.events if e["type"] == "approval_request"]) == 1
        c.send("command", "p1", command="permissions")
        assert scope in (await c.until("command_result", "p1"))["text"]
        c.send("command", "p2", command="permissions_reset")
        assert "撤销" in (await c.until("command_result", "p2"))["text"]
        assert agent.approval.permissions(tmp_path) == {"workspace_allowed": False, "session_rules": 0}
        c.send("run", "r3", text="再创建")
        await c.until("approval_request", "r3")
        c.send("cancel", "cancel3", target="r3")
        await c.until("done", "r3")
        await c.close()

    asyncio.run(scenario())


def test_server_commands_use_registry_and_persisted_session_cost(tmp_path):
    async def scenario():
        session = SessionStore.new(tmp_path, "fake")
        session.append_trace({"type": "run_end", "status": "done", "total_tokens": 1234,
                              "prompt_tokens": 1000, "completion_tokens": 234, "cost_usd": 0.25,
                              "tool_calls": 2})
        agent = make_agent(tmp_path)
        agent.tools.register(ToolSpec("custom", "额外工具", {}, lambda: "ok"))
        c = Connection(agent, tmp_path, session)
        for command, expected in (("tools", "custom"), ("cost", "1,234"), ("mcp", "未配置")):
            c.send("command", command, command=command)
            event = await c.until("command_result", command)
            assert event["command"] == command and expected in event["text"]
        assert not agent.provider.seen
        await c.close()

    asyncio.run(scenario())


def test_skill_commands_delegate_without_starting_model_or_saving_messages(tmp_path):
    async def scenario():
        agent = make_agent(tmp_path)
        calls = []

        def manage(action="list", name=None):
            calls.append((action, name))
            return f"skills {action}: {name or 'catalog'}"

        agent.manage_skills = manage
        c = Connection(agent, tmp_path)
        try:
            for index, args in enumerate((None, {"action": "refresh"},
                                          {"action": "activate", "name": "release"},
                                          {"action": "unload", "name": "release"})):
                payload = {} if args is None else {"args": args}
                request_id = f"skills-{index}"
                c.send("command", request_id, command="skills", **payload)
                result = await c.until("command_result", request_id)
                assert result["command"] == "skills" and "skills" in result["text"]
            assert calls == [("list", None), ("refresh", None),
                             ("activate", "release"), ("unload", "release")]
            assert not agent.provider.seen and not agent.history
        finally:
            await c.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("command,args", [
    ("cost", {}), ("skills", None), ("skills", []),
    ("skills", {"unknown": "value"}), ("skills", {"action": 1}),
    ("skills", {"action": "delete"}), ("skills", {"action": "activate"}),
    ("skills", {"action": "unload", "name": " "}),
    ("skills", {"action": "activate", "name": 1}),
    ("skills", {"action": "list", "name": "release"}),
    ("skills", {"action": "refresh", "name": "release"}),
])
def test_skill_command_protocol_rejects_invalid_arguments(tmp_path, command, args):
    async def scenario():
        agent = make_agent(tmp_path)
        c = Connection(agent, tmp_path)
        try:
            c.send("command", "bad", command=command, args=args)
            assert (await c.until("error", "bad"))["code"] == "invalid_request"
            assert not agent.provider.seen and not agent.history
        finally:
            await c.close()

    asyncio.run(scenario())


def test_skill_command_unavailable_in_chat_only_mode(tmp_path):
    async def scenario():
        agent = make_agent(tmp_path)
        agent.tools_enabled = False
        agent.manage_skills = lambda **kwargs: pytest.fail("disabled skills must not be managed")
        c = Connection(agent, tmp_path)
        try:
            c.send("command", "skills", command="skills")
            assert "不可用" in (await c.until("command_result", "skills"))["text"]
            assert not agent.provider.seen
        finally:
            await c.close()

    asyncio.run(scenario())


def test_skill_management_refresh_activation_and_unload_update_model_context(tmp_path):
    async def scenario():
        agent = make_agent(tmp_path)
        skill_file = tmp_path / ".vibe/skills/release/SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("---\nname: release\ndescription: 发布检查\n---\n\nSKILL_BODY_ONLY_WHEN_ACTIVE\n")
        c = Connection(agent, tmp_path)
        try:
            c.send("command", "discover", command="skills", args={"action": "refresh"})
            assert "release: 发布检查" in (await c.until("command_result", "discover"))["text"]
            assert "release: 发布检查" in agent.system
            assert "SKILL_BODY_ONLY_WHEN_ACTIVE" not in agent.system
            c.send("command", "activate", command="skills", args={"action": "activate", "name": "release"})
            await c.until("command_result", "activate")
            assert "SKILL_BODY_ONLY_WHEN_ACTIVE" in agent.system
            c.send("command", "unload", command="skills", args={"action": "unload", "name": "release"})
            await c.until("command_result", "unload")
            assert "SKILL_BODY_ONLY_WHEN_ACTIVE" not in agent.system
            assert skill_file.exists() and not agent.provider.seen and not agent.history
        finally:
            await c.close()

    asyncio.run(scenario())


def test_skill_management_is_rejected_while_an_approval_is_pending(tmp_path):
    async def scenario():
        agent = make_agent(tmp_path)
        agent.manage_skills = lambda **kwargs: pytest.fail("busy skills must not be managed")
        c = Connection(agent, tmp_path)
        try:
            c.send("command", "diff", command="diff")
            await c.until("approval_request", "diff")
            c.send("command", "skills", command="skills", args={"action": "refresh"})
            assert (await c.until("error", "skills"))["code"] == "busy"
            c.send("cancel", "cancel", target="diff")
            await c.until("command_result", "diff")
            assert not agent.provider.seen
        finally:
            await c.close()

    asyncio.run(scenario())


def test_skill_management_refreshes_loaded_context_before_first_task(tmp_path):
    async def scenario():
        skill_file = tmp_path / ".vibe/skills/release/SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("---\nname: release\ndescription: 发布检查\n---\n\nACTIVE_SKILL_BODY\n")
        agent = make_agent(tmp_path)
        agent.context = ContextBuilder(policy=Config().budget_policy())
        session = SessionStore.new(tmp_path, "fake", purpose="test")
        c = Connection(agent, tmp_path, session=session)
        try:
            await c.until("status")
            before = next(event["snapshot"] for event in c.events if event["type"] == "context_snapshot")
            assert not any(block["kind"] == "active_skill" for block in before["blocks"])
            for action, expected_active in (("activate", True), ("unload", False)):
                c.send("command", action, command="skills", args={"action": action, "name": "release"})
                await c.until("command_result", action)
                snapshot_event = await c.until("context_snapshot")
                snapshot = snapshot_event["snapshot"]
                assert snapshot_event["id"] is None and snapshot["phase"] == "loaded"
                assert any(block["kind"] == "active_skill" for block in snapshot["blocks"]) is expected_active
                assert snapshot["snapshot_id"] != before["snapshot_id"]
                before = snapshot
                await c.until("status")
            assert not agent.provider.seen and not agent.history and agent.state is None
            assert session.latest_run() is None and session.load_messages() == []
            assert all(event["used_tokens"] == 0 for event in c.events if event["type"] == "metrics")
        finally:
            await c.close()

    asyncio.run(scenario())


def test_skill_management_after_task_keeps_request_replay_and_recorded_usage(tmp_path):
    async def scenario():
        skill_file = tmp_path / ".vibe/skills/release/SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text("---\nname: release\ndescription: 发布检查\n---\n\nACTIVE_SKILL_BODY\n")
        agent = make_agent(tmp_path, [Response(content="done", usage={"total_tokens": 17})])
        agent.context = ContextBuilder(policy=Config().budget_policy())
        session = SessionStore.new(tmp_path, "fake", purpose="test")
        c = Connection(agent, tmp_path, session=session)
        try:
            c.send("run", "first", text="检查项目")
            await c.until("done", "first")
            await c.until("status")
            before = len(c.events)
            history = list(agent.history)
            cost = session.cost_summary()
            c.send("command", "activate", command="skills", args={"action": "activate", "name": "release"})
            await c.until("command_result", "activate")
            await c.until("status")
            assert not any(event["type"] in {"context_snapshot", "metrics"} for event in c.events[before:])
            assert session.cost_summary() == cost and cost["total_tokens"] == 17
            assert len(agent.provider.seen) == 1 and agent.history == history
        finally:
            await c.close()

    asyncio.run(scenario())


def test_mcp_status_does_not_call_broken_connection_connected(tmp_path):
    async def scenario():
        manager = StartupManager()
        manager.servers = {}
        c = Connection(make_agent(tmp_path), tmp_path, mcp=manager)
        await c.until("status")
        manager.servers = {"sample": object()}
        manager.clients["sample"] = SimpleNamespace(broken="timeout or cancelled", client=None)
        c.send("command", "mcp1", command="mcp")
        text = (await c.until("command_result", "mcp1"))["text"]
        assert "已连接" not in text and "不可用" in text
        await c.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("answer", ["once", "deny", "cancel"])
def test_diff_command_requires_same_gate_and_can_cancel(tmp_path, answer):
    async def scenario():
        agent = make_agent(tmp_path)
        executed = []

        async def execute(call, cancel=None):
            executed.append(call)
            return True, "diff output"

        agent.tools.execute_async = execute
        c = Connection(agent, tmp_path)
        c.send("command", "diff1", command="diff")
        request = await c.until("approval_request", "diff1")
        assert request["call"]["name"] == "bash" and "git diff" in request["preview"]
        c.send("command", "busy", command="tools")
        assert (await c.until("error", "busy"))["code"] == "busy"
        if answer == "cancel":
            c.send("cancel", "cancel1", target="diff1")
        else:
            c.send("approval", "a1", approval_id=request["approval_id"], decision=answer)
        result = await c.until("command_result", "diff1")
        assert bool(executed) is (answer == "once")
        assert ("diff output" in result["text"]) is (answer == "once")
        assert any(e["type"] == "status" and e["phase"] == "idle" for e in c.events)
        await c.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("close_mode", ["cancel", "disconnect"])
def test_diff_in_flight_is_cancelled_without_hanging(tmp_path, close_mode):
    async def scenario():
        agent = make_agent(tmp_path)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def execute(call, cancel=None):
            started.set()
            await cancel.wait()
            cancelled.set()
            return False, "ERROR cancelled: execution outcome unknown; already started"

        agent.tools.execute_async = execute
        c = Connection(agent, tmp_path)
        c.send("command", "diff1", command="diff")
        request = await c.until("approval_request", "diff1")
        c.send("approval", "a1", approval_id=request["approval_id"], decision="once")
        await asyncio.wait_for(started.wait(), 1)
        if close_mode == "cancel":
            c.send("cancel", "cancel1", target="diff1")
            result = await c.until("command_result", "diff1")
            assert "unknown" in result["text"]
        await c.close()
        assert cancelled.is_set()

    asyncio.run(scenario())


class StartupManager:
    servers = {"sample": object()}

    def __init__(self):
        self.errors = []
        self.clients = {}
        self.tools = {}
        self.started = False
        self.closed = False

    async def register_into(self, registry, approval):
        decision = await approval.request(ToolCall("mcp_start", {"server": "sample"}), "start sample", registry.root)
        if not decision.allowed:
            self.errors.append("sample: startup denied")
            return []
        self.started = True
        self.clients["sample"] = object()
        return []

    async def close(self):
        self.closed = True


@pytest.mark.parametrize("decision", ["once", "deny", "cancel", "disconnect"])
def test_mcp_startup_is_approved_cancelled_and_closed(tmp_path, decision):
    async def scenario():
        manager = StartupManager()
        agent = make_agent(tmp_path)
        c = Connection(agent, tmp_path, mcp=manager)
        await c.until("ready")
        request = await c.until("approval_request", "startup")
        assert request["decisions"] == ["once", "session", "deny"]
        c.send("run", "early", text="不能抢跑")
        assert (await c.until("error", "early"))["code"] == "busy"
        if decision == "disconnect":
            await c.close()
        else:
            if decision == "cancel":
                c.send("cancel", "cancel1", target="startup")
            else:
                c.send("approval", "a1", approval_id=request["approval_id"], decision=decision)
            while (await c.until("status"))["phase"] != "idle":
                pass
            await c.close()
        assert manager.started is (decision == "once")
        assert manager.closed and not agent.provider.seen
        assert not agent.approval.workspace_allowed

    asyncio.run(scenario())


def test_mcp_workspace_scope_is_rejected_without_releasing_approval(tmp_path):
    async def scenario():
        manager = StartupManager()
        c = Connection(make_agent(tmp_path), tmp_path, mcp=manager)
        request = await c.until("approval_request", "startup")
        c.send("approval", "bad", approval_id=request["approval_id"], decision="workspace")
        assert (await c.until("error", "bad"))["code"] == "invalid_request"
        assert not manager.started
        await c.close()

    asyncio.run(scenario())


def test_cancel_mcp_during_connection_initialization(tmp_path):
    class SlowManager(StartupManager):
        def __init__(self):
            super().__init__()
            self.connecting = asyncio.Event()
            self.connection_cancelled = False

        async def register_into(self, registry, approval):
            await super().register_into(registry, approval)
            self.connecting.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.connection_cancelled = True
                raise

    async def scenario():
        manager = SlowManager()
        c = Connection(make_agent(tmp_path), tmp_path, mcp=manager)
        request = await c.until("approval_request", "startup")
        c.send("approval", "a1", approval_id=request["approval_id"], decision="once")
        await asyncio.wait_for(manager.connecting.wait(), 1)
        c.send("cancel", "cancel1", target="startup")
        while (await c.until("status"))["phase"] != "idle":
            pass
        assert manager.connection_cancelled
        assert manager.closed
        await c.close()
        assert manager.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("decision", ["once", "deny", "cancel", "disconnect"])
def test_real_mcp_manager_registered_tool_requires_its_own_approval(tmp_path, monkeypatch, decision):
    class Remote:
        name = "echo"
        description = "echo"
        input_schema = {"type": "object"}

    async def scenario():
        from app import mcp as mcp_module

        called = []

        class Transport:
            def __init__(self, server, root):
                self.server = server

            async def open(self):
                return [Remote()]

            async def call(self, name, args):
                called.append((name, args))
                return "remote"

            async def close(self):
                pass

        monkeypatch.setattr(mcp_module, "_Connection", Transport)
        manager = McpManager.from_config({"demo": {"command": "never-launched"}}, tmp_path)
        agent = make_agent(tmp_path, [Response(tool_calls=[ToolCall("mcp_demo_echo", {}, "tool1")]), "done"])
        c = Connection(agent, tmp_path, mcp=manager)
        startup = await c.until("approval_request", "startup")
        c.send("approval", "a1", approval_id=startup["approval_id"], decision="once")
        while (await c.until("status"))["phase"] != "idle":
            pass
        c.send("run", "r1", text="外部工具")
        request = await c.until("approval_request", "r1")
        assert request["call"]["name"] == "mcp_demo_echo"
        assert "workspace" not in request["decisions"]
        assert not called
        if decision == "disconnect":
            await c.close()
        else:
            if decision == "cancel":
                c.send("cancel", "cancel1", target="r1")
            else:
                c.send("approval", "a2", approval_id=request["approval_id"], decision=decision)
            await c.until("done", "r1")
            await c.close()
        assert bool(called) is (decision == "once")

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["failed", "timed_out", "cancelled"])
def test_native_command_execution_metadata_reaches_tool_result(tmp_path, outcome):
    async def scenario():
        marker = tmp_path / "started"
        code = f"from pathlib import Path; import time; Path({str(marker)!r}).touch(); print('partial', flush=True); time.sleep(5)"
        command = "exit 7" if outcome == "failed" else f"{shlex.quote(sys.executable)} -u -c {shlex.quote(code)}"
        args = {"command": command, "timeout": 0.2 if outcome == "timed_out" else 10}
        agent = make_agent(tmp_path, [Response(tool_calls=[ToolCall("bash", args, "shell1")]),
                                     "检查失败"])
        c = Connection(agent, tmp_path)
        c.send("run", "r1", text="运行检查")
        request = await c.until("approval_request", "r1")
        c.send("approval", "a1", approval_id=request["approval_id"], decision="once")
        if outcome == "cancelled":
            async with asyncio.timeout(2):
                while not marker.exists():
                    await asyncio.sleep(0.01)
            c.send("cancel", "cancel1", target="r1")
        await c.until("done", "r1")
        result = next(e for e in c.events if e["type"] == "tool_result")
        assert result["ok"] is False
        assert result["execution"]["status"] == outcome
        if outcome == "failed":
            assert result["execution"]["exit_code"] == 7
        else:
            assert "partial" in result["output"]
        restored = c.events[0]["session_path"]
        tool = next(m for m in SessionStore(restored).load_messages() if m.role == "tool")
        assert tool.execution == result["execution"]
        await c.close()

    asyncio.run(scenario())


def test_stdio_with_real_fastmcp_server_lifecycle(tmp_path):
    async def scenario():
        script = tmp_path / "server.py"
        marker = tmp_path / "server.pid"
        script.write_text(
            "import os\nfrom pathlib import Path\nfrom fastmcp import FastMCP\n"
            f"Path({str(marker)!r}).write_text(str(os.getpid()))\n"
            "server = FastMCP('stdio-parity')\n"
            "@server.tool\ndef echo(text: str) -> str:\n    return 'remote:' + text\n"
            "server.run(transport='stdio', show_banner=False)\n",
        )
        manager = McpManager.from_config({"demo": {"command": sys.executable, "args": [str(script)]}}, tmp_path)
        agent = make_agent(tmp_path, [Response(tool_calls=[ToolCall("mcp_demo_echo", {"text": "编码"}, "remote1")]),
                                     "已收到远端结果"])
        c = Connection(agent, tmp_path, mcp=manager)
        try:
            request = await c.until("approval_request", "startup")
            assert not marker.exists()
            c.send("approval", "start", approval_id=request["approval_id"], decision="once")
            while (await c.until("status", timeout=20))["phase"] != "idle":
                pass
            assert marker.exists() and "mcp_demo_echo" in agent.tools.names()
            c.send("command", "tools1", command="tools")
            assert "mcp_demo_echo" in (await c.until("command_result", "tools1"))["text"]
            c.send("run", "r1", text="调用 echo")
            request = await c.until("approval_request", "r1")
            assert request["call"]["name"] == "mcp_demo_echo"
            c.send("approval", "tool", approval_id=request["approval_id"], decision="once")
            done = await c.until("done", "r1", timeout=10)
            assert done["result"]["status"] == "done"
            output = next(e for e in c.events if e["type"] == "tool_result")
            assert output["ok"] is True and "remote:编码" in output["output"]
            audit = [json.loads(line) for line in agent.approval.audit_path.read_text().splitlines()]
            assert [event["tool"] for event in audit] == ["mcp_start", "mcp_demo_echo"]
        finally:
            await c.close()
        assert not manager.clients
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["append_trace", "append_message", "save_state"])
def test_session_write_failure_still_closes_mcp_and_restores_handler(tmp_path, monkeypatch, operation):
    async def scenario():
        session = SessionStore.new(tmp_path, "fake")
        manager = StartupManager()
        manager.servers = {}
        agent = make_agent(tmp_path, ["ok"])

        async def previous_handler(call, preview):
            raise AssertionError("stdio must install its own handler")

        agent.approval.handler = previous_handler

        def fail(*args, **kwargs):
            raise OSError(f"disk full: {operation}")

        monkeypatch.setattr(session, operation, fail)
        c = Connection(agent, tmp_path, session=session, mcp=manager)
        c.send("run", "r1", text="保留进度")
        done = await c.until("done", "r1")
        assert done["result"]["status"] == "error"
        assert "disk full" in done["result"]["text"]
        await c.close()
        assert manager.closed and agent.approval.handler is previous_handler
        assert c.events[-1]["type"] == "closed"
        assert len([event for event in c.events if event["type"] == "done"]) == 1
        assert any(event["type"] == "error" and "disk full" in event["message"] for event in c.events)

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["save_context", "save_state"])
def test_final_snapshot_failure_records_one_honest_end_with_usage(tmp_path, monkeypatch, operation):
    async def scenario():
        session = SessionStore.new(tmp_path, "fake")
        context_calls = 0
        state_calls = 0
        save_context, save_state = session.save_context, session.save_state

        def context(messages):
            nonlocal context_calls
            context_calls += 1
            if operation == "save_context":
                raise OSError("disk full: final context")
            save_context(messages)

        def state(value):
            nonlocal state_calls
            state_calls += 1
            if operation == "save_state" and state_calls > 1:
                raise OSError("disk full: final state")
            save_state(value)

        monkeypatch.setattr(session, "save_context", context)
        monkeypatch.setattr(session, "save_state", state)
        agent = make_agent(tmp_path, [Response(content="ok", usage={"total_tokens": 17})])
        c = Connection(agent, tmp_path, session=session)
        c.send("run", "r1", text="保存结果")
        done = await c.until("done", "r1")
        assert done["result"]["status"] == "error"
        assert done["result"]["usage"]["total_tokens"] == 17
        assert state_calls == 2 and context_calls == 1
        assert session.latest_run()["status"] == "error"
        assert session.cost_summary()["runs"] == 1
        assert session.cost_summary()["total_tokens"] == 17
        metrics = [event for event in c.events if event["type"] == "metrics"]
        assert metrics[-1]["used_tokens"] == 17
        await c.close()

    asyncio.run(scenario())


def test_active_task_failure_does_not_skip_cleanup_or_hide_mcp_close_error(tmp_path, monkeypatch):
    from app.stdio import _Connection

    class FailingClose(StartupManager):
        servers = {}

        async def close(self):
            self.closed = True
            raise OSError("MCP close failed")

    async def scenario():
        manager = FailingClose()
        agent = make_agent(tmp_path)
        previous_handler = agent.approval.handler
        started = asyncio.Event()

        async def crash(self, request_id, text):
            started.set()
            raise RuntimeError("worker finalizer failed")

        monkeypatch.setattr(_Connection, "run", crash)
        c = Connection(agent, tmp_path, mcp=manager)
        c.send("run", "r1", text="崩溃")
        await asyncio.wait_for(started.wait(), 1)
        await c.close()
        assert manager.closed and agent.approval.handler is previous_handler
        assert c.events[-1]["type"] == "closed"
        assert any(event["type"] == "error" and "worker finalizer failed" in event["message"] for event in c.events)
        assert any(event["type"] == "notice" and "MCP close failed" in event["message"] for event in c.events)

    asyncio.run(scenario())


class _GatedProvider:
    """第一次请求返回工具调用；之后的请求等待测试放行，便于在运行中插入 steering。"""

    def __init__(self):
        self.seen = []
        self.seen_tools = []
        self.calls = 0
        self.release = asyncio.Event()

    async def stream(self, messages, tools):
        self.seen.append(list(messages))
        self.seen_tools.append(list(tools))
        self.calls += 1
        if self.calls == 1:
            yield ToolCallsReady([ToolCall("list_files", {}, id="c1")])
            yield StreamDone()
            return
        await self.release.wait()
        yield TextDelta("已按纠偏继续")
        yield StreamDone()


def test_steer_frame_queues_for_the_active_run_and_is_persisted(tmp_path):
    async def scenario():
        provider = _GatedProvider()
        agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate())
        session = SessionStore.new(tmp_path, "fake")
        c = Connection(agent, tmp_path, session)
        ready = await c.until("ready")
        assert "steer" in ready["capabilities"]
        await c.until("status")
        c.send("run", "r1", text="重构")
        for _ in range(200):
            if provider.calls >= 2:
                break
            await asyncio.sleep(0.005)
        assert provider.calls == 2
        c.send("steer", "s1", target="r1", text="保留公开接口")
        ack = await c.until("ack", "s1")
        assert ack["action"] == "steer"
        provider.release.set()
        done = await c.until("done", "r1")
        # 模型收尾前到达的纠偏会再走一步，不被静默丢弃。
        assert done["result"]["status"] == "done"
        assert done["result"]["steps"] == 3
        assert [m.content for m in provider.seen[2] if m.role == "user"] == ["重构", "保留公开接口"]
        assert any(
            m.role == "user" and m.name == "steering" and m.content == "保留公开接口"
            for m in agent.history
        )
        assert any(
            message.role == "user" and message.name == "steering"
            and message.content == "保留公开接口"
            for message in session.load_messages()
        )
        assert any(
            event["type"] == "notice" and "已应用纠偏：保留公开接口" in event["message"]
            for event in c.events
        )
        await c.close()

    asyncio.run(scenario())


def test_steer_requires_matching_active_run_and_nonempty_text(tmp_path):
    async def scenario():
        agent = make_agent(tmp_path, ["done"])
        c = Connection(agent, tmp_path)
        await c.until("ready")
        await c.until("status")
        c.send("steer", "s1", target="missing", text="保留公开接口")
        assert (await c.until("error", "s1"))["code"] == "run_mismatch"
        c.send("steer", "s2", target="missing", text="   ")
        assert (await c.until("error", "s2"))["code"] == "invalid_request"
        c.send("steer", "s3", target="missing")
        assert (await c.until("error", "s3"))["code"] == "invalid_request"
        await c.close()

    asyncio.run(scenario())
