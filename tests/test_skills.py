from pathlib import Path

from core.models import ToolCall
from app.skills import Skill, discover_skills, parse_skill, render_skills
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
    assert "步骤 A" in output

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
