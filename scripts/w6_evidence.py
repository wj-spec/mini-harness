#!/usr/bin/env python3
"""W6 收益对照证据收集：会话指标、git 状态、用户消息数、实际注入的背景/偏好。

用法：
    python3 scripts/w6_evidence.py --label task-a-on --workspace <ws> --user-profile <profile>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".vibe" / "w6" / "evidence"


def json_lines(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def git(workspace: Path, args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=workspace, capture_output=True, text=True)
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--user-profile", default="")
    args = parser.parse_args()
    workspace = Path(args.workspace).resolve()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    sessions_root = workspace / ".vibe" / "sessions"
    sessions = sorted(sessions_root.glob("*"), key=lambda p: p.stat().st_mtime) if sessions_root.is_dir() else []
    session = sessions[-1] if sessions else None

    evidence: dict = {
        "label": args.label,
        "stamp": stamp,
        "workspace": str(workspace),
        "session": session.name if session else None,
        "git": {
            "head": git(workspace, ["rev-parse", "--short", "HEAD"]),
            "status": git(workspace, ["status", "--porcelain"]),
            "diff": git(workspace, ["diff"])[:8000],
        },
        "project_background": None,
        "user_profile": None,
    }
    project_profile = workspace / ".vibe" / "project-profile.json"
    if project_profile.is_file():
        evidence["project_background"] = json.loads(project_profile.read_text(encoding="utf-8"))
    if args.user_profile and Path(args.user_profile).is_file():
        evidence["user_profile"] = json.loads(Path(args.user_profile).read_text(encoding="utf-8"))

    if session is not None:
        trace = json_lines(session / "trace.jsonl")
        evidence["run_ends"] = [
            {key: event.get(key) for key in ("run_id", "status", "steps", "prompt_tokens", "completion_tokens", "total_tokens", "tool_calls", "duration_ms", "stop_reason")}
            for event in trace if event.get("type") == "run_end"
        ]
        evidence["commands"] = [
            {key: event.get(key) for key in ("command", "status", "exit_code")}
            for event in trace if event.get("type") == "command_result"
        ]
        events = json_lines(session / "events.jsonl")
        messages = [event["message"] for event in events if isinstance(event.get("message"), dict)]
        user_messages = [m for m in messages if m.get("role") == "user"]
        evidence["user_message_count"] = len(user_messages)
        evidence["user_messages"] = [str(m.get("content", ""))[:200] for m in user_messages]
        assistant = [m for m in messages if m.get("role") == "assistant" and m.get("content")]
        evidence["final_answer_tail"] = str(assistant[-1].get("content", ""))[-1500:] if assistant else None

    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / f"{args.label}-{stamp}.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"证据已保存：{path}")
    print(f"会话：{evidence['session']} | 用户消息数：{evidence.get('user_message_count', 0)}")
    for event in evidence.get("run_ends", []):
        print(f"  run {event.get('run_id')}: {event.get('status')} steps={event.get('steps')} tokens={event.get('total_tokens')} tools={event.get('tool_calls')}")
    print(f"git status：{evidence['git']['status'] or '无'}")


if __name__ == "__main__":
    main()
