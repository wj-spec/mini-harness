"""W4 项目背景：读取、校验与渲染用户维护的项目 profile。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PROFILE_RELATIVE = Path(".vibe") / "project-profile.json"
MAX_BACKGROUND_CHARS = 4000


@dataclass(frozen=True)
class ProjectBackgroundEntry:
    text: str
    source: str = "user"


def profile_path(workspace: str | Path) -> Path:
    return Path(workspace) / PROFILE_RELATIVE


def load_project_background(workspace: str | Path) -> list[ProjectBackgroundEntry]:
    """读取工作区 profile；损坏、缺失或版本不匹配时返回空列表，不阻塞任务。"""
    path = profile_path(workspace)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return []
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        return []
    entries: list[ProjectBackgroundEntry] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        source = item.get("source", "user")
        if not isinstance(text, str) or not text.strip() or not isinstance(source, str):
            continue
        entries.append(ProjectBackgroundEntry(text.strip(), source.strip() or "user"))
    return entries


def render_project_background(
    entries: list[ProjectBackgroundEntry], max_chars: int = MAX_BACKGROUND_CHARS,
) -> str | None:
    if not entries:
        return None
    header = (
        "\n\n[PROJECT BACKGROUND]\n"
        "用户维护的项目背景摘要（来源在括号内）。与项目文件冲突时以文件为准，必要时重新读取确认。\n"
    )
    lines: list[str] = []
    used = len(header)
    for entry in entries:
        line = f"- {entry.text}（来源：{entry.source}）\n"
        if used + len(line) > max_chars:
            lines.append("- …（背景过长，已截断；完整内容见 .vibe/project-profile.json）\n")
            break
        lines.append(line)
        used += len(line)
    return header + "".join(lines)
