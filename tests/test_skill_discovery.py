import json
from pathlib import Path

import pytest

from app.skills import SkillManager, discover_skills
from app.tools import ToolRegistry
from core.models import ToolCall


def put_skill(root: Path, name: str, body: str = "正文") -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"name: {name}\ndescription: {name} 简介\n\n{body}", encoding="utf-8")
    return path


def configure(workspace: Path, **values) -> None:
    path = workspace / ".vibe" / "skills.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding="utf-8")


def test_default_discovery_combines_project_and_local_skill_directories(tmp_path, monkeypatch):
    workspace, home = tmp_path / "project", tmp_path / "home"
    monkeypatch.setenv("VIBE_SKILLS_HOME", str(home))
    expected = []
    for index, relative in enumerate((".vibe/skills", ".agents/skills", ".claude/skills", "skills")):
        name = f"project-{index}"
        expected.append(name)
        put_skill(workspace / relative, name)
    for index, relative in enumerate((".vibe/skills", ".agents/skills", ".codex/skills", ".claude/skills")):
        name = f"local-{index}"
        expected.append(name)
        put_skill(home / relative, name)

    manager = SkillManager(workspace)
    assert {skill.name for skill in manager.available} == set(expected)
    assert {skill.name for skill in discover_skills(workspace)} == set(expected)
    assert manager.active == []
    assert manager.errors == []


@pytest.mark.parametrize("scope,relative", [
    ("project", ".cursor/skills"),
    ("project", ".gemini/skills"),
    ("project", ".github/skills"),
    ("project", ".opencode/skills"),
    ("project", ".windsurf/skills"),
    ("project", ".devin/skills"),
    ("project", ".kiro/skills"),
    ("project", ".agent/skills"),
    ("local", ".cursor/skills"),
    ("local", ".gemini/skills"),
    ("local", ".copilot/skills"),
    ("local", ".config/opencode/skills"),
    ("local", ".codeium/windsurf/skills"),
    ("local", ".config/devin/skills"),
    ("local", ".kiro/skills"),
    ("local", ".gemini/config/skills"),
    ("local", ".gemini/antigravity-cli/skills"),
    ("local", ".gemini/antigravity/skills"),
])
def test_list_skills_rescans_common_agent_directories(tmp_path, monkeypatch, scope, relative):
    workspace, home = tmp_path / "project", tmp_path / "home"
    monkeypatch.setenv("VIBE_SKILLS_HOME", str(home))
    registry = ToolRegistry(workspace)
    assert registry.skills == []
    root = workspace if scope == "project" else home
    path = put_skill(root / relative, "new-skill", "PRIVATE BODY")

    ok, output = registry.execute(ToolCall("list_skills", {}))
    assert ok and "new-skill 简介" in output
    assert str(path) not in output and "PRIVATE BODY" not in output
    assert registry.skill_manager.active == []

    path.write_text("name: new-skill\ndescription: Updated summary\n\nUPDATED BODY", encoding="utf-8")
    ok, output = registry.execute(ToolCall("list_skills", {}))
    assert ok and "Updated summary" in output and "new-skill 简介" not in output
    path.unlink()
    ok, output = registry.execute(ToolCall("list_skills", {}))
    assert ok and "no skills" in output
    assert registry.skills == []


def test_common_agent_roots_preserve_priority_and_symlink_boundaries(tmp_path, monkeypatch):
    workspace, home = tmp_path / "project", tmp_path / "home"
    monkeypatch.setenv("VIBE_SKILLS_HOME", str(home))
    local = put_skill(home / ".config/opencode/skills", "shared", "LOCAL")
    project = put_skill(workspace / ".cursor/skills", "shared", "PROJECT")
    aliases = home / ".gemini/skills"
    aliases.mkdir(parents=True)
    (aliases / "alias").symlink_to(local.parent, target_is_directory=True)
    outside = put_skill(home / "documents", "outside")
    (aliases / "escape").symlink_to(outside.parent, target_is_directory=True)

    manager = SkillManager(workspace)
    assert [skill.path for skill in manager.available] == [project]
    assert sum("duplicate skill" in error for error in manager.errors) == 1
    assert any("escapes source root" in error for error in manager.errors)
    configure(workspace, discover=False, paths=[str(local.parent)])
    manager.refresh()
    assert [skill.path for skill in manager.available] == [local]
    assert manager.errors == []


def test_discovery_prioritizes_explicit_then_project_then_local_and_reports_conflicts(tmp_path, monkeypatch):
    workspace, home, explicit = tmp_path / "project", tmp_path / "home", tmp_path / "custom"
    monkeypatch.setenv("VIBE_SKILLS_HOME", str(home))
    local_path = put_skill(home / ".agents/skills", "shared", "LOCAL")
    project_path = put_skill(workspace / ".agents/skills", "shared", "PROJECT")
    explicit_path = put_skill(explicit, "shared", "EXPLICIT")
    put_skill(workspace / "skills", "project-only")
    configure(workspace, paths=[str(explicit)])

    manager = SkillManager(workspace)
    assert {skill.name for skill in manager.available} == {"shared", "project-only"}
    assert next(skill for skill in manager.available if skill.name == "shared").path == explicit_path
    assert any(str(local_path) in error and "duplicate" in error for error in manager.errors)
    assert any(str(project_path) in error and "duplicate" in error for error in manager.errors)
    configure(workspace)
    manager.refresh()
    assert next(skill for skill in manager.available if skill.name == "shared").path == project_path


def test_discover_false_limits_discovery_to_explicit_paths(tmp_path, monkeypatch):
    workspace, home, explicit = tmp_path / "project", tmp_path / "home", tmp_path / "custom"
    monkeypatch.setenv("VIBE_SKILLS_HOME", str(home))
    put_skill(home / ".agents/skills", "local")
    put_skill(workspace / ".vibe/skills", "project")
    expected = put_skill(explicit, "configured")
    configure(workspace, discover=False, paths=[str(explicit)])

    manager = SkillManager(workspace)
    assert [skill.path for skill in manager.available] == [expected]
    configure(workspace, discover=False)
    manager.refresh()
    assert manager.available == []


@pytest.mark.parametrize("value", ["false", 0, [], None])
def test_discovery_flag_requires_boolean(tmp_path, value):
    configure(tmp_path, discover=value)
    with pytest.raises(ValueError, match="discover.*bool"):
        SkillManager(tmp_path)


def test_discovery_reads_system_and_category_directories_with_bounded_depth(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("VIBE_SKILLS_HOME", str(home))
    root = home / ".codex/skills"
    put_skill(root / ".system", "system")
    put_skill(root / "category" / "subcategory", "nested")
    put_skill(root / "category" / "subcategory" / "too-deep", "ignored")
    for directory in ("references", "scripts", "assets", "resources", "node_modules", ".git"):
        put_skill(root / directory, f"skip-{directory.lstrip('.')}")
    parent = put_skill(root, "parent")
    put_skill(parent.parent, "child")

    manager = SkillManager(tmp_path / "project")
    assert {skill.name for skill in manager.available} == {"system", "nested", "parent"}


def test_discovery_deduplicates_symlinks_to_known_local_roots_without_diagnostic(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("VIBE_SKILLS_HOME", str(home))
    original = put_skill(home / ".agents/skills", "shared")
    aliases = home / ".claude/skills"
    aliases.mkdir(parents=True)
    (aliases / "alias").symlink_to(original.parent, target_is_directory=True)

    manager = SkillManager(tmp_path / "project")
    assert [skill.name for skill in manager.available] == ["shared"]
    assert manager.errors == []


def test_discovery_blocks_symlink_outside_known_sources_and_breaks_cycles(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("VIBE_SKILLS_HOME", str(home))
    original = put_skill(home / ".agents/skills", "safe")
    outside = put_skill(home / "documents", "outside")
    root = original.parent.parent
    (root / "escape").symlink_to(outside.parent, target_is_directory=True)
    (root / "cycle").symlink_to(root, target_is_directory=True)

    manager = SkillManager(tmp_path / "project")
    assert [skill.name for skill in manager.available] == ["safe"]
    assert any("escapes source" in error for error in manager.errors)


def test_missing_default_roots_are_silent_but_explicit_roots_report_errors(tmp_path):
    manager = SkillManager(tmp_path)
    assert manager.errors == []
    configure(tmp_path, paths=["missing-skills"])
    manager.refresh()
    assert any("does not exist" in error for error in manager.errors)


def test_clear_temporary_preserves_discovery_and_files(tmp_path):
    path = put_skill(tmp_path / ".vibe/skills", "one")
    second = put_skill(tmp_path / ".vibe/skills", "two")
    manager = SkillManager(tmp_path)
    manager.activate("two")
    manager.activate("one")
    assert manager.clear_temporary() == ["two", "one"]
    assert manager.active == []
    assert {skill.name for skill in manager.available} == {"one", "two"}
    assert path.exists() and second.exists()
    assert manager.clear_temporary() == []


def test_large_valid_skill_is_activated_without_silent_truncation(tmp_path):
    body = "每一步都保留。" * 2000
    put_skill(tmp_path / ".vibe/skills", "long-guide", body)
    manager = SkillManager(tmp_path)
    manager.activate("long-guide")
    assert body in manager.render_active()
    assert "truncated" not in manager.render_active()


def test_render_never_truncates_a_skill_before_explicit_activation_validation(tmp_path):
    from app.skills import MAX_SKILL_CHARS, Skill

    body = "x" * (MAX_SKILL_CHARS + 1)
    skill = Skill("over-limit", "description", body, tmp_path / "SKILL.md")
    assert body in skill.render()


def test_refresh_finds_new_local_skills_and_clears_deleted_active_skill(tmp_path, monkeypatch):
    home, workspace = tmp_path / "home", tmp_path / "project"
    monkeypatch.setenv("VIBE_SKILLS_HOME", str(home))
    manager = SkillManager(workspace)
    path = put_skill(home / ".agents/skills", "new-skill")
    manager.refresh()
    assert [skill.name for skill in manager.available] == ["new-skill"]
    manager.activate("new-skill")
    path.unlink()
    manager.refresh()
    assert manager.available == []
    assert manager.active == []


def test_registration_remains_in_catalog_after_temporary_cleanup(tmp_path):
    path = put_skill(tmp_path / ".vibe/skills", "registered", "PRIVATE BODY")
    manager = SkillManager(tmp_path)
    manager.activate("registered")
    manager.clear_temporary()
    assert "registered: registered 简介" in manager.render_catalog()
    assert str(path) in manager.render_catalog()
    assert "PRIVATE BODY" not in manager.render_catalog()
    assert "1 registered, 0 temporary_active" in manager.format_status()


def test_explicit_single_file_does_not_authorize_neighboring_files(tmp_path):
    workspace, external = tmp_path / "project", tmp_path / "external"
    external.mkdir()
    allowed = external / "allowed.md"
    allowed.write_text("name: allowed\n\nALLOWED", encoding="utf-8")
    neighbor = external / "neighbor.md"
    neighbor.write_text("name: neighbor\n\nPRIVATE", encoding="utf-8")
    directory = workspace / ".vibe/skills"
    directory.mkdir(parents=True)
    (directory / "neighbor.md").symlink_to(neighbor)
    configure(workspace, paths=[str(allowed)])

    manager = SkillManager(workspace)
    assert [skill.name for skill in manager.available] == ["allowed"]
    assert any("escapes source root" in error for error in manager.errors)
