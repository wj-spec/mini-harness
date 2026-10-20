import asyncio
import sys

import pytest
from textual.widgets import RichLog

from app import __main__ as entrypoint
from app.config import Config
from app.session import SessionStore
from app.tui import VibeApp
from core.models import Message
from core.state import TaskState


def test_continue_opens_previous_conversation_after_empty_launch(tmp_path, monkeypatch):
    previous = SessionStore.new(tmp_path, "fake")
    previous.append_message(Message("user", "实现 MCP 客户端"))
    previous.append_message(Message("assistant", "已完成调研，等待继续。", name="budget_stop"))
    previous.save_state(TaskState(goal="实现 MCP 客户端", open=["实现客户端"]))
    previous.append_trace({"type": "run_end", "status": "stopped", "stop_reason": "token_budget"})
    SessionStore.new(tmp_path, "fake")
    opened = []

    class CaptureApp:
        def __init__(self, config, agent, session, resumed):
            opened.append((agent, session, resumed))

        def run(self):
            pass

    monkeypatch.setattr(entrypoint, "VibeApp", CaptureApp)
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake", "--workspace", str(tmp_path), "--continue"])

    entrypoint.main()

    agent, session, resumed = opened[0]
    assert resumed and session.id == previous.id
    assert agent.history[-1].content == "已完成调研，等待继续。"
    assert agent.state.goal == "实现 MCP 客户端"


def test_continue_restores_checkpoint_and_resume_after_empty_launch(tmp_path, monkeypatch):
    previous = SessionStore.new(tmp_path, "fake")
    goal = Message("user", "实现 MCP 客户端")
    previous.append_message(goal)
    previous.append_message(Message("assistant", "很早的原文"))
    summary = Message("assistant", "已定位接入位置，尚未修改。", name="history_summary_l2")
    previous.save_context([goal, summary])
    previous.append_message(Message("assistant", "预算暂停，等待继续。", name="budget_stop"))
    previous.append_trace({"type": "run_end", "status": "stopped", "stop_reason": "token_budget"})
    SessionStore.new(tmp_path, "fake")

    class CheckedApp(VibeApp):
        def run(self):
            async def scenario():
                async with self.run_test() as pilot:
                    await pilot.pause(0.2)
                    assert self.session.id == previous.id
                    assert self.agent.history == previous.load_context()
                    assert self.agent.provider.seen == []
                    log = "\n".join(line.text for line in self.query_one("#log", RichLog).lines)
                    assert "任务已暂停" in log
                    await pilot.click("#prompt")
                    await pilot.press(*"/resume")
                    await pilot.press("enter")
                    await pilot.pause(0.6)
                    assert len(self.agent.provider.seen) == 1
                    request = self.agent.provider.seen[0]
                    assert summary in request
                    assert not any(m.content == "很早的原文" for m in request)
                    assert self.session.latest_run()["status"] == "done"
                    await pilot.press("ctrl+q")

            asyncio.run(scenario())

    monkeypatch.setattr(entrypoint, "VibeApp", CheckedApp)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake", "--continue"])

    entrypoint.main()


def test_invalid_mcp_config_exits_before_tui(tmp_path, monkeypatch, capsys):
    opened = []

    class CaptureApp:
        def __init__(self, *args, **kwargs):
            opened.append(args)

        def run(self):
            pass

    monkeypatch.setattr(entrypoint, "VibeApp", CaptureApp)
    monkeypatch.setattr(
        entrypoint.Config,
        "from_env",
        lambda: Config(workspace=tmp_path, mcp_servers="{not json"),
    )
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake"])

    with pytest.raises(SystemExit) as exc:
        entrypoint.main()

    assert exc.value.code == 1
    assert "invalid MCP config" in capsys.readouterr().err
    assert opened == []
