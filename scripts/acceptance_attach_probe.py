#!/usr/bin/env python3
"""低层 attach 探针：用于人工验收 controller/observer 与 v2 帧。

用法：
  ./.venv/bin/python3 scripts/acceptance_attach_probe.py \
    --workspace /tmp/vibe-acceptance --session <sid> --task-id <tid> \
    --mode observe --request cancel
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.runtime.worker import socket_path  # noqa: E402
from app.runtime.tasks import TaskStore  # noqa: E402


async def run(args) -> int:
    task_dir = TaskStore(Path(args.workspace) / ".vibe" / "sessions" / args.session).task_dir(args.task_id)
    path = socket_path(task_dir, args.task_id)
    if not path.exists():
        print(f"worker socket not found: {path}", file=sys.stderr)
        return 1
    reader, writer = await asyncio.open_unix_connection(str(path))
    writer.write((json.dumps({
        "v": 1, "type": "attach", "id": f"probe-{uuid.uuid4().hex[:6]}",
        "mode": args.mode, "take_control": args.take_control,
        "capabilities": ["task_snapshot", "approval_list", "approval_decide", "control_lease"],
    }) + "\n").encode())
    await writer.drain()
    print("FIRST", (await asyncio.wait_for(reader.readline(), 2)).decode().strip())
    if args.request == "snapshot":
        writer.write((json.dumps({"v": 1, "type": "task_snapshot_request", "id": "p-snap"}) + "\n").encode())
    elif args.request == "approvals":
        writer.write((json.dumps({"v": 1, "type": "approval_list_request", "id": "p-appr"}) + "\n").encode())
    elif args.request == "cancel":
        writer.write((json.dumps({"v": 1, "type": "cancel", "id": "p-cancel", "target": "probe"}) + "\n").encode())
    await writer.drain()
    deadline = asyncio.get_running_loop().time() + args.listen_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            line = await asyncio.wait_for(reader.readline(), 0.5)
        except asyncio.TimeoutError:
            continue
        if not line:
            break
        print("FRAME", line.decode().strip())
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--mode", choices=["observe", "control"], default="observe")
    parser.add_argument("--take-control", action="store_true")
    parser.add_argument("--request", choices=["snapshot", "approvals", "cancel", "none"], default="snapshot")
    parser.add_argument("--listen-seconds", type=float, default=2.0)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
