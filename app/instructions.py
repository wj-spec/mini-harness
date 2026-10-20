from __future__ import annotations

from pathlib import Path

MAX_CHARS = 12_000


def load_agents_md(workspace: str | Path, max_chars: int = MAX_CHARS) -> str | None:
    """读取项目根 AGENTS.md；没有就退回 .vibe/AGENTS.md。"""
    root = Path(workspace)
    for relative in ("AGENTS.md", ".vibe/AGENTS.md"):
        path = root / relative
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text:
            return None
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[AGENTS.md truncated]..."
        return text
    return None
