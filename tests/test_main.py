import sys

import pytest

from app import __main__ as entrypoint
from app.config import Config
from app.session import SessionStore
from core.models import Message
from core.state import TaskState


def test_retired_textual_entry_points_to_ink(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr(sys, "argv", ["vibe"])

    with pytest.raises(SystemExit) as error:
        entrypoint.main()

    assert error.value.code == 2
    assert "./run_ink.sh" in capsys.readouterr().err


def test_invalid_mcp_config_exits_before_backend(tmp_path, monkeypatch, capsys):
    from app import stdio

    opened = []
    monkeypatch.setattr(stdio, "run_stdio", lambda *args, **kwargs: opened.append(args))
    monkeypatch.setattr(
        entrypoint.Config,
        "from_env",
        lambda: Config(workspace=tmp_path, mcp_servers="{not json"),
    )
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake", "--stdio"])

    with pytest.raises(SystemExit) as exc:
        entrypoint.main()

    assert exc.value.code == 1
    assert "invalid MCP config" in capsys.readouterr().err
    assert opened == []


@pytest.mark.parametrize("chat_only", [False, True])
def test_mcp_is_passed_to_backend_without_starting_it(tmp_path, monkeypatch, chat_only):
    from app import stdio

    class Manager:
        servers = [object()]

        async def register_into(self, registry, approval):
            pytest.fail("entrypoint must leave MCP startup to the backend")

        async def close(self):
            pytest.fail("entrypoint must leave MCP cleanup to the backend")

    manager = Manager()
    opened = []
    monkeypatch.setattr(stdio, "run_stdio", lambda agent, config, **kwargs: opened.append((agent, kwargs)))
    monkeypatch.setattr(entrypoint.McpManager, "from_env", lambda *args: manager)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    argv = ["vibe", "--fake", "--stdio"] + (["--chat-only"] if chat_only else [])
    monkeypatch.setattr(sys, "argv", argv)

    entrypoint.main()

    agent, options = opened[0]
    assert options["mcp"] is (None if chat_only else manager)
    assert agent.tools_enabled is not chat_only


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
    monkeypatch.setattr(sys, "argv", ["vibe", "--stdio", "--session", selection])

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
