from __future__ import annotations

import difflib


def unified_diff(
    old: str,
    new: str,
    path: str,
    context: int = 3,
    max_lines: int = 200,
    max_chars: int = 20000,
) -> str:
    diff = list(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=context,
        )
    )
    if not diff:
        return "(no changes)"
    lines = "".join(diff).splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["...[diff truncated]..."]
    return "\n".join(lines)[:max_chars]
