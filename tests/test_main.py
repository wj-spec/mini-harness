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
    previous = SessionStore.new(tmp_path, "fake", purpose="test")
    previous.append_message(Message("user", "实现 MCP 客户端"))
    previous.append_message(Message("assistant", "已完成调研，等待继续。", name="budget_stop"))
    previous.save_state(TaskState(goal="实现 MCP 客户端", open=["实现客户端"]))
    previous.append_trace({"type": "run_end", "status": "stopped", "stop_reason": "token_budget"})
    SessionStore.new(tmp_path, "fake", purpose="test")
    opened = []

    class CaptureApp:
        def __init__(self, config, agent, session, resumed, mcp=None):
            opened.append((agent, session, resumed))

        def run(self):
            pass

    monkeypatch.setattr(entrypoint, "build_textual_app", CaptureApp)
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake", "--workspace", str(tmp_path), "--continue"])

    entrypoint.main()

    agent, session, resumed = opened[0]
    assert resumed and session.id == previous.id
    assert agent.history[-1].content == "已完成调研，等待继续。"
    assert agent.state.goal == "实现 MCP 客户端"


def test_continue_restores_checkpoint_and_resume_after_empty_launch(tmp_path, monkeypatch):
    previous = SessionStore.new(tmp_path, "fake", purpose="test")
    goal = Message("user", "实现 MCP 客户端")
    previous.append_message(goal)
    previous.append_message(Message("assistant", "很早的原文"))
    summary = Message("assistant", "已定位接入位置，尚未修改。", name="history_summary_l2")
    previous.save_context([goal, summary])
    previous.append_message(Message("assistant", "预算暂停，等待继续。", name="budget_stop"))
    previous.append_trace({"type": "run_end", "status": "stopped", "stop_reason": "token_budget"})
    SessionStore.new(tmp_path, "fake", purpose="test")

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

    monkeypatch.setattr(entrypoint, "build_textual_app", CheckedApp)
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

    monkeypatch.setattr(entrypoint, "build_textual_app", CaptureApp)
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


@pytest.mark.parametrize("chat_only", [False, True])
def test_mcp_starts_inside_tui_lifecycle(tmp_path, monkeypatch, chat_only):
    opened = []

    class Manager:
        servers = [object()]

        async def register_into(self, registry, approval):
            pytest.fail("entrypoint must leave MCP startup to the TUI")

        async def close(self):
            pytest.fail("entrypoint must leave MCP cleanup to the TUI event loop")

    manager = Manager()

    class CaptureApp:
        def __init__(self, config, agent, session, resumed, mcp=None):
            opened.append((agent, mcp))

        def run(self):
            pass

    monkeypatch.setattr(entrypoint, "build_textual_app", CaptureApp)
    monkeypatch.setattr(entrypoint.McpManager, "from_env", lambda *args: manager)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    args = ["vibe", "--fake"] + (["--chat-only"] if chat_only else [])
    monkeypatch.setattr(sys, "argv", args)

    entrypoint.main()

    assert len(opened) == 1
    assert opened[0][1] is (None if chat_only else manager)
    assert opened[0][0].tools_enabled is not chat_only


def test_stdio_continue_restores_session_checkpoint_and_mcp(tmp_path, monkeypatch):
    from app import stdio

    previous = SessionStore.new(tmp_path, "fake", purpose="test")
    goal = Message("user", "继续接口")
    summary = Message("assistant", "已完成调研", name="history_summary_l2")
    previous.append_message(goal)
    previous.append_message(Message("assistant", "原文"))
    previous.save_context([goal, summary])
    previous.save_state(TaskState(goal="继续接口", open=["实现"]))
    manager = object()
    opened = []
    monkeypatch.setattr(stdio, "run_stdio", lambda agent, config, **kwargs: opened.append((agent, kwargs)))
    monkeypatch.setattr(entrypoint.McpManager, "from_env", lambda *args: manager)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake", "--stdio", "--continue"])

    entrypoint.main()

    agent, options = opened[0]
    assert options["resumed"] is True and options["session"].id == previous.id
    assert options["mcp"] is manager
    assert agent.history == [goal, summary] and agent.state.open == ["实现"]
    assert not agent.provider.seen


def test_stdio_accepts_configured_mcp_and_keeps_startup_in_event_loop(tmp_path, monkeypatch):
    from app import stdio

    manager = object()
    opened = []
    monkeypatch.setattr(stdio, "run_stdio", lambda agent, config, **kwargs: opened.append(kwargs))
    monkeypatch.setattr(entrypoint.McpManager, "from_env", lambda *args: manager)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path, mcp_servers="{}"))
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake", "--stdio"])

    entrypoint.main()

    assert opened[0]["mcp"] is manager and opened[0]["resumed"] is False


def test_stdio_eval_still_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vibe", "--stdio", "--eval"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "eval" in capsys.readouterr().err


def test_list_sessions_does_not_initialize_provider_or_mcp(tmp_path, monkeypatch, capsys):
    session = SessionStore.new(tmp_path, "m")
    session.append_message(Message("user", "查找这条会话\n不要展示正文"))
    session.append_message(Message("assistant", "private response"))
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr(entrypoint, "build_provider", lambda *a, **k: pytest.fail("provider initialized"))
    monkeypatch.setattr(entrypoint.McpManager, "from_env", lambda *a: pytest.fail("MCP initialized"))
    monkeypatch.setattr(sys, "argv", ["vibe", "--list-sessions"])
    before = session.events_path.read_bytes()

    entrypoint.main()

    output = capsys.readouterr().out
    assert session.id in output and "查找这条会话" in output and "2" in output
    assert "不要展示正文" not in output and "private response" not in output
    assert session.events_path.read_bytes() == before


def test_session_flag_restores_exact_conversation_checkpoint_without_auto_run(tmp_path, monkeypatch):
    from app import stdio

    previous = SessionStore.new(tmp_path, "fake", purpose="test")
    goal = Message("user", "实现原来的任务")
    summary = Message("assistant", "压缩的工作上下文", name="history_summary_l2")
    previous.append_message(goal)
    previous.append_message(Message("assistant", "原始历史"))
    previous.save_context([goal, summary])
    previous.save_state(TaskState(goal="实现原来的任务"))
    newer = SessionStore.new(tmp_path, "fake", purpose="test")
    newer.append_message(Message("user", "context smoke"))
    opened = []
    monkeypatch.setattr(stdio, "run_stdio", lambda agent, config, **kwargs: opened.append((agent, kwargs)))
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake", "--stdio", "--session", previous.id])

    entrypoint.main()

    agent, options = opened[0]
    assert options["resumed"] is True and options["session"].id == previous.id
    assert agent.history == [goal, summary] and agent.provider.seen == []


@pytest.mark.parametrize("selection", ["missing", "../outside", "/tmp/outside"])
def test_invalid_or_missing_session_fails_before_provider(tmp_path, monkeypatch, capsys, selection):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr(entrypoint, "build_provider", lambda *a, **k: pytest.fail("provider initialized"))
    monkeypatch.setattr(sys, "argv", ["vibe", "--session", selection])

    with pytest.raises(SystemExit) as error:
        entrypoint.main()

    assert error.value.code in {1, 2}
    assert "session" in capsys.readouterr().err
    assert not (tmp_path / ".vibe").exists()


def test_session_selection_flags_are_mutually_exclusive(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vibe", "--continue", "--session", "old"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "not allowed" in capsys.readouterr().err
