"""Skill：可复用的操作手册，渐进式披露。

目录约定（按顺序覆盖同名）：
  .vibe/skills/<name>/SKILL.md
  .vibe/skills/<name>.md

文件格式：开头几行 `key: value` 头信息（name / description），空行后是正文。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MAX_SKILL_CHARS = 8_000
MAX_SUMMARY_CHARS = 600
SKILL_FILE = "SKILL.md"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
HEADER_KEYS = ("name", "description")


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path

    def summary(self, limit: int = MAX_SUMMARY_CHARS) -> str:
        text = " ".join(self.description.split())
        if len(text) > limit:
            text = text[: limit - 3] + "..."
        return text

    def render(self) -> str:
        """load_skill 的返回内容。"""
        body = self.body.strip()
        if len(body) > MAX_SKILL_CHARS:
            body = body[:MAX_SKILL_CHARS] + "\n...[skill truncated]..."
        header = f"# skill: {self.name}\n{self.description.strip()}\n\n" if self.description else f"# skill: {self.name}\n\n"
        return header + body


def parse_skill(text: str, *, name: str, path: Path = Path("SKILL.md")) -> Skill | None:
    """解析单个 skill 文本；缺 name 时用文件名，正文为空则返回 None。"""
    lines = text.splitlines()
    meta: dict[str, str] = {}
    index = 0
    if lines and lines[0].strip() == "---":
        index = 1
        while index < len(lines) and lines[index].strip() != "---":
            key, _, value = lines[index].partition(":")
            key = key.strip().lower()
            if key in HEADER_KEYS:
                meta[key] = value.strip().strip("\"'")
            index += 1
        index = index + 1 if index < len(lines) else index
    else:
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                break
            key, sep, value = line.partition(":")
            if not sep or key.strip().lower() not in HEADER_KEYS:
                break
            meta[key.strip().lower()] = value.strip().strip("\"'")
            index += 1

    body = "\n".join(lines[index:]).strip()
    if not body:
        return None
    resolved = (meta.get("name") or name).strip()
    if not NAME_RE.match(resolved):
        return None
    description = meta.get("description", "").strip()
    if not description:
        # 退回正文首段，保证摘要里至少有点信息
        description = " ".join(part for part in body.splitlines() if part.strip())[:200]
    return Skill(name=resolved, description=description, body=body, path=path)


def load_skill(path: str | Path) -> Skill | None:
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    stem = file.parent.name if file.name == SKILL_FILE else file.stem
    return parse_skill(text, name=stem, path=file)


def discover_skills(workspace: str | Path) -> list[Skill]:
    """扫描 workspace 下的 skill 目录，按名称排序。"""
    base = Path(workspace) / ".vibe" / "skills"
    if not base.is_dir():
        return []
    found: dict[str, Skill] = {}
    for directory in sorted(p for p in base.iterdir() if p.is_dir()):
        skill = load_skill(directory / SKILL_FILE)
        if skill is not None:
            found.setdefault(skill.name, skill)
    for file in sorted(base.glob("*.md")):
        skill = load_skill(file)
        if skill is not None:
            found.setdefault(skill.name, skill)
    return [found[name] for name in sorted(found)]


def render_skills(skills: list[Skill], limit: int = MAX_SUMMARY_CHARS) -> str:
    """注入 system prompt 的摘要块；只给名字和一句话，正文靠 load_skill。"""
    if not skills:
        return ""
    lines = [
        "[SKILLS] 可复用的操作手册（只有摘要）。任务匹配时先调用 load_skill 读取正文步骤，再按步骤执行。",
    ]
    for skill in skills:
        lines.append(f"- {skill.name}: {skill.summary(limit)}")
    return "\n".join(lines)
