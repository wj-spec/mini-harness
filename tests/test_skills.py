from pathlib import Path

import json
import os
import pytest

from core.models import ToolCall
from app.skills import MAX_SKILL_CHARS, Skill, discover_skills, parse_skill, render_skills
from app.tools import ToolRegistry


def write_skill(root: Path, name: str, text: str) -> Path:
    path = root / ".vibe" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_skill_header_and_body():
    skill = parse_skill(
        "name: releasenotes\ndescription: 写发布说明\n\n1. 收集提交\n2. 分组\n",
        name="fallback",
    )
    assert skill is not None
    assert skill.name == "releasenotes"
    assert skill.description == "写发布说明"
    assert "收集提交" in skill.render()
    assert skill.render().startswith("# skill: releasenotes")


def test_parse_skill_frontmatter():
    skill = parse_skill("---\nname: tdd\ndescription: \"先写测试\"\n---\n写测试再实现\n", name="x")
    assert skill is not None
    assert skill.name == "tdd"
    assert skill.description == "先写测试"
    assert "写测试再实现" in skill.body


def test_parse_skill_falls_back_to_stem_and_empty_body_is_skipped():
    skill = parse_skill("只是正文，没有头信息\n", name="fromfile")
    assert skill is not None
    assert skill.name == "fromfile"
    assert skill.description
    assert parse_skill("name: empty\n\n", name="empty") is None


def test_discover_skills_reads_dir_and_file_forms(tmp_path):
    write_skill(tmp_path, "alpha", "name: alpha\ndescription: 第一个\n\n步骤 A\n")
    (tmp_path / ".vibe" / "skills" / "beta.md").write_text(
        "description: 第二个\n\n步骤 B\n", encoding="utf-8"
    )
    skills = discover_skills(tmp_path)
    assert [s.name for s in skills] == ["alpha", "beta"]
    assert [s.summary() for s in skills] == ["第一个", "第二个"]


def test_discover_skills_handles_missing_dir(tmp_path):
    assert discover_skills(tmp_path) == []
    assert render_skills([]) == ""


def test_render_skills_only_exposes_summary(tmp_path):
    skills = [Skill("s", "做某事", "SECRET BODY", tmp_path)]
    rendered = render_skills(skills)
    assert "s: 做某事" in rendered
    assert "SECRET BODY" not in rendered
    assert "load_skill" in rendered


def test_registry_list_and_load_skill(tmp_path):
    write_skill(tmp_path, "alpha", "description: 第一个\n\n步骤 A\n")
    registry = ToolRegistry(tmp_path)
    assert {"list_skills", "load_skill"} <= set(registry.names())
    assert [s.name for s in registry.skills] == ["alpha"]

    ok, output = registry.execute(ToolCall("list_skills", {}))
    assert ok is True
    assert "alpha: 第一个" in output

    ok, output = registry.execute(ToolCall("load_skill", {"name": "alpha"}))
    assert ok is True
    assert "skill active: alpha" in output
    assert "步骤 A" not in output
    assert "步骤 A" in registry.skill_manager.render_active()

    ok, output = registry.execute(ToolCall("unload_skill", {"name": "alpha"}))
    assert ok is True
    assert registry.skill_manager.active == []

    ok, output = registry.execute(ToolCall("load_skill", {"name": "missing"}))
    assert ok is False
    assert "unknown skill" in output


def test_registry_without_skills(tmp_path):
    registry = ToolRegistry(tmp_path)
    ok, output = registry.execute(ToolCall("list_skills", {}))
    assert ok is True
    assert "no skills" in output
    ok, output = registry.execute(ToolCall("load_skill", {"name": "x"}))
    assert ok is False


def write_skill_config(root: Path, **config) -> Path:
    path = root / ".vibe" / "skills.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_skill_manager_discovers_then_activates_and_unloads(tmp_path):
    from app.skills import SkillManager

    path = write_skill(tmp_path, "alpha", "description: 第一个\n\nPRIVATE STEP\n")
    manager = SkillManager(tmp_path)
    assert [skill.name for skill in manager.available] == ["alpha"]
    assert manager.active == []
    assert "PRIVATE STEP" not in manager.render_catalog()
    assert "inactive" in manager.render_catalog()
    assert str(path) in manager.format_status()
    assert manager.render_active() == ""
    manager.activate("alpha")
    manager.activate("alpha")
    assert [skill.name for skill in manager.active] == ["alpha"]
    assert "PRIVATE STEP" in manager.render_active()
    assert "active" in manager.format_status()
    assert manager.deactivate("alpha") is True
    assert manager.deactivate("alpha") is False
    assert manager.render_active() == ""
    assert path.exists()
    with pytest.raises(KeyError, match="unknown skill"):
        manager.deactivate("missing")


def test_skill_manager_static_configuration_and_refresh_preserve_unload(tmp_path):
    from app.skills import SkillManager

    path = write_skill(tmp_path, "alpha", "description: 第一版\n\n步骤 A\n")
    write_skill(tmp_path, "hidden", "description: 不可用\n\n隐藏正文\n")
    write_skill_config(tmp_path, activate=["alpha"], disabled=["hidden"])
    manager = SkillManager(tmp_path)
    assert [skill.name for skill in manager.available] == ["alpha"]
    assert [skill.name for skill in manager.active] == ["alpha"]
    manager.deactivate("alpha")
    path.write_text("description: 第二版\n\n步骤 B\n", encoding="utf-8")
    manager.refresh()
    assert manager.active == []
    assert manager.available[0].description == "第二版"
    manager.activate("alpha")
    assert "步骤 B" in manager.render_active()
    path.unlink()
    manager.refresh()
    assert manager.active == []
    with pytest.raises(KeyError, match="unknown skill"):
        manager.activate("hidden")


def test_skill_manager_refreshes_active_content_and_new_disabled_config(tmp_path):
    from app.skills import SkillManager

    path = write_skill(tmp_path, "alpha", "步骤 A")
    manager = SkillManager(tmp_path)
    manager.activate("alpha")
    path.write_text("description: 更新\n\n步骤 B", encoding="utf-8")
    manager.refresh()
    assert "步骤 B" in manager.render_active()
    assert "步骤 A" not in manager.render_active()
    write_skill_config(tmp_path, disabled=["alpha"])
    manager.refresh()
    assert manager.active == []
    assert manager.available == []


def test_skill_manager_explicit_roots_and_duplicate_diagnostics(tmp_path):
    from app.skills import SkillManager

    external = tmp_path / "external"
    primary = write_skill(external, "alpha", "description: primary\n\nPRIMARY")
    secondary = write_skill(tmp_path, "alpha", "description: secondary\n\nSECONDARY")
    write_skill_config(tmp_path, paths=[str(primary.parent.parent), ".vibe/skills"])
    manager = SkillManager(tmp_path)
    assert manager.available[0].path == primary
    assert any("duplicate" in error and str(secondary) in error for error in manager.errors)
    assert "duplicate" in manager.format_status()
    manager.activate("alpha")
    assert "PRIMARY" in manager.render_active()
    assert "SECONDARY" not in manager.render_active()


def test_skill_manager_explicit_config_relative_path_and_single_skill(tmp_path):
    from app.skills import SkillManager

    path = write_skill(tmp_path / "external", "alpha", "步骤 A")
    custom = tmp_path / "custom.json"
    custom.write_text(json.dumps({"paths": [str(path)], "activate": ["alpha"]}), encoding="utf-8")
    manager = SkillManager(tmp_path, config_path="custom.json")
    assert [skill.name for skill in manager.active] == ["alpha"]
    assert manager.active[0].path.is_absolute()
    with pytest.raises(ValueError, match="missing.json"):
        SkillManager(tmp_path, config_path="missing.json")


def test_skill_manager_explicit_markdown_source_and_symlink_fallback_name(tmp_path):
    from app.skills import SkillManager

    source = tmp_path / "custom.md"
    source.write_text("description: 单文件\n\n正文", encoding="utf-8")
    write_skill_config(tmp_path, paths=[str(source)])
    manager = SkillManager(tmp_path)
    assert [skill.name for skill in manager.available] == ["custom"]
    base = tmp_path / ".vibe" / "skills"
    base.mkdir()
    original = base / "original.md"
    original.write_text("正文", encoding="utf-8")
    (base / "alias.md").symlink_to(original)
    write_skill_config(tmp_path, paths=[".vibe/skills"])
    manager.refresh()
    assert [skill.name for skill in manager.available] == ["alias"]


@pytest.mark.parametrize("config", [[], {"unknown": []}, {"paths": "skills"}, {"paths": [1]},
                                    {"paths": [""]}, {"activate": "alpha"}, {"activate": [False]},
                                    {"disabled": ["bad name"]}])
def test_skill_manager_rejects_invalid_configuration(tmp_path, config):
    from app.skills import SkillManager

    path = write_skill_config(tmp_path)
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="skill.*config"):
        SkillManager(tmp_path)


def test_skill_manager_invalid_refresh_is_atomic(tmp_path):
    from app.skills import SkillManager

    write_skill(tmp_path, "alpha", "步骤 A")
    manager = SkillManager(tmp_path)
    manager.activate("alpha")
    path = write_skill_config(tmp_path)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="skill.*config"):
        manager.refresh()
    assert [skill.name for skill in manager.active] == ["alpha"]


def test_skill_manager_rejects_symlink_escape_but_allows_explicit_external_root(tmp_path):
    from app.skills import SkillManager

    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    external_path = write_skill(external, "outside", "EXTERNAL BODY")
    inside = write_skill(workspace, "inside", "INSIDE BODY")
    (inside.parent.parent / "escape").symlink_to(external_path.parent, target_is_directory=True)
    (inside.parent.parent / "escape.md").symlink_to(external_path)
    manager = SkillManager(workspace)
    assert [skill.name for skill in manager.available] == ["inside"]
    assert any("outside" in error or "escape" in error for error in manager.errors)
    write_skill_config(workspace, paths=[str(external_path.parent.parent)], discover=False)
    manager.refresh()
    assert [skill.name for skill in manager.available] == ["outside"]


def test_skill_manager_default_root_cannot_point_outside_workspace(tmp_path):
    from app.skills import SkillManager

    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    skill = write_skill(external, "outside", "EXTERNAL BODY")
    (workspace / ".vibe").mkdir(parents=True)
    (workspace / ".vibe" / "skills").symlink_to(skill.parent.parent, target_is_directory=True)
    manager = SkillManager(workspace)
    assert manager.available == []
    assert manager.errors
    write_skill_config(workspace, paths=[".vibe/skills"])
    manager.refresh()
    assert [item.name for item in manager.available] == ["outside"]


def test_skill_manager_reports_broken_and_oversized_files(tmp_path):
    from app.skills import MAX_SKILL_FILE_BYTES, SkillManager

    write_skill(tmp_path, "broken", "---\nname: broken\nmissing closing delimiter")
    write_skill(tmp_path, "empty", "name: empty\n\n")
    write_skill(tmp_path, "large", "x" * (MAX_SKILL_FILE_BYTES + 1))
    manager = SkillManager(tmp_path)
    assert manager.available == []
    assert len(manager.errors) == 3
    assert any("large" in error and "limit" in error for error in manager.errors)


def test_skill_manager_never_silently_truncates_active_skills(tmp_path):
    from app.skills import SkillManager

    path = write_skill(tmp_path, "large", "description: short\n\n" + "x" * (MAX_SKILL_CHARS + 1))
    manager = SkillManager(tmp_path)
    with pytest.raises(ValueError, match="limit"):
        manager.activate("large")
    assert manager.active == []
    path.write_text("description: " + "x" * MAX_SKILL_CHARS + "\n\nbody", encoding="utf-8")
    manager.refresh()
    with pytest.raises(ValueError, match="limit"):
        manager.activate("large")
    path.write_text("description: short\n\nbody", encoding="utf-8")
    manager.activate("large")
    path.write_text("description: short\n\n" + "x" * MAX_SKILL_CHARS, encoding="utf-8")
    manager.refresh()
    assert manager.active == []
    assert any("large" in error and "limit" in error for error in manager.errors)


def test_skill_manager_bad_static_activation_is_reported(tmp_path):
    from app.skills import SkillManager

    write_skill(tmp_path, "large", "x" * (MAX_SKILL_CHARS + 1))
    write_skill_config(tmp_path, activate=["missing", "large"])
    manager = SkillManager(tmp_path)
    assert manager.active == []
    assert any("missing" in error for error in manager.errors)
    assert any("large" in error and "limit" in error for error in manager.errors)
    manager.refresh()
    assert any("missing" in error for error in manager.errors)
    assert any("large" in error and "limit" in error for error in manager.errors)


def test_skill_manager_refresh_does_not_activate_new_static_entries(tmp_path):
    from app.skills import SkillManager

    write_skill(tmp_path, "alpha", "正文")
    manager = SkillManager(tmp_path)
    write_skill_config(tmp_path, activate=["alpha"])
    manager.refresh()
    assert manager.active == []


def test_skill_manager_rejects_nonregular_skill_files(tmp_path):
    from app.skills import SkillManager

    base = tmp_path / ".vibe" / "skills"
    base.mkdir(parents=True)
    os.mkfifo(base / "blocked.md")
    manager = SkillManager(tmp_path)
    assert manager.available == []
    assert any("regular file" in error for error in manager.errors)


def test_parse_skill_supports_multiline_descriptions_and_rejects_unclosed_frontmatter():
    folded = parse_skill("---\nname: alpha\ndescription: >-\n  第一行\n  第二行\n---\n正文", name="fallback")
    literal = parse_skill("---\ndescription: |\n  第一行\n  第二行\n---\n正文", name="beta")
    assert folded is not None and folded.description == "第一行 第二行"
    assert literal is not None and literal.description == "第一行\n第二行"
    assert parse_skill("---\nname: alpha\n正文", name="fallback") is None


def test_skill_entries_expose_catalog_for_tui_menu(tmp_path):
    write_skill(tmp_path, "alpha", "name: alpha\ndescription: 第一个\n\n步骤 A\n")
    registry = ToolRegistry(tmp_path)
    entries = registry.skill_entries()
    assert entries == [{"name": "alpha", "summary": "第一个", "active": False,
                        "source": str(tmp_path / ".vibe" / "skills" / "alpha" / "SKILL.md")}]
    registry.manage_skills("activate", "alpha")
    assert registry.skill_entries()[0]["active"] is True
