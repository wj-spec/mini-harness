import asyncio
import json

import pytest

from app.tools import ToolRegistry
from app.agent import Agent
from app.providers.fake import FakeProvider
from core.context import estimate_tokens
from core.models import Response, ToolCall


def put_skill(root, name, description, body="PRIVATE BODY"):
    path = root / ".vibe" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"name: {name}\ndescription: {description}\n\n{body}", encoding="utf-8")
    return path


def test_model_listing_is_bounded_but_manual_catalog_remains_complete(tmp_path):
    for index in range(12):
        put_skill(tmp_path, f"guide-{index:02}", "简介" * 300)
    registry = ToolRegistry(tmp_path)

    ok, result = registry.execute(ToolCall("list_skills"))
    assert ok and "12 matches" in result
    assert len([line for line in result.splitlines() if line.startswith("- ")]) == 5
    assert estimate_tokens(result) <= 800
    assert str(tmp_path) not in result and "PRIVATE BODY" not in result
    assert "guide-11" not in result
    assert "refine" in result.lower()
    assert registry.skill_manager.active == []

    status = registry.manage_skills("list")
    assert "guide-11" in status and str(tmp_path) in status
    assert len(registry.skill_entries()) == 12


def test_search_ranks_exact_name_and_matches_case_insensitive_descriptions(tmp_path):
    put_skill(tmp_path, "a-other", "Review code changes")
    put_skill(tmp_path, "review", "审查代码")
    put_skill(tmp_path, "unrelated", "Generate audio")
    registry = ToolRegistry(tmp_path)

    ok, result = asyncio.run(registry.execute_async(ToolCall(
        "list_skills", {"query": "REVIEW", "limit": 1})))
    assert ok and "2 matches" in result
    assert "- review:" in result and "- a-other:" not in result

    ok, result = registry.execute(ToolCall("list_skills", {"query": "code changes"}))
    assert ok and "- a-other:" in result and "- unrelated:" not in result
    ok, result = registry.execute(ToolCall("list_skills", {"query": "审查"}))
    assert ok and "- review:" in result and "- a-other:" not in result
    ok, result = registry.execute(ToolCall("list_skills", {"query": "no-such-topic"}))
    assert ok and "no matching" in result.lower()
    assert "- review:" not in result and "- a-other:" not in result


def test_search_truncates_summaries_and_honors_total_output_budget(tmp_path):
    for index in range(10):
        put_skill(tmp_path, f"guide-{index:02}", "汉" * 120 + "TRUNCATED_MARKER")
    registry = ToolRegistry(tmp_path)
    ok, result = registry.execute(ToolCall("list_skills", {"limit": 10}))
    assert ok and "10 matches" in result
    assert "TRUNCATED_MARKER" not in result
    assert estimate_tokens(result) <= 800
    assert "refine" in result.lower()
    assert len([line for line in result.splitlines() if line.startswith("- ")]) < 10


def test_oversized_name_does_not_hide_other_matching_skills(tmp_path):
    path = put_skill(tmp_path, "oversized", "Release workflow")
    path.write_text("name: " + "a" * 4000 + "\ndescription: Release workflow\n\nBODY", encoding="utf-8")
    put_skill(tmp_path, "normal", "Release workflow")
    registry = ToolRegistry(tmp_path)
    ok, result = registry.execute(ToolCall("list_skills", {"query": "release", "limit": 1}))
    assert ok and "2 matches" in result and "- normal:" in result
    assert "a" * 4000 not in result and estimate_tokens(result) <= 800


@pytest.mark.parametrize("args", [
    {"query": None}, {"query": []}, {"query": "字" * 201},
    {"limit": 0}, {"limit": 11}, {"limit": True}, {"limit": 1.5}, {"limit": "5"},
])
def test_search_rejects_invalid_parameters(tmp_path, args):
    registry = ToolRegistry(tmp_path)
    ok, result = registry.execute(ToolCall("list_skills", args))
    assert not ok and "ValueError" in result
    assert estimate_tokens(result) <= 800


def test_scan_errors_and_unknown_skill_errors_do_not_dump_catalog(tmp_path):
    for index in range(20):
        put_skill(tmp_path, f"hidden-{index:02}", "Only for matching tasks")
        path = tmp_path / ".vibe" / "skills" / f"broken-{index:02}.md"
        path.write_text("---\n", encoding="utf-8")
    registry = ToolRegistry(tmp_path)
    ok, result = registry.execute(ToolCall("list_skills", {"query": "nothing-matches"}))
    assert ok and "20" in result and "error" in result.lower()
    assert str(tmp_path) not in result and "broken-" not in result and "hidden-" not in result
    assert estimate_tokens(result) <= 800
    assert "broken-19" in registry.manage_skills("refresh")

    ok, result = registry.execute(ToolCall("load_skill", {"name": "missing"}))
    assert not ok and "list_skills" in result
    assert "hidden-" not in result


@pytest.mark.parametrize("asynchronous", [False, True])
def test_invalid_config_diagnostics_stay_bounded_for_model(tmp_path, asynchronous):
    put_skill(tmp_path, "guide", "A guide")
    registry = ToolRegistry(tmp_path)
    oversized_key = "unknown" * 1000
    (tmp_path / ".vibe" / "skills.json").write_text(json.dumps({oversized_key: []}), encoding="utf-8")
    call = ToolCall("list_skills", {"query": "guide"})
    ok, result = (asyncio.run(registry.execute_async(call)) if asynchronous else registry.execute(call))
    assert not ok and "skills refresh" in result and estimate_tokens(result) <= 800
    assert oversized_key not in result
    with pytest.raises(ValueError) as error:
        registry.manage_skills("refresh")
    assert oversized_key in str(error.value)


def test_search_rescans_new_vendor_directory_before_matching(tmp_path):
    registry = ToolRegistry(tmp_path)
    path = tmp_path / ".cursor" / "skills" / "fresh" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("description: Review code\n\nNEW PRIVATE BODY", encoding="utf-8")
    ok, result = registry.execute(ToolCall("list_skills", {"query": "review"}))
    assert ok and "- fresh:" in result and "NEW PRIVATE BODY" not in result
    assert str(path) not in result
    ok, receipt = registry.execute(ToolCall("load_skill", {"name": "fresh"}))
    assert ok and str(path) in receipt


def test_agent_discovers_then_loads_and_unloads_without_catalog_in_system(tmp_path):
    path = put_skill(tmp_path, "review", "Review a change")
    put_skill(tmp_path, "unrelated", "UNRELATED_SUMMARY")
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("list_skills", {"query": "review", "limit": 1}, "search")]),
        Response(tool_calls=[ToolCall("load_skill", {"name": "review"}, "load")]),
        Response(tool_calls=[ToolCall("unload_skill", {"name": "review"}, "unload")]),
        "完成",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path))
    assert asyncio.run(agent.run("检查这段说明")).status == "done"
    assert len(provider.seen) == 4
    for messages in provider.seen:
        assert "UNRELATED_SUMMARY" not in "\n".join(message.content for message in messages)
    search_result = next(message for message in provider.seen[1] if message.name == "list_skills")
    assert "- review:" in search_result.content and str(path) not in search_result.content
    assert estimate_tokens(search_result.content) <= 800
    assert "PRIVATE BODY" not in provider.seen[1][0].content
    assert "PRIVATE BODY" in provider.seen[2][0].content
    assert str(path) in provider.seen[2][0].content
    assert "PRIVATE BODY" not in "\n".join(message.content for message in provider.seen[3])
    assert agent.tools.skill_manager.active == []

