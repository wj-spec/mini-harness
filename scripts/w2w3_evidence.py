#!/usr/bin/env python3
"""收集 W2/W3 真实验收证据：会话、状态、trace、审批、文件与残留进程。

用法：
    python3 scripts/w2w3_evidence.py --label w2-steer
    python3 scripts/w2w3_evidence.py --label w3-resume --session 20261023-...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / ".vibe" / "w2w3" / "workspace"
OUTPUT = ROOT / ".vibe" / "w2w3" / "evidence"


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


def file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def git(args: list[str]) -> str:
    result = subprocess.run(["git", *args], cwd=WORKSPACE, capture_output=True, text=True)
    return result.stdout.strip()


def running_processes() -> list[str]:
    lines: list[str] = []
    for pattern in ("slow_test.py", "slow_command.py"):
        result = subprocess.run(["pgrep", "-fl", pattern], capture_output=True, text=True)
        lines.extend(line for line in result.stdout.splitlines() if line.strip())
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="证据标签，例如 w2-steer")
    parser.add_argument("--session", help="指定会话 id，默认取最新")
    args = parser.parse_args()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    sessions_root = WORKSPACE / ".vibe" / "sessions"
    sessions = sorted(sessions_root.glob("*")) if sessions_root.is_dir() else []
    session = sessions_root / args.session if args.session else (sessions[-1] if sessions else None)

    evidence: dict = {
        "label": args.label,
        "stamp": stamp,
        "workspace": str(WORKSPACE),
        "session": session.name if session else None,
        "git": {
            "head": git(["rev-parse", "--short", "HEAD"]),
            "status": git(["status", "--porcelain"]),
            "diff": git(["diff"])[:8000],
        },
        "files": {name: file_hash(WORKSPACE / name) for name in ("calc.py", "test_calc.py", "slow_test.py", "slow_command.py")},
        "slow_test_processes": running_processes(),
        "sessions": [path.name for path in sessions],
    }
    if session is not None:
        state_path = session / "state.json"
        evidence["state"] = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else None
        trace = json_lines(session / "trace.jsonl")
        evidence["run_ends"] = [event for event in trace if event.get("type") == "run_end"][-5:]
        evidence["steering"] = [event for event in trace if event.get("event") == "steering_applied"][-5:]
        evidence["commands"] = [
            {key: event.get(key) for key in ("command", "status", "exit_code", "cancelled")}
            for event in trace if event.get("type") == "command_result"
        ][-20:]
        evidence["audit"] = json_lines(session / "audit.jsonl")[-20:]
        messages = json_lines(session / "events.jsonl")
        evidence["pending_tool_messages"] = [
            message for message in messages
            if isinstance(message.get("message"), dict)
            and message["message"].get("role") == "tool"
            and ("not executed" in str(message["message"].get("content", ""))
                 or "unknown" in str(message["message"].get("content", "")).lower())
        ][-10:]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / f"{args.label}-{stamp}.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"证据已保存：{path}")
    print(f"会话：{evidence['session']} | run_end 状态：{[event.get('status') for event in evidence.get('run_ends', [])]}")
    print(f"慢命令残留进程：{evidence['slow_test_processes'] or '无'}")
    print(f"工作区改动：{evidence['git']['status'] or '无'}")


if __name__ == "__main__":
    main()
