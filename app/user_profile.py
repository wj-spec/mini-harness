"""用户偏好：读取用户级 profile，只注入已确认、未过期且与当前任务相关的条目。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

MAX_USER_PROFILE_CHARS = 3000
MAX_INJECT_CHARS = 800
MAX_INJECT_ENTRIES = 3
CURRENT_VERSION = 2
DEFAULT_PROFILE = Path.home() / ".vibe" / "user-profile.json"
_KINDS = {"preference", "constraint", "goal", "fact"}
_SCOPES = {"global", "project", "workflow"}
_STATUSES = {"proposed", "confirmed", "stale"}


@dataclass(frozen=True)
class UserPreference:
    text: str
    source: str = "user"
    status: str = "confirmed"
    id: str = ""
    kind: str = "preference"
    scope: str = "global"
    triggers: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    confidence: float = 1.0
    created_at: str = ""
    updated_at: str = ""
    last_confirmed_at: str = ""
    expires_at: str = ""


@dataclass
class UserProfile:
    enabled: bool = True
    entries: list[UserPreference] = field(default_factory=list)


def profile_file(override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override)
    configured = os.getenv("VIBE_USER_PROFILE")
    return Path(configured) if configured else DEFAULT_PROFILE


def _stable_id(text: str, source: str, index: int) -> str:
    digest = hashlib.sha1(f"{index}\0{text}\0{source}".encode("utf-8")).hexdigest()
    return f"pref-{digest[:12]}"


def _strings(value: object, limit: int = 20) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value[:limit] if isinstance(item, str) and item.strip())


def _timestamp(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def load_user_profile(override: str | Path | None = None) -> UserProfile:
    path = profile_file(override)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return UserProfile()
    if not isinstance(payload, dict) or payload.get("version") not in {1, 2}:
        return UserProfile()
    version = int(payload["version"])
    enabled = payload.get("enabled", True)
    entries: list[UserPreference] = []
    raw_entries = payload.get("entries", [])
    if not isinstance(raw_entries, list):
        return UserProfile(enabled if isinstance(enabled, bool) else True, [])
    for index, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        source = item.get("source", "user")
        status = item.get("status", "confirmed")
        if not isinstance(text, str) or not text.strip():
            continue
        if not isinstance(source, str) or not isinstance(status, str) or status not in _STATUSES:
            continue
        raw_scope = item.get("scope", "global")
        raw_kind = item.get("kind", "preference")
        scope = raw_scope if isinstance(raw_scope, str) and raw_scope in _SCOPES else "global"
        kind = raw_kind if isinstance(raw_kind, str) and raw_kind in _KINDS else "preference"
        raw_confidence = item.get("confidence", 1.0 if status == "confirmed" else 0.5)
        confidence = raw_confidence if isinstance(raw_confidence, (int, float)) else 1.0
        confidence = min(1.0, max(0.0, float(confidence)))
        entry_id = item.get("id") if isinstance(item.get("id"), str) and item.get("id") else ""
        if version == 2 and not entry_id:
            entry_id = _stable_id(text.strip(), source.strip() or "user", index)
        entries.append(UserPreference(
            text=text.strip(),
            source=source.strip() or "user",
            status=status,
            id=entry_id,
            kind=kind,
            scope=scope,
            triggers=_strings(item.get("triggers")),
            evidence=_strings(item.get("evidence")),
            confidence=confidence,
            created_at=_timestamp(item.get("created_at")),
            updated_at=_timestamp(item.get("updated_at")),
            last_confirmed_at=_timestamp(item.get("last_confirmed_at")),
            expires_at=_timestamp(item.get("expires_at")),
        ))
    return UserProfile(enabled if isinstance(enabled, bool) else True, entries)


def _expired(entry: UserPreference, now: datetime) -> bool:
    if not entry.expires_at:
        return False
    try:
        value = entry.expires_at.replace("Z", "+00:00")
        return datetime.fromisoformat(value) <= now
    except ValueError:
        return False


def _tokens(text: str) -> tuple[str, ...]:
    """触发器为空时用文本关键词做轻量相关性匹配；不引入分词依赖。"""
    lowered = text.casefold()
    words = re.findall(r"[a-z][a-z0-9_+.-]{2,}", lowered)
    cjk = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
    for run in cjk:
        words.extend(run[i:i + 2] for i in range(max(0, len(run) - 1)))
    return tuple(dict.fromkeys(words))


def _matches(entry: UserPreference, task: str) -> int:
    haystack = task.casefold()
    triggers = [trigger.casefold() for trigger in entry.triggers if trigger.strip()]
    if triggers:
        return sum(1 for trigger in triggers if trigger in haystack)
    return sum(1 for token in _tokens(entry.text) if token in haystack)


def _sort_key(entry: UserPreference) -> tuple[float, float, str]:
    return (entry.confidence, 1.0 if entry.last_confirmed_at else 0.0,
            entry.last_confirmed_at or entry.updated_at or entry.created_at)


def select_user_preferences(
    entries: list[UserPreference], task: str, *,
    max_entries: int = MAX_INJECT_ENTRIES,
    max_chars: int = MAX_INJECT_CHARS,
    now: datetime | None = None,
) -> list[UserPreference]:
    """只选已确认、未过期、与任务相关的偏好；触发器为空时保留旧的 always 注入语义。"""
    current = now or datetime.now(timezone.utc)
    confirmed = [entry for entry in entries if entry.status == "confirmed" and not _expired(entry, current)]
    matched = [entry for entry in confirmed if entry.triggers and _matches(entry, task) > 0]
    matched.sort(key=lambda entry: (_matches(entry, task),) + _sort_key(entry), reverse=True)
    selected: list[UserPreference] = []
    used = 0
    for entry in [*matched, *[entry for entry in confirmed if not entry.triggers and entry not in matched]]:
        if len(selected) >= max_entries:
            break
        size = len(entry.text) + len(entry.source) + 8
        if selected and used + size > max_chars:
            break
        selected.append(entry)
        used += size
    return selected


def render_user_background(entries: list[UserPreference], max_chars: int = MAX_USER_PROFILE_CHARS) -> str | None:
    """只渲染已确认的偏好；待确认观察与与当前任务无关的条目不应传入。"""
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
