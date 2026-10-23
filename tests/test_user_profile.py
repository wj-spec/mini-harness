import asyncio
import json

from app.agent import Agent
from app.config import Config
from app.providers.fake import FakeProvider
from app.session import SessionStore
from app.tools import ToolRegistry
from app.user_profile import (
    UserPreference,
    UserProfile,
    load_user_profile,
    profile_file,
    render_user_background,
)
from tests.test_stdio import Harness, agent_for


def _write_profile(path, entries, enabled=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "enabled": enabled, "entries": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_loads_confirmed_and_proposed_entries(tmp_path, monkeypatch):
    target = tmp_path / "user-profile.json"
    monkeypatch.setenv("VIBE_USER_PROFILE", str(target))
    _write_profile(target, [
        {"text": "回答先给结论", "source": "user", "status": "confirmed"},
        {"text": "可能偏好简短回复", "source": "observation", "status": "proposed"},
        {"text": "  ", "status": "confirmed"},
        {"text": "未知状态", "status": "weird"},
    ])
    loaded = load_user_profile()
    assert loaded.enabled is True
    assert loaded.entries == [
        UserPreference("回答先给结论", "user", "confirmed"),
        UserPreference("可能偏好简短回复", "observation", "proposed"),
    ]
    assert profile_file() == target


def test_render_only_injects_confirmed_entries_and_keeps_source():
    assert render_user_background([]) is None
    assert render_user_background([UserPreference("待观察", "obs", "proposed")]) is None
    block = render_user_background([UserPreference("回答先给结论", "user"), UserPreference("待观察", "obs", "proposed")])
    assert "USER PROFILE" in block and "回答先给结论" in block and "（来源：user）" in block
    assert "待观察" not in block
    assert "当前明确指令优先" in block


def test_missing_or_disabled_profile(tmp_path, monkeypatch):
    target = tmp_path / "user-profile.json"
    monkeypatch.setenv("VIBE_USER_PROFILE", str(target))
    assert load_user_profile() == UserProfile()
    _write_profile(target, [{"text": "偏好", "status": "confirmed"}], enabled=False)
    loaded = load_user_profile()
    assert loaded.enabled is False and loaded.entries == [UserPreference("偏好", "user")]


def test_agent_injects_and_replaces_user_background(tmp_path):
    agent = Agent(FakeProvider([]), ToolRegistry(tmp_path))
    agent.set_user_background([UserPreference("旧偏好", "user")])
    assert "旧偏好" in agent.system and "USER PROFILE" in agent.system
    agent.set_user_background([UserPreference("新偏好", "user")])
    assert "新偏好" in agent.system and "旧偏好" not in agent.system


def test_stdio_injects_only_for_user_sessions(tmp_path, monkeypatch):
    profile = tmp_path / "user-profile.json"
    monkeypatch.setenv("VIBE_USER_PROFILE", str(profile))
    _write_profile(profile, [{"text": "只在用户会话注入", "status": "confirmed"}])

    async def scenario(purpose):
        agent = agent_for(tmp_path, ["ok"])
        session = SessionStore.new(tmp_path, "fake", purpose=purpose)
        harness = Harness(agent, Config(workspace=tmp_path), session=session)
        harness.send("run", "r1", text="hi")
        await harness.until("done", "r1")
        await harness.close()
        return "只在用户会话注入" in agent.system

    assert asyncio.run(scenario("user")) is True
    assert asyncio.run(scenario("test")) is False
