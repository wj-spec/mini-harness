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


def _log_text(app):
    return "\n".join(line.text for line in app.query_one("#log", RichLog).lines)


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
