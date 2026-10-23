import json

from app.agent import Agent
from app.project_profile import (
    MAX_BACKGROUND_CHARS,
    ProjectBackgroundEntry,
    load_project_background,
    profile_path,
    render_project_background,
)
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry


def _write_profile(workspace, entries):
    path = profile_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "entries": entries}, ensure_ascii=False), encoding="utf-8")


def test_loads_valid_entries_and_ignores_invalid_ones(tmp_path):
    _write_profile(tmp_path, [
        {"text": "Ink 是唯一界面入口", "source": "AGENTS.md"},
        {"text": "  ", "source": "empty"},
        {"source": "missing-text"},
        "not-a-dict",
    ])
    entries = load_project_background(tmp_path)
    assert entries == [ProjectBackgroundEntry("Ink 是唯一界面入口", "AGENTS.md")]


def test_missing_or_broken_profile_is_empty(tmp_path):
    assert load_project_background(tmp_path) == []
    path = profile_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    assert load_project_background(tmp_path) == []
    path.write_text(json.dumps({"version": 2, "entries": [{"text": "old"}]}), encoding="utf-8")
    assert load_project_background(tmp_path) == []


def test_render_keeps_sources_and_respects_cap():
    assert render_project_background([]) is None
    block = render_project_background([ProjectBackgroundEntry("规则 A", "docs/a.md")])
    assert "PROJECT BACKGROUND" in block and "规则 A" in block and "docs/a.md" in block

    long_entries = [ProjectBackgroundEntry(f"条目 {index} " + "x" * 200, "docs") for index in range(50)]
    capped = render_project_background(long_entries, max_chars=MAX_BACKGROUND_CHARS)
    assert len(capped) <= MAX_BACKGROUND_CHARS + 200
    assert "已截断" in capped


def test_agent_injects_and_replaces_project_background(tmp_path):
    agent = Agent(FakeProvider([]), ToolRegistry(tmp_path))
    agent.set_project_background([ProjectBackgroundEntry("旧背景", "user")])
    assert "旧背景" in agent.system
    assert "PROJECT BACKGROUND" in agent.system

    agent.set_project_background([ProjectBackgroundEntry("新背景", "user")])
    assert "新背景" in agent.system
    assert "旧背景" not in agent.system
