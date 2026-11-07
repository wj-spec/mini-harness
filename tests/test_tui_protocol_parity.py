"""TUI（TypeScript）与 Python 后端命令集合的一致性检查。"""

from __future__ import annotations

import re
from pathlib import Path

from app.stdio import COMMANDS

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_TS = REPO_ROOT / "tui" / "src" / "protocol.ts"


def _tui_backend_commands() -> set[str]:
    source = PROTOCOL_TS.read_text(encoding="utf-8")
    match = re.search(r"export const BACKEND_COMMANDS = \[(.*?)\] as const;", source, re.S)
    assert match, "BACKEND_COMMANDS not found"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_backend_command_sets_match():
    tui_commands = _tui_backend_commands()
    assert tui_commands == set(COMMANDS), (
        f"only in TUI: {sorted(tui_commands - set(COMMANDS))}; "
        f"only in Python: {sorted(set(COMMANDS) - tui_commands)}"
    )


def test_every_backend_command_has_description_entry():
    source = PROTOCOL_TS.read_text(encoding="utf-8")
    command_descriptions = source[source.index("export const COMMAND_DESCRIPTIONS"):]
    for command in sorted(_tui_backend_commands()):
        aliases = {f"/{command}", f"/{command.replace('_', ' ')}"}
        assert any(f"'{alias}'" in command_descriptions for alias in aliases), command
