"""无人值守 profile 红队清单（09 P1）：只读检查默认拒绝矩阵，不执行任何工具。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.safety.unattended import UnattendedProfile, load_profile  # noqa: E402

CASES = (
    ("read_file", {"worktree": True}, True),
    ("write_file", {"worktree": True}, True),
    ("write_file", {"worktree": False}, False),
    ("bash", {"worktree": True}, False),
    ("web_search", {"worktree": True}, False),
    ("mcp_filesystem_read", {"worktree": True}, False),
    ("browser_action", {"worktree": True}, False),
    ("unknown_tool", {"worktree": True}, False),
)


def run_checks(profile: UnattendedProfile) -> list[dict[str, Any]]:
    rows = []
    for tool, kwargs, expected in CASES:
        allowed = profile.allows_tool(tool, **kwargs)
        rows.append({
            "tool": tool,
            "worktree": kwargs["worktree"],
            "expected": expected,
            "allowed": allowed,
            "ok": allowed is expected,
            "reason": profile.denial_reason(tool, **kwargs),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=".", help="读取该工作区的 .vibe/unattended.json")
    args = parser.parse_args(argv)
    try:
        profile = load_profile(Path(args.workspace))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    rows = run_checks(profile)
    ok = all(row["ok"] for row in rows)
    print(json.dumps({"ok": ok, "profile": profile.profile_id, "checks": rows},
                     ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
