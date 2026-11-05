#!/usr/bin/env python3
"""为 07 attach 人工验收创建一个隔离的 test session + goal task。

用法（项目根目录）：
  ./.venv/bin/python3 scripts/acceptance_seed_task.py --workspace /tmp/vibe-acceptance
  ./.venv/bin/python3 scripts/acceptance_seed_task.py --workspace /tmp/vibe-acceptance --with-worktree
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.runtime.tasks import TaskStore  # noqa: E402
from app.runtime.workspace import WorktreeManager  # noqa: E402
from app.session.store import SessionStore  # noqa: E402
from core.contracts import Message  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--objective", default="验收：后台任务 attach 与控制面")
    parser.add_argument("--with-worktree", action="store_true", help="额外创建 worktree + 一个改动，用于 merge 验收")
    args = parser.parse_args()
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise SystemExit(f"workspace not found: {workspace}")
    session = SessionStore.new(workspace, "fake", purpose="test")
    session.append_message(Message("user", args.objective))
    task = TaskStore(session.root).create(
        kind="goal", objective=args.objective, workspace=str(workspace), mode="goal",
    )
    payload = {
        "workspace": str(workspace),
        "session_id": session.id,
        "session_root": str(session.root),
        "task_id": task.task_id,
        "task_dir": str(session.root / "tasks" / task.task_id),
    }
    if args.with_worktree:
        worktree = WorktreeManager(workspace).create(task.task_id, session.id)
        (worktree.path / "ACCEPTANCE_MERGE.txt").write_text("created for manual merge acceptance\n", encoding="utf-8")
        payload["worktree"] = str(worktree.path)
        payload["worktree_branch"] = worktree.branch
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
