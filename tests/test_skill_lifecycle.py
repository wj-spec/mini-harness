import asyncio
import json
import threading

import pytest

from app.agent import Agent
from app.config import Config
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry
from app.skills import SkillManager
from core.budget import BudgetPolicy
from core.context import ContextBuilder
from core.models import Message, Response, ToolCall


def skill_file(root, name="review", body="PRIVATE_SKILL_STEPS"):
    path = root / ".vibe" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"description: Review a change\n\n{body}\n")
    return path


def sent_text(messages):
    return "\n".join(message.content for message in messages)


def test_model_activation_and_unload_replace_prompt_without_history_body(tmp_path):
    skill_file(tmp_path)
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("load_skill", {"name": "review"}, "load")]),
        Response(tool_calls=[ToolCall("unload_skill", {"name": "review"}, "unload")]),
        "完成",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path))
    assert "review" in agent.system and "PRIVATE_SKILL_STEPS" not in agent.system
    result = asyncio.run(agent.run("检查这个修改"))
    assert result.status == "done"
    assert "PRIVATE_SKILL_STEPS" not in sent_text(provider.seen[0])
    assert "PRIVATE_SKILL_STEPS" in provider.seen[1][0].content
    assert "PRIVATE_SKILL_STEPS" not in sent_text(provider.seen[2])
    assert "PRIVATE_SKILL_STEPS" not in sent_text(agent.history)
    assert not agent.tools.skill_manager.active


def test_manual_discovery_activation_and_unload_take_effect_without_model_calls(tmp_path):
    provider = FakeProvider(["第一次", "第二次"])
    agent = Agent(provider, ToolRegistry(tmp_path))
    path = skill_file(tmp_path)
    assert "review" in agent.manage_skills()
    assert "review" in agent.system
    assert "PRIVATE_SKILL_STEPS" not in agent.system
    agent.manage_skills("activate", "review")
    assert provider.seen == []
    asyncio.run(agent.run("使用技能"))
    agent.manage_skills("unload", "review")
    asyncio.run(agent.run("继续普通工作"))
    assert "PRIVATE_SKILL_STEPS" in provider.seen[0][0].content
    assert "PRIVATE_SKILL_STEPS" not in sent_text(provider.seen[1])
    assert path.exists()


def test_refreshed_catalog_and_active_body_match_context_snapshot(tmp_path):
    async def scenario():
        path = skill_file(tmp_path)
        agent = Agent(FakeProvider([]), ToolRegistry(tmp_path),
                      context=ContextBuilder(policy=BudgetPolicy()))
        agent.manage_skills("activate", "review")
        path.write_text("description: New summary\n\nUPDATED_BODY\n")
        agent.manage_skills("refresh")
        snapshot = await agent.loaded_context_snapshot("test")
        assert "UPDATED_BODY" in agent.system and "PRIVATE_SKILL_STEPS" not in agent.system
        active = next(block for block in snapshot["blocks"] if block["kind"] == "active_skill")
        assert active["source"] == str(path.resolve())
        assert "New summary" in agent.system
        agent.manage_skills("unload", "review")
        snapshot = await agent.loaded_context_snapshot("test")
        assert not any(block["kind"] == "active_skill" for block in snapshot["blocks"])
        assert agent.provider.seen == [] and agent.history == []

    asyncio.run(scenario())


def test_active_skill_survives_history_compaction_but_not_unload(tmp_path):
    skill_file(tmp_path)
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("load_skill", {"name": "review"}, "load")],
                 usage={"total_tokens": 160000}),
        "已使用", "普通回答",
    ])
    context = ContextBuilder(policy=BudgetPolicy(), keep_recent_groups=1)
    agent = Agent(provider, ToolRegistry(tmp_path, context=context), context=context,
                  max_run_tokens=1_000_000)
    events = []
    assert asyncio.run(agent.run("执行任务", trace=events.append)).status == "done"
    assert any(event["type"] == "history_compacted" for event in events)
    assert "PRIVATE_SKILL_STEPS" in provider.seen[1][0].content
    agent.manage_skills("unload", "review")
    asyncio.run(agent.run("继续"))
    assert "PRIVATE_SKILL_STEPS" not in sent_text(provider.seen[-1])


def test_old_skill_tool_bodies_are_not_reactivated_from_restored_history(tmp_path):
    skill_file(tmp_path)
    old = Message("tool", "# skill: review\nOld description\n\nOBSOLETE_INSTRUCTION",
                  name="load_skill", tool_call_id="old-load")
    history = [Message("user", "以前的任务"), Message("assistant", "", [
        ToolCall("load_skill", {"name": "review"}, "old-load")]), old]
    provider = FakeProvider(["继续"])
    agent = Agent(provider, ToolRegistry(tmp_path), history=history)
    asyncio.run(agent.run("恢复"))
    assert "OBSOLETE_INSTRUCTION" not in sent_text(provider.seen[0])
    assert "OBSOLETE_INSTRUCTION" in old.content
    assert not agent.tools.skill_manager.active


def test_chat_only_skips_catalog_and_rejects_activation(tmp_path):
    skill_file(tmp_path)
    agent = Agent(FakeProvider([]), ToolRegistry(tmp_path), tools_enabled=False)
    assert "PRIVATE_SKILL_STEPS" not in agent.system
    assert "[SKILLS]" not in agent.system
    with pytest.raises(ValueError, match="chat-only"):
        agent.manage_skills("activate", "review")


def test_skill_instructions_do_not_bypass_write_approval(tmp_path):
    skill_file(tmp_path, body="Write result.txt using write_file.")
    calls = []

    async def deny(call, preview):
        calls.append(call.name)
        return ApprovalDecision(False, "deny")

    provider = FakeProvider([
        Response(tool_calls=[ToolCall("load_skill", {"name": "review"}, "load")]),
        Response(tool_calls=[ToolCall("write_file", {"path": "result.txt", "content": "unsafe"}, "write")]),
        "写入被拒绝",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(deny))
    assert asyncio.run(agent.run("用技能写结果")).status == "done"
    assert calls == ["write_file"] and not (tmp_path / "result.txt").exists()
    assert any("denied_by_user" in message.content for message in agent.history)


def test_config_env_selects_static_skill_config(tmp_path, monkeypatch):
    skill_file(tmp_path)
    config_path = tmp_path / "project-skills.json"
    config_path.write_text(json.dumps({"activate": ["review"]}))
    monkeypatch.setenv("VIBE_SKILLS_CONFIG", str(config_path))
    config = Config.from_env()
    registry = ToolRegistry(tmp_path, skills_config=config.skills_config)
    agent = Agent(FakeProvider([]), registry)
    assert "PRIVATE_SKILL_STEPS" in agent.system


def test_recall_of_old_skill_body_returns_state_without_instruction_text(tmp_path):
    context = ContextBuilder()
    context._refs["old-skill"] = [Message("tool", "# skill: old\n\nOLD_SKILL_BODY",
                                        name="load_skill", tool_call_id="old")]
    registry = ToolRegistry(tmp_path, context=context)
    output = registry.recall("old-skill")
    assert "OLD_SKILL_BODY" not in output
    assert "load_skill" in output


def test_entrypoint_passes_skill_config_to_runtime(tmp_path, monkeypatch):
    from app import __main__ as entrypoint
    from app import stdio
    import sys

    skill_file(tmp_path)
    path = tmp_path / "skills-config.json"
    path.write_text(json.dumps({"activate": ["review"]}))
    opened = []

    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(
        workspace=tmp_path, skills_config=str(path)))
    monkeypatch.setattr(stdio, "run_stdio", lambda agent, config, **kwargs: opened.append(agent))
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake", "--stdio"])
    entrypoint.main()
    assert "PRIVATE_SKILL_STEPS" in opened[0].system


def test_invalid_static_config_fails_before_backend(tmp_path, monkeypatch, capsys):
    from app import __main__ as entrypoint
    from app import stdio
    import sys

    path = tmp_path / ".vibe" / "skills.json"
    path.parent.mkdir()
    path.write_text('{"paths": "not-a-list"}')
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr(stdio, "run_stdio", lambda *args, **kwargs: pytest.fail("backend started"))
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 1 and "invalid skills config" in capsys.readouterr().err


@pytest.mark.parametrize("outcome", ["timeout", "cancel"])
def test_failed_skill_scan_cannot_commit_late_activation(tmp_path, monkeypatch, outcome):
    skill_file(tmp_path)
    registry = ToolRegistry(tmp_path)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = SkillManager.refresh

    def blocked(manager):
        entered.set()
        assert release.wait(2)
        try:
            return original(manager)
        finally:
            finished.set()

    monkeypatch.setattr(SkillManager, "refresh", blocked)
    registry._tools["load_skill"].timeout = 0.03 if outcome == "timeout" else 1

    async def scenario():
        cancel = asyncio.Event()
        operation = asyncio.create_task(registry.execute_async(
            ToolCall("load_skill", {"name": "review"}), cancel=cancel))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.001)
            assert entered.is_set()
            if outcome == "cancel":
                cancel.set()
            ok, output = await asyncio.wait_for(operation, 0.3)
            assert not ok and outcome in output
            assert not registry.skill_manager.active
        finally:
            release.set()
            await asyncio.to_thread(finished.wait, 1)
            await asyncio.gather(operation, return_exceptions=True)
        assert not registry.skill_manager.active

    asyncio.run(scenario())


def test_temporary_body_is_automatically_released_but_registration_stays(tmp_path):
    skill_file(tmp_path)
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("load_skill", {"name": "review"}, "load")]),
        "已经使用技能", "下一项任务",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path))
    assert asyncio.run(agent.run("使用技能")).status == "done"
    assert "PRIVATE_SKILL_STEPS" in provider.seen[1][0].content
    assert not agent.tools.skill_manager.active
    assert "PRIVATE_SKILL_STEPS" not in agent.system
    assert "review: Review a change" in agent.system
    assert [skill.name for skill in agent.tools.skills] == ["review"]
    assert asyncio.run(agent.run("普通任务")).status == "done"
    assert "PRIVATE_SKILL_STEPS" not in sent_text(provider.seen[2])
    assert "review: Review a change" in provider.seen[2][0].content


@pytest.mark.parametrize("outcome", ["error", "cancel", "max_steps", "exception"])
def test_temporary_skills_are_released_on_all_run_exits(tmp_path, outcome):
    from app.events import StreamError

    skill_file(tmp_path)
    if outcome in {"error", "exception"}:
        class FailedProvider:
            async def stream(self, messages, tools):
                if outcome == "exception":
                    raise RuntimeError("provider exploded")
                yield StreamError("provider failed")

        provider = FailedProvider()
    else:
        provider = FakeProvider([Response(tool_calls=[
            ToolCall("load_skill", {"name": "review"}, "load")])])
    agent = Agent(provider, ToolRegistry(tmp_path), max_steps=1)
    agent.manage_skills("activate", "review")
    cancel = asyncio.Event()
    if outcome == "cancel":
        cancel.set()
    if outcome == "exception":
        with pytest.raises(RuntimeError, match="provider exploded"):
            asyncio.run(agent.run("任务", cancel=cancel))
    else:
        result = asyncio.run(agent.run("任务", cancel=cancel))
        assert result.status == {"error": "error", "cancel": "cancelled", "max_steps": "stopped"}[outcome]
    assert not agent.tools.skill_manager.active
    assert "PRIVATE_SKILL_STEPS" not in agent.system
    assert "review: Review a change" in agent.system


@pytest.mark.parametrize("failure", ["config", "message_callback", "trace_callback"])
def test_initialization_failures_also_release_temporary_skills(tmp_path, failure):
    skill_file(tmp_path)
    agent = Agent(FakeProvider([]), ToolRegistry(tmp_path))
    agent.manage_skills("activate", "review")
    if failure == "config":
        (tmp_path / ".vibe" / "skills.json").write_text("{broken")
        assert asyncio.run(agent.run("任务")).status == "error"
    else:
        def failed_callback(value):
            raise RuntimeError("callback failed")

        kwargs = {"on_message" if failure == "message_callback" else "trace": failed_callback}
        with pytest.raises(RuntimeError, match="callback failed"):
            asyncio.run(agent.run("任务", **kwargs))
    assert not agent.tools.skill_manager.active
    assert "PRIVATE_SKILL_STEPS" not in agent.system
    assert "review: Review a change" in agent.system


def test_budget_pause_releases_temporary_body_and_resume_can_reload(tmp_path):
    skill_file(tmp_path)
    provider = FakeProvider([
        Response("预算收尾", usage={"total_tokens": 10}),
        Response(tool_calls=[ToolCall("load_skill", {"name": "review"}, "resume-load")]),
        "继续完成",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path), max_run_tokens=1)
    agent.manage_skills("activate", "review")
    result = asyncio.run(agent.run("任务"))
    assert result.status == "stopped" and result.stop_reason == "token_budget"
    assert not agent.tools.skill_manager.active
    assert "review: Review a change" in agent.system
    agent.max_run_tokens = 0
    provider.i = 1
    assert asyncio.run(agent.run("继续任务")).status == "done"
    assert "PRIVATE_SKILL_STEPS" in provider.seen[-1][0].content
    assert not agent.tools.skill_manager.active


def test_loaded_snapshot_after_run_keeps_only_registered_catalog(tmp_path):
    async def scenario():
        skill_file(tmp_path)
        provider = FakeProvider([
            Response(tool_calls=[ToolCall("load_skill", {"name": "review"}, "load")]),
            "已完成",
        ])
        context = ContextBuilder(policy=BudgetPolicy())
        agent = Agent(provider, ToolRegistry(tmp_path), context=context)
        events = []
        await agent.run("使用技能", trace=events.append)
        requests = [event for event in events if event["type"] == "context_snapshot"]
        assert any(block["kind"] == "active_skill" for block in requests[-1]["blocks"])
        snapshot = await agent.loaded_context_snapshot("test")
        assert any(block["kind"] == "skills" for block in snapshot["blocks"])
        assert not any(block["kind"] == "active_skill" for block in snapshot["blocks"])

    asyncio.run(scenario())
