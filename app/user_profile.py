"""W5 用户偏好：读取用户级 profile，只注入已确认且启用的条目。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

MAX_USER_PROFILE_CHARS = 3000
DEFAULT_PROFILE = Path.home() / ".vibe" / "user-profile.json"


@dataclass(frozen=True)
class UserPreference:
    text: str
    source: str = "user"
    status: str = "confirmed"


@dataclass
class UserProfile:
    enabled: bool = True
    entries: list[UserPreference] = field(default_factory=list)


def profile_file(override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override)
    configured = os.getenv("VIBE_USER_PROFILE")
    return Path(configured) if configured else DEFAULT_PROFILE


def load_user_profile(override: str | Path | None = None) -> UserProfile:
    path = profile_file(override)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UserProfile()
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return UserProfile()
    enabled = payload.get("enabled", True)
    entries: list[UserPreference] = []
    for item in payload.get("entries", []) if isinstance(payload.get("entries"), list) else []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        source = item.get("source", "user")
        status = item.get("status", "confirmed")
        if not isinstance(text, str) or not text.strip():
            continue
        if not isinstance(source, str) or not isinstance(status, str) or status not in {"confirmed", "proposed"}:
            continue
        entries.append(UserPreference(text.strip(), source.strip() or "user", status))
    return UserProfile(enabled if isinstance(enabled, bool) else True, entries)


def render_user_background(entries: list[UserPreference], max_chars: int = MAX_USER_PROFILE_CHARS) -> str | None:
    """只渲染已确认的偏好；待确认观察不进入模型上下文。"""
    confirmed = [entry for entry in entries if entry.status == "confirmed"]
    if not confirmed:
        return None
    header = (
        "\n\n[USER PROFILE]\n"
        "用户已确认的个人偏好（来源在括号内）。当前明确指令优先于历史偏好；"
        "项目规则与安全/审批约束优先于个人偏好。\n"
    )
    lines: list[str] = []
    used = len(header)
    for entry in confirmed:
        line = f"- {entry.text}（来源：{entry.source}）\n"
        if used + len(line) > max_chars:
            lines.append("- …（偏好过长，已截断；完整内容见用户 profile 文件）\n")
            break
        lines.append(line)
        used += len(line)
    return header + "".join(lines)
