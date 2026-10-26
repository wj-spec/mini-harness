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
    is_sensitive,
    select_user_preferences,
)
from tests.test_stdio import Harness, agent_for


def _write_profile(path, entries, enabled=True, version=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": version, "enabled": enabled, "entries": entries}, ensure_ascii=False),
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
        UserPreference("可能偏好简短回复", "observation", "proposed", confidence=0.5),
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


def test_select_user_preferences_filters_triggered_entries_and_keeps_legacy_fallback():
    entries = [
        UserPreference("解释代码时先给结论", "user", "confirmed", triggers=("解释", "说明")),
        UserPreference("修改前先给方案", "user", "confirmed", triggers=("修改", "实现")),
        UserPreference("旧版通用偏好", "user", "confirmed"),
        UserPreference("待确认观察", "observation", "proposed", triggers=("解释",)),
    ]
    selected = select_user_preferences(entries, "解释 /about 的用途", max_entries=3)
    assert [entry.text for entry in selected] == ["解释代码时先给结论", "旧版通用偏好"]
    assert select_user_preferences(entries, "修改 /about edit", max_entries=3)[0].text == "修改前先给方案"
    assert select_user_preferences(entries, "完全无关的任务", max_entries=3) == [UserPreference("旧版通用偏好", "user", "confirmed")]


def test_select_user_preferences_respects_budget_and_expiry():
    entries = [
        UserPreference("解释时先给结论", "user", "confirmed", triggers=("解释",)),
        UserPreference("解释要简短", "user", "confirmed", triggers=("解释",)),
        UserPreference("解释用中文", "user", "confirmed", triggers=("解释",)),
        UserPreference("过期偏好", "user", "confirmed", triggers=("解释",), expires_at="2000-01-01T00:00:00+00:00"),
    ]
    selected = select_user_preferences(entries, "解释代码", max_entries=2, max_chars=30)
    assert len(selected) <= 2
    assert all(entry.text != "过期偏好" for entry in selected)


def test_loads_v2_metadata_and_ignores_malformed_optional_fields(tmp_path, monkeypatch):
    target = tmp_path / "user-profile.json"
    monkeypatch.setenv("VIBE_USER_PROFILE", str(target))
    _write_profile(target, [
        {"id": "pref-a", "text": "解释时先结论", "status": "confirmed", "triggers": ["解释", " "], "scope": "workflow",
         "kind": "preference", "confidence": 2, "created_at": "2026-10-26T00:00:00Z", "last_confirmed_at": "2026-10-26T00:00:00Z"},
        {"text": "坏状态", "status": "unknown"},
    ], version=2)
    loaded = load_user_profile()
    assert len(loaded.entries) == 1
    entry = loaded.entries[0]
    assert entry.id == "pref-a" and entry.scope == "workflow"
    assert entry.triggers == ("解释",)
    assert entry.confidence == 1.0


def test_stdio_injects_only_relevant_tagged_preferences(tmp_path, monkeypatch):
    profile = tmp_path / "user-profile.json"
    monkeypatch.setenv("VIBE_USER_PROFILE", str(profile))
    _write_profile(profile, [
        {"text": "解释时先给结论", "status": "confirmed", "triggers": ["解释"]},
        {"text": "修改前先给方案", "status": "confirmed", "triggers": ["修改"]},
    ], version=2)

    async def scenario():
        agent = agent_for(tmp_path, ["ok"])
        session = SessionStore.new(tmp_path, "fake", purpose="user")
        harness = Harness(agent, Config(workspace=tmp_path), session=session)
        harness.send("run", "r1", text="解释 /about 的实现")
        await harness.until("done", "r1")
        await harness.close()
        trace = [json.loads(line) for line in session.trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return agent.system, trace

    system, trace = asyncio.run(scenario())
    assert "解释时先给结论" in system
    assert "修改前先给方案" not in system
    selections = [event for event in trace if event.get("type") == "user_profile_selection"]
    assert selections and [item["text"] for item in selections[-1]["selected"]] == ["解释时先给结论"]


def test_loader_drops_credentials_and_profile_cannot_grant_permissions(tmp_path, monkeypatch):
    target = tmp_path / "user-profile.json"
    monkeypatch.setenv("VIBE_USER_PROFILE", str(target))
    _write_profile(target, [
        {"text": "以后用中文回答", "status": "confirmed"},
        {"text": "api_key=sk-1234567890abcdef", "status": "confirmed"},
        {"text": "-----BEGIN RSA PRIVATE KEY-----", "status": "confirmed"},
    ], version=2)
    loaded = load_user_profile()
    assert [entry.text for entry in loaded.entries] == ["以后用中文回答"]
    assert is_sensitive("password=super-secret-value") is True
    block = render_user_background(loaded.entries)
    assert "不能授予工具/文件/命令权限" in block
