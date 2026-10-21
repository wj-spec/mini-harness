import asyncio
import json

import pytest

from textual.widgets import Input, RichLog, Static

from app.agent import Agent
from app.config import Config
from app.policy import ApprovalGate
from app.providers.fake import FakeProvider
from app.session import SessionStore
from app.trace import Pricing
from app.tools import ToolRegistry, ToolSpec
from app.tui import ApprovalScreen, VibeApp
from core.models import Message, Response, ToolCall
from core.context import ContextBuilder
from core.state import TaskState
from tests.test_context_view import snapshot


def _log_text(app):
    return "\n".join(line.text for line in app.query_one("#log", RichLog).lines)


class StartupMcp:
    servers = [object()]

    def __init__(self):
        self.errors = []
        self.clients = {}
        self.events = []
        self.loop = None

    async def register_into(self, registry, approval):
        self.loop = asyncio.get_running_loop()
        self.events.append("approval")
        decision = await approval.request(
            ToolCall("mcp_start", {"command": "fake-mcp"}), "启动 fake-mcp", registry.root,
        )
        if not decision.allowed:
            self.errors.append("fake: startup denied")
            return []
        self.events.append("started")
        self.clients["fake"] = object()
        registry.register(ToolSpec("mcp_fake_echo", "Echo", {"type": "object"}, lambda: "ok"))
        return ["mcp_fake_echo"]

    async def close(self):
        assert asyncio.get_running_loop() is self.loop
        self.events.append("closed")
        self.clients.clear()


def test_mcp_startup_approval_precedes_resume_and_closes_on_quit(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    session.append_message(Message("user", "continue the MCP task"))
    provider = FakeProvider(["resumed"])
    gate = ApprovalGate(audit_path=session.audit_path)
    manager = StartupMcp()
    app = VibeApp(Config(workspace=tmp_path),
                  Agent(provider, ToolRegistry(tmp_path), approval=gate, history=session.load_context()),
                  session=session, resumed=True, mcp=manager)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            assert isinstance(app.screen, ApprovalScreen)
            assert app.screen.tool_call.name == "mcp_start"
            assert provider.seen == [] and manager.events == ["approval"]
            assert app.query_one("#prompt", Input).disabled
            await pilot.press("y")
            await pilot.pause(0.4)
            assert manager.events == ["approval", "started"]
            assert len(provider.seen) == 1
            assert any(tool["function"]["name"] == "mcp_fake_echo" for tool in provider.seen_tools[0])
            assert "mcp tools: 1" in _log_text(app)
            await pilot.press("ctrl+q")
        assert manager.events == ["approval", "started", "closed"]

    asyncio.run(scenario())


def test_mcp_denied_startup_stays_usable_and_reports_warning(tmp_path):
    manager = StartupMcp()
    provider = FakeProvider(["still usable"])
    app = VibeApp(Config(workspace=tmp_path),
                  Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate()), mcp=manager)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            await pilot.press("n")
            await pilot.pause(0.2)
            assert manager.events == ["approval"]
            assert "startup denied" in _log_text(app)
            assert not app.query_one("#prompt", Input).disabled
            await pilot.click("#prompt")
            await pilot.press(*"hi", "enter")
            await pilot.pause(0.2)
            assert len(provider.seen) == 1
            await pilot.press("ctrl+q")
        assert manager.events == ["approval", "closed"]

    asyncio.run(scenario())


def test_quit_during_mcp_startup_does_not_resume_or_leave_modal(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    session.append_message(Message("user", "interrupted task"))
    manager = StartupMcp()
    provider = FakeProvider(["must not run"])
    app = VibeApp(Config(workspace=tmp_path),
                  Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(), history=session.load_context()),
                  session=session, resumed=True, mcp=manager)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            assert isinstance(app.screen, ApprovalScreen)
            await pilot.press("ctrl+q")
        assert provider.seen == []
        assert manager.events == ["approval", "closed"]

    asyncio.run(scenario())


def test_cancel_mcp_startup_keeps_prompt_usable_without_auto_resume(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    session.append_message(Message("user", "interrupted task"))
    manager = StartupMcp()
    provider = FakeProvider(["new request"])
    app = VibeApp(Config(workspace=tmp_path),
                  Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(), history=session.load_context()),
                  session=session, resumed=True, mcp=manager)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            await pilot.press("ctrl+c")
            await pilot.pause(0.2)
            assert not isinstance(app.screen, ApprovalScreen)
            assert manager.events == ["approval"] and provider.seen == []
            assert not app.busy and not app.query_one("#prompt", Input).disabled
            await pilot.click("#prompt")
            await pilot.press(*"hi", "enter")
            await pilot.pause(0.2)
            assert len(provider.seen) == 1
            await pilot.press("ctrl+q")
        assert manager.events == ["approval", "closed"]

    asyncio.run(scenario())


def test_mcp_cleanup_also_runs_when_tui_is_unmounted(tmp_path):
    manager = StartupMcp()
    app = VibeApp(Config(workspace=tmp_path),
                  Agent(FakeProvider([]), ToolRegistry(tmp_path), approval=ApprovalGate()), mcp=manager)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            await pilot.press("y")
            await pilot.pause(0.2)
        assert manager.events == ["approval", "started", "closed"]

    asyncio.run(scenario())


def test_mcp_cleanup_failure_does_not_block_quit(tmp_path):
    class FailingCloseMcp(StartupMcp):
        async def close(self):
            await super().close()
            raise RuntimeError("cleanup failed")

    manager = FailingCloseMcp()
    app = VibeApp(Config(workspace=tmp_path),
                  Agent(FakeProvider([]), ToolRegistry(tmp_path), approval=ApprovalGate()), mcp=manager)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            await pilot.press("y")
            await pilot.pause(0.2)
            await pilot.press("ctrl+q")
        assert manager.events == ["approval", "started", "closed"]
        assert app._mcp_shutdown_error == "cleanup failed"

    asyncio.run(scenario())


def test_approval_modal_allows_write(tmp_path):
    provider = FakeProvider(
        [
            Response(
                tool_calls=[
                    ToolCall("write_file", {"path": "a.txt", "content": "hello"}, id="c1")
                ]
            ),
            Response(content="done"),
        ]
    )
    app = VibeApp(
        Config(workspace=tmp_path),
        Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate()),
    )

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"hi")
            await pilot.press("enter")
            await pilot.pause(0.5)
            assert isinstance(app.screen, ApprovalScreen)
            await pilot.press("y")
            await pilot.pause(1.0)
            assert (tmp_path / "a.txt").read_text() == "hello"
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_approval_modal_denies_write(tmp_path):
    provider = FakeProvider(
        [
            Response(
                tool_calls=[
                    ToolCall("write_file", {"path": "a.txt", "content": "hello"}, id="c1")
                ]
            ),
            Response(content="done"),
        ]
    )
    app = VibeApp(
        Config(workspace=tmp_path),
        Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate()),
    )

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"hi")
            await pilot.press("enter")
            await pilot.pause(0.5)
            await pilot.press("n")
            await pilot.pause(1.0)
            assert not (tmp_path / "a.txt").exists()
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_tui_resumes_interrupted_turn(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    session.append_message(Message("user", "old question"))
    provider = FakeProvider(["resumed answer"])
    agent = Agent(provider, ToolRegistry(tmp_path), history=session.load_messages())
    app = VibeApp(Config(workspace=tmp_path), agent, session=session, resumed=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause(0.6)
            assert provider.seen
            assert any(
                m.role == "user" and m.content == "old question"
                for m in provider.seen[0]
            )
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_tui_does_not_resume_completed_turn(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    session.append_message(Message("user", "q"))
    session.append_message(Message("assistant", "done"))
    provider = FakeProvider(["should not be called"])
    agent = Agent(provider, ToolRegistry(tmp_path), history=session.load_messages())
    app = VibeApp(Config(workspace=tmp_path), agent, session=session, resumed=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause(0.4)
            assert provider.seen == []
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


@pytest.mark.parametrize("cancelled_terminal", [True, False])
def test_mcp_unknown_outcome_requires_resume_after_recorded_cancellation(tmp_path, cancelled_terminal):
    session = SessionStore.new(tmp_path, "fake")
    call = ToolCall("mcp_fake_write", {"path": "a.txt", "content": "hello"}, id="mcp-write")
    session.append_message(Message("user", "write through MCP"))
    session.append_message(Message("assistant", tool_calls=[call]))
    session.append_message(Message("tool", "ERROR cancelled: execution outcome unknown",
                                   name=call.name, tool_call_id=call.id))
    session.append_trace({"type": "run_start", "run_id": "mcp-run"})
    if cancelled_terminal:
        session.append_trace({"type": "run_end", "run_id": "mcp-run", "status": "cancelled"})
    provider = FakeProvider(["先核对远端结果。"])
    app = VibeApp(Config(workspace=tmp_path),
                  Agent(provider, ToolRegistry(tmp_path), history=session.load_context()),
                  session=session, resumed=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            if cancelled_terminal:
                assert provider.seen == []
                assert "任务已取消" in _log_text(app)
                assert "/resume" in _log_text(app)
                assert "重新打开会话不会自动续跑" in _log_text(app)
                await pilot.click("#prompt")
                await pilot.press(*"/resume", "enter")
                await pilot.pause(0.2)
                assert "核对" in provider.seen[0][-1].content
            assert len(provider.seen) == 1
            assert any("execution outcome unknown" in m.content for m in provider.seen[0])
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


@pytest.mark.parametrize("stop_reason", ["token_budget", "cost_budget", None])
def test_tui_paused_run_requires_explicit_resume(tmp_path, stop_reason):
    session = SessionStore.new(tmp_path, "fake")
    session.append_message(Message("user", "unfinished task"))
    session.append_message(Message("tool", "last read result", name="read_file"))
    stop = {"type": "run_end", "status": "stopped", "total_tokens": 208401}
    if stop_reason is not None:
        stop.update(stop_reason=stop_reason, limit_tokens=200000, remaining_tokens=0)
    session.append_trace(stop)
    session.save_state(TaskState(goal="unfinished task", done=["read files"], open=["add MCP"]))
    provider = FakeProvider(["resumed answer"])
    agent = Agent(provider, ToolRegistry(tmp_path), history=session.load_messages())
    app = VibeApp(Config(workspace=tmp_path), agent, session=session, resumed=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            assert provider.seen == []
            assert "/resume" in _log_text(app)
            assert "208,401" in app._status_text()
            assert "read files" in _log_text(app)
            assert "add MCP" in _log_text(app)
            if stop_reason is not None:
                assert stop_reason in _log_text(app)

            await pilot.click("#prompt")
            await pilot.press(*"/resume")
            await pilot.press("enter")
            await pilot.pause(0.5)
            assert len(provider.seen) == 1
            assert not any(m.role == "user" and m.content == "/resume" for m in provider.seen[0])
            assert "新一轮预算" in _log_text(app)
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_tui_resumes_new_interrupted_run_after_old_budget_stop(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    session.append_message(Message("user", "unfinished task"))
    session.append_trace({"type": "run_end", "run_id": "a", "status": "stopped"})
    session.append_trace({"type": "run_start", "run_id": "b"})
    provider = FakeProvider(["resumed answer"])
    agent = Agent(provider, ToolRegistry(tmp_path), history=session.load_messages())
    app = VibeApp(Config(workspace=tmp_path), agent, session=session, resumed=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            assert len(provider.seen) == 1
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_resume_after_budget_summary_sends_continue_request_and_resets_budget(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    session.append_message(Message("user", "add MCP support"))
    session.append_message(Message("assistant", "已读取相关代码，尚未写入。", name="budget_stop"))
    session.append_trace({
        "type": "run_end", "run_id": "previous", "status": "stopped",
        "stop_reason": "token_budget", "total_tokens": 200000,
        "limit_tokens": 200000, "remaining_tokens": 0,
    })
    provider = FakeProvider([Response(
        content="continued work", usage={"prompt_tokens": 20, "completion_tokens": 5},
    )])
    agent = Agent(provider, ToolRegistry(tmp_path), history=session.load_messages(), max_run_tokens=200000)
    app = VibeApp(Config(workspace=tmp_path), agent, session=session, resumed=True)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            assert provider.seen == []
            await pilot.click("#prompt")
            await pilot.press(*"/resume")
            await pilot.press("enter")
            await pilot.pause(0.5)
            assert len(provider.seen) == 1
            assert provider.seen[0][-1].role == "user"
            assert "继续上次暂停" in provider.seen[0][-1].content
            assert "核对" in provider.seen[0][-1].content
            trace = [json.loads(line) for line in session.trace_path.read_text().splitlines()]
            updates = [event for event in trace if event["type"] == "budget_update"]
            assert updates[0]["used_tokens"] == 0
            assert updates[0]["remaining_tokens"] == 200000
            assert session.latest_run()["run_id"] != "previous"
            assert session.latest_run()["total_tokens"] == 25
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_tui_updates_run_budget_without_session(tmp_path):
    provider = FakeProvider([])
    app = VibeApp(Config(workspace=tmp_path), Agent(provider, ToolRegistry(tmp_path)))

    async def scenario():
        async with app.run_test() as pilot:
            app._record_trace({
                "type": "budget_update", "used_tokens": 160000, "limit_tokens": 200000,
                "remaining_tokens": 40000, "prompt_tokens": 150000,
                "completion_tokens": 10000, "phase": "finalizing", "estimated": True,
            })
            await pilot.pause()
            status = app._status_text()
            assert "160,000" in status
            assert "200,000" in status
            assert "40,000" in status
            assert "约" in status
            assert "收尾" in status
            assert status.startswith("任务累计")
            assert "160,000" in str(app.query_one("#status", Static).content)
            await pilot.click("#prompt")
            await pilot.press(*"/cost")
            await pilot.press("enter")
            await pilot.pause()
            assert "本轮" in _log_text(app)
            assert "入 150,000" in _log_text(app)
            assert "出 10,000" in _log_text(app)
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_context_modal_replays_snapshots_and_keeps_usage_separate(tmp_path):
    from app.tui import ContextScreen

    session = SessionStore.new(tmp_path, "fake")
    app = VibeApp(Config(workspace=tmp_path), Agent(FakeProvider([]), ToolRegistry(tmp_path)),
                  session=session)

    async def scenario():
        async with app.run_test(size=(48, 22)) as pilot:
            app._record_trace({"type": "run_start", "run_id": "run-a"})
            app._record_trace(snapshot())
            app._record_trace(snapshot(2, phase="after_compaction", delta_tokens=-50))
            app._record_trace({"type": "budget_update", "used_tokens": 400, "limit_tokens": 1000})
            app._on_context(type("LegacyContext", (), {
                "sections": {"history_before": 900, "history_after": 100},
                "caps": {}, "tokens": 999, "over_budget": False,
            })())
            assert "ctx 估算 250/900" in app._status_text()
            assert "任务累计 400" in app._status_text()
            assert "999" not in app._status_text()
            await pilot.click("#prompt")
            await pilot.press(*"/context", "enter")
            await pilot.pause()
            assert isinstance(app.screen, ContextScreen)
            assert app.screen.index == 1
            assert "压缩后" in app.screen.query_one("#context_body", Static).content.plain
            await pilot.press("left")
            assert app.screen.index == 0
            await pilot.press("right")
            assert app.screen.index == 1
            app._record_trace(snapshot(3))
            assert app.screen.index == 2
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ContextScreen)
            events = [json.loads(line) for line in session.trace_path.read_text().splitlines()]
            assert len([event for event in events if event["type"] == "context_snapshot"]) == 3
            app._record_trace({"type": "run_start", "run_id": "run-b"})
            assert app.context_history.snapshots == []
            assert "250/900" not in app._status_text()

    asyncio.run(scenario())


def test_context_view_empty_and_restores_only_latest_run(tmp_path):
    from app.tui import ContextScreen

    session = SessionStore.new(tmp_path, "fake")
    session.append_message(Message("user", "previous"))
    session.append_message(Message("assistant", "done"))
    session.append_trace({"type": "run_start", "run_id": "run-old"})
    session.append_trace(snapshot(1, "run-old"))
    session.append_trace({"type": "run_start", "run_id": "run-new"})
    session.append_trace(snapshot(2, "run-new"))
    session.append_trace({"type": "run_end", "run_id": "run-new", "status": "done"})
    with session.trace_path.open("a") as stream:
        stream.write("broken partial line\n")
    provider = FakeProvider([])
    app = VibeApp(Config(workspace=tmp_path), Agent(provider, ToolRegistry(tmp_path)),
                  session=session, resumed=True)

    async def scenario():
        async with app.run_test(size=(36, 18)) as pilot:
            assert app.context_history.run_id == "run-new"
            assert [event["step"] for event in app.context_history.snapshots] == [2]
            assert provider.seen == []
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert isinstance(app.screen, ContextScreen)
            app._record_trace({"type": "run_start", "run_id": "run-next"})
            assert "暂无" in app.screen.query_one("#context_body", Static).content.plain
            await pilot.press("left", "right", "escape")

    asyncio.run(scenario())


def test_tui_persists_compaction_for_resume(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    for message in [Message("user", "开发 MCP"), Message("assistant", "old detail " * 1000)]:
        session.append_message(message)
    context = ContextBuilder(policy=Config().budget_policy(), keep_recent_groups=1,
                             offload_dir=tmp_path / ".vibe" / "offload")
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("list_files", {}, id="files")], usage={"total_tokens": 80000}),
        Response("已完成调研，尚未实现。", usage={"total_tokens": 100}),
    ])
    agent = Agent(provider, ToolRegistry(tmp_path, context=context), context=context,
                  history=session.load_context(), max_run_tokens=100000, compact_interval_tokens=100000)
    app = VibeApp(Config(workspace=tmp_path), agent, session=session)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"go")
            await pilot.press("enter")
            await pilot.pause(0.7)
            assert len(provider.seen) == 2 and provider.seen_tools[-1]
            assert any(m.name == "history_summary_l2" for m in session.load_context())
            assert session.load_context() == agent.history
            assert "history 压缩" in _log_text(app)
            assert session.latest_run()["status"] == "done"
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_cancel_during_approval_dismisses_modal_and_restores_input(tmp_path):
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("write_file", {"path": "a.txt", "content": "hello"}, id="write")]),
        "新的回答",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate())
    app = VibeApp(Config(workspace=tmp_path), agent)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"hi")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert isinstance(app.screen, ApprovalScreen)
            await pilot.press("ctrl+c")
            await pilot.pause(0.2)
            assert not isinstance(app.screen, ApprovalScreen)
            assert app.busy is False
            assert app.query_one("#prompt", Input).disabled is False
            assert not (tmp_path / "a.txt").exists()
            requested = next(m for m in agent.history if m.tool_calls)
            assert any(m.tool_call_id == requested.tool_calls[0].id for m in agent.history if m.role == "tool")
            await pilot.click("#prompt")
            await pilot.press(*"again")
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert "新的回答" in _log_text(app)
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_cancelled_worker_cleans_up_approval_modal(tmp_path):
    provider = FakeProvider([Response(tool_calls=[ToolCall("write_file", {"path": "a", "content": "ok"}, id="a")])])
    app = VibeApp(Config(workspace=tmp_path), Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate()))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"go")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert isinstance(app.screen, ApprovalScreen)
            app.workers.cancel_all()
            await pilot.pause(0.2)
            assert not isinstance(app.screen, ApprovalScreen)
            assert not app.busy and not app.query_one("#prompt", Input).disabled
            assert not (tmp_path / "a").exists()
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_workspace_approval_can_be_viewed_and_revoked(tmp_path):
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("write_file", {"path": "a", "content": "one"}, id="a")]),
        Response(tool_calls=[ToolCall("write_file", {"path": "b", "content": "two"}, id="b")]),
        "done",
        Response(tool_calls=[ToolCall("write_file", {"path": "c", "content": "three"}, id="c")]),
        "done again",
    ])
    gate = ApprovalGate()
    app = VibeApp(Config(workspace=tmp_path), Agent(provider, ToolRegistry(tmp_path), approval=gate))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"go")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert isinstance(app.screen, ApprovalScreen)
            await pilot.press("p")
            await pilot.pause(0.3)
            assert (tmp_path / "a").exists() and (tmp_path / "b").exists()
            await pilot.click("#prompt")
            await pilot.press(*"/permissions")
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert "本进程工作区授权：已开启" in _log_text(app)
            await pilot.press(*"/permissions reset")
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert not gate.permissions(tmp_path)["workspace_allowed"]
            await pilot.press(*"go")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert isinstance(app.screen, ApprovalScreen)
            await pilot.press("n")
            await pilot.pause(0.2)
            assert not (tmp_path / "c").exists()
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_model_timeout_restores_input_for_next_turn(tmp_path):
    class Provider:
        calls = 0

        async def stream(self, messages, tools):
            from app.events import StreamDone, TextDelta
            self.calls += 1
            if self.calls == 1:
                await asyncio.Event().wait()
            yield TextDelta("下一轮可以正常回答")
            yield StreamDone(usage={"total_tokens": 20})

    app = VibeApp(Config(workspace=tmp_path), Agent(Provider(), ToolRegistry(tmp_path),
                                                 llm_timeout=0.03, llm_retries=0))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"go")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert not app.busy and not app.query_one("#prompt", Input).disabled
            assert "timeout" in _log_text(app)
            await pilot.press(*"again")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert "下一轮可以正常回答" in _log_text(app)
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_cancel_while_model_is_silent_restores_input(tmp_path):
    class Provider:
        calls = 0

        async def stream(self, messages, tools):
            from app.events import StreamDone, TextDelta
            self.calls += 1
            if self.calls == 1:
                await asyncio.Event().wait()
            yield TextDelta("取消后仍可继续对话")
            yield StreamDone(usage={"total_tokens": 20})

    app = VibeApp(Config(workspace=tmp_path), Agent(Provider(), ToolRegistry(tmp_path), llm_timeout=10))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"go")
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app.busy
            await pilot.press("ctrl+c")
            await pilot.pause(0.2)
            assert not app.busy and not app.query_one("#prompt", Input).disabled
            await pilot.press(*"again")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert "取消后仍可继续对话" in _log_text(app)
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_quit_from_approval_modal_exits_without_writing(tmp_path):
    provider = FakeProvider([Response(tool_calls=[ToolCall("write_file", {"path": "a", "content": "ok"}, id="a")])])
    app = VibeApp(Config(workspace=tmp_path), Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate()))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"go")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert isinstance(app.screen, ApprovalScreen)
            await pilot.press("ctrl+q")
            await pilot.pause(0.1)
        assert not (tmp_path / "a").exists()

    asyncio.run(scenario())


def test_no_progress_pause_requests_new_information_instead_of_more_budget(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    registry = ToolRegistry(tmp_path)
    registry.register(ToolSpec("inspect", "Inspect", {"type": "object"}, lambda: "unchanged"))
    provider = FakeProvider([
        *[Response(tool_calls=[ToolCall("inspect", {}, id=f"c{i}")]) for i in range(5)],
        "缺少新证据，需要调整方法。",
    ])
    app = VibeApp(Config(workspace=tmp_path), Agent(provider, registry), session=session)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"go")
            await pilot.press("enter")
            await pilot.pause(0.6)
            text = _log_text(app)
            assert "补充信息或调整方法" in text
            assert "输入 /resume 开启新一轮预算继续" not in text
            assert session.latest_run()["stop_reason"] == "no_progress"
            assert not app.busy and not app.query_one("#prompt", Input).disabled
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_cost_command_does_not_call_model(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    provider = FakeProvider(
        [
            Response(
                content="hello",
                usage={"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
            )
        ]
    )
    agent = Agent(provider, ToolRegistry(tmp_path), pricing=Pricing(1.0, 2.0))
    app = VibeApp(Config(workspace=tmp_path), agent, session=session)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"hi")
            await pilot.press("enter")
            await pilot.pause(0.5)

            await pilot.click("#prompt")
            await pilot.press(*"/cost")
            await pilot.press("enter")
            await pilot.pause(0.2)

            assert len(provider.seen) == 1
            assert session.cost_summary()["runs"] == 1
            assert session.cost_summary()["total_tokens"] == 1500
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_diff_command_does_not_call_model(tmp_path):
    provider = FakeProvider([])
    app = VibeApp(Config(workspace=tmp_path), Agent(provider, ToolRegistry(tmp_path)))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"/diff")
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert provider.seen == []
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_skills_command_lists_rescan_without_model(tmp_path):
    provider = FakeProvider([])
    app = VibeApp(Config(workspace=tmp_path), Agent(provider, ToolRegistry(tmp_path)))
    skill_dir = tmp_path / ".vibe" / "skills" / "release"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "name: release\ndescription: 打包并发布\n\n跑 make release\n", encoding="utf-8"
    )

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"/skill")
            await pilot.press("enter")
            await pilot.pause(0.3)
            text = _log_text(app)
            assert "skills" in text
            assert "release: 打包并发布" in text
            assert provider.seen == []
            await pilot.press("ctrl+q")

    asyncio.run(scenario())


def test_skills_command_handles_empty_directory(tmp_path):
    provider = FakeProvider([])
    app = VibeApp(Config(workspace=tmp_path), Agent(provider, ToolRegistry(tmp_path)))

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.click("#prompt")
            await pilot.press(*"/skills")
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert "no skills" in _log_text(app)
            assert provider.seen == []
            await pilot.press("ctrl+q")

    asyncio.run(scenario())
