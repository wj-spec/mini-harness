import asyncio
import json
import sys

import pytest

from app import __main__ as entrypoint
from app.agent import Agent
from app.config import Config
from app.providers.fake import FakeProvider
from app.session import SessionStore
from app.stdio import serve
from app.tools import ToolRegistry
from core.models import Message


def configure(tmp_path, monkeypatch):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr(entrypoint, "build_provider", lambda *a, **k: FakeProvider(["unused"]))
    opened = []
    monkeypatch.setattr(entrypoint, "_run", lambda config, session, *a, **k: opened.append(session))
    return opened


@pytest.mark.parametrize("flags", [["--fake"], ["--provider", "fake"], ["--test-session"]])
def test_test_modes_label_new_sessions(tmp_path, monkeypatch, flags):
    opened = configure(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["vibe", *flags])

    entrypoint.main()

    assert json.loads(opened[0].meta_path.read_text())["purpose"] == "test"


def test_normal_mode_labels_new_user_session(tmp_path, monkeypatch):
    opened = configure(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["vibe"])
    entrypoint.main()
    assert json.loads(opened[0].meta_path.read_text())["purpose"] == "user"


def test_normal_continue_ignores_newer_marked_test(tmp_path, monkeypatch):
    previous = SessionStore.new(tmp_path, "m")
    previous.append_message(Message("user", "真实工作"))
    smoke = SessionStore.new(tmp_path, "m", purpose="test")
    smoke.append_message(Message("user", "验证"))
    opened = configure(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["vibe", "--continue"])
    entrypoint.main()
    assert opened[0].id == previous.id


@pytest.mark.parametrize("flags", [[], ["--fake"]])
def test_explicit_session_id_cannot_cross_purpose(tmp_path, monkeypatch, flags):
    purpose = "user" if flags else "test"
    session = SessionStore.new(tmp_path, "m", purpose=purpose)
    session.append_message(Message("user", "不能跨范围加载"))
    configure(tmp_path, monkeypatch)
    monkeypatch.setattr(entrypoint, "build_provider", lambda *a, **k: pytest.fail("provider initialized"))
    monkeypatch.setattr(sys, "argv", ["vibe", *flags, "--session", session.id])
    before = {path.name: path.read_bytes() for path in session.root.iterdir() if path.is_file()}
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 1
    assert {path.name: path.read_bytes() for path in session.root.iterdir() if path.is_file()} == before


def test_test_mode_continues_only_test_session(tmp_path, monkeypatch):
    smoke = SessionStore.new(tmp_path, "m", purpose="test")
    smoke.append_message(Message("user", "测试工作"))
    normal = SessionStore.new(tmp_path, "m")
    normal.append_message(Message("user", "较新的正常工作"))
    opened = configure(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["vibe", "--test-session", "--continue"])
    entrypoint.main()
    assert opened[0].id == smoke.id


@pytest.mark.parametrize("flags,purpose", [([], "user"), (["--test-session"], "test"), (["--fake"], "test")])
def test_session_listing_uses_current_purpose_without_provider(tmp_path, monkeypatch, capsys, flags, purpose):
    normal = SessionStore.new(tmp_path, "m")
    normal.append_message(Message("user", "正常工作"))
    smoke = SessionStore.new(tmp_path, "m", purpose="test")
    smoke.append_message(Message("user", "验证工作"))
    configure(tmp_path, monkeypatch)
    monkeypatch.setattr(entrypoint, "build_provider", lambda *a, **k: pytest.fail("provider initialized"))
    monkeypatch.setattr(sys, "argv", ["vibe", *flags, "--list-sessions"])
    entrypoint.main()
    output = capsys.readouterr().out
    expected, hidden = (normal, smoke) if purpose == "user" else (smoke, normal)
    assert expected.id in output and hidden.id not in output


@pytest.mark.parametrize("purpose", ["user", "test"])
def test_stdio_announces_recorded_purpose_without_running_model(tmp_path, purpose):
    async def scenario():
        session = SessionStore.new(tmp_path, "m", purpose=purpose)
        agent = Agent(FakeProvider(["unused"]), ToolRegistry(tmp_path))
        events = []

        async def requests():
            yield json.dumps({"v": 1, "type": "shutdown", "id": "close"})

        await serve(agent, Config(workspace=tmp_path), session, requests=requests(), emit=events.append)
        assert events[0]["type"] == "ready" and events[0]["session_purpose"] == purpose
        assert agent.provider.seen == []

    asyncio.run(scenario())


def test_direct_fake_stdio_creation_also_marks_test_session(tmp_path):
    async def scenario():
        agent = Agent(FakeProvider(["unused"]), ToolRegistry(tmp_path))
        events = []

        async def requests():
            yield json.dumps({"v": 1, "type": "shutdown", "id": "close"})

        await serve(agent, Config(workspace=tmp_path), requests=requests(), emit=events.append)
        assert events[0]["session_purpose"] == "test"
        assert SessionStore.latest(tmp_path) is None
        assert SessionStore.open_existing(tmp_path, events[0]["session_id"]) is None
        assert SessionStore.open_existing(tmp_path, events[0]["session_id"], purpose="test") is not None

    asyncio.run(scenario())


@pytest.mark.parametrize("purpose", ["user", "test"])
def test_textual_title_marks_test_session(tmp_path, purpose):
    from app.tui import VibeApp

    session = SessionStore.new(tmp_path, "m", purpose=purpose)
    agent = Agent(FakeProvider(["unused"]), ToolRegistry(tmp_path))
    app = VibeApp(Config(workspace=tmp_path), agent, session=session)
    assert ("测试会话" in app.title) is (purpose == "test")
