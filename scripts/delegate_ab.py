"""只读子 Agent 委派 A/B 小实验：同一调研任务对比「单 Agent」与「可用 delegate」。

用法：
    VIBE_MODEL=deepseek-flash .venv/bin/python3 scripts/delegate_ab.py --runs 1
输出 JSON：每轮 status/steps/tokens/cost/正确性/delegate 调用次数，以及 summary。

实验设计：
- 临时 git 仓库含 3 个文件（README/docs/design.md/notes.txt），各有一个标记；
- 任务要求给出 `RESULT=<项目>|<设计标记>|<运维标记>`；
- single 条件不注入 delegate 环境变量（工具不存在），delegate 条件注入 `VIBE_SUBAGENT_DELEGATE=1`；
- 正确性按 RESULT 行是否同时含 widget/zebra/falcon 判定（大小写不敏感）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

README_TEXT = "# Widget Service\n\nAlpha service for widget management.\n"
DESIGN_TEXT = "# Design\n\nBeta module handles scheduling.\nSystem marker: ZEBRA.\n"
NOTES_TEXT = "# Ops notes\n\nGamma pipeline runs nightly.\nOps marker: FALCON.\n"

PROMPT = (
    "调研当前仓库并回答三件事：\n"
    "1. README 描述的项目用途（一句话）；\n"
    "2. docs/design.md 里的系统标记词；\n"
    "3. notes.txt 里的运维标记词。\n"
    "如果 delegate 工具可用，优先用一次 delegate 完成调研；否则自己读取文件。\n"
    "最后一行必须输出：RESULT=<项目描述>|<设计标记>|<运维标记>"
)


def build_workspace(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "docs").mkdir(exist_ok=True)
    (workspace / "README.md").write_text(README_TEXT, encoding="utf-8")
    (workspace / "docs" / "design.md").write_text(DESIGN_TEXT, encoding="utf-8")
    (workspace / "notes.txt").write_text(NOTES_TEXT, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "ab@example.com"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "AB"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=workspace, check=True)


def check_answer(text: str) -> tuple[bool, str]:
    line = ""
    for candidate in reversed((text or "").splitlines()):
        if candidate.strip().upper().startswith("RESULT="):
            line = candidate.strip()
            break
    value = line.split("=", 1)[1] if "=" in line else ""
    lowered = value.lower()
    ok = "widget" in lowered and "zebra" in lowered and "falcon" in lowered
    return ok, value


async def run_condition(condition: str, config, workspace: Path) -> dict:
    from app.__main__ import build_provider
    from app.runtime.agent import Agent
    from app.safety.policy import ApprovalGate
    from app.session.store import SessionStore
    from app.stdio import register_delegate_tool
    from app.tools.registry import ToolRegistry

    if condition == "delegate":
        os.environ["VIBE_SUBAGENT_DELEGATE"] = "1"
    else:
        os.environ.pop("VIBE_SUBAGENT_DELEGATE", None)
    config.workspace = workspace
    session = SessionStore.new(workspace, config.provider, purpose="test")
    agent = Agent(build_provider(config, False), ToolRegistry(workspace),
                  approval=ApprovalGate(), max_steps=8, max_run_tokens=60_000,
                  max_run_seconds=180)
    calls = {"count": 0}
    if register_delegate_tool(agent, session.root):
        original = agent.tools._tools["delegate"].fn

        async def counting(prompt: str):
            calls["count"] += 1
            return await original(prompt=prompt)

        agent.tools._tools["delegate"].fn = counting
    started = time.monotonic()
    result = await agent.run(PROMPT)
    duration = time.monotonic() - started
    correct, answer = check_answer(result.text or "")
    return {
        "condition": condition,
        "status": result.status,
        "steps": result.steps,
        "tokens": int((result.usage or {}).get("total_tokens") or 0),
        "cost_usd": float(result.cost_usd or 0.0),
        "client_calls": calls["count"],
        "correct": correct,
        "duration_seconds": round(duration, 1),
        "answer": answer[:300],
    }


def summarize(runs: list[dict]) -> dict:
    summary: dict[str, dict] = {}
    for condition in ("single", "delegate"):
        items = [run for run in runs if run["condition"] == condition]
        if not items:
            continue
        summary[condition] = {
            "runs": len(items),
            "ok": sum(1 for item in items if item["status"] == "done"),
            "correct": sum(1 for item in items if item["correct"]),
            "avg_tokens": round(sum(item["tokens"] for item in items) / len(items), 1),
            "avg_steps": round(sum(item["steps"] for item in items) / len(items), 2),
            "avg_duration_seconds": round(sum(item["duration_seconds"] for item in items) / len(items), 1),
            "delegate_calls": sum(item["client_calls"] for item in items),
        }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1, help="每个条件的轮数（默认 1）")
    parser.add_argument("--keep", action="store_true", help="保留临时工作区")
    parser.add_argument("--out", default=None, help="报告路径；默认 .vibe/delegate-ab/<stamp>.json")
    args = parser.parse_args(argv)
    if args.runs < 1 or args.runs > 5:
        print("--runs must be between 1 and 5", file=sys.stderr)
        return 2
    from app.config import Config

    config = Config.from_env()
    if not config.api_key:
        print(json.dumps({"ok": False, "error": "missing API key"}, ensure_ascii=False))
        return 2
    runs = []
    workspaces = []
    try:
        for index in range(args.runs):
            for condition in ("single", "delegate"):
                workspace = Path(tempfile.mkdtemp(prefix=f"delegate-ab-{condition}-"))
                build_workspace(workspace)
                workspaces.append(workspace)
                outcome = asyncio.run(run_condition(condition, config, workspace))
                outcome["run"] = index + 1
                runs.append(outcome)
                print(f"{condition}#{index + 1}: status={outcome['status']} "
                      f"steps={outcome['steps']} tokens={outcome['tokens']} "
                      f"correct={outcome['correct']} delegate_calls={outcome['client_calls']}")
    finally:
        os.environ.pop("VIBE_SUBAGENT_DELEGATE", None)
        if not args.keep:
            for workspace in workspaces:
                shutil.rmtree(workspace, ignore_errors=True)
    summary = summarize(runs)
    payload = {"model": config.model, "prompt": PROMPT, "runs": runs, "summary": summary}
    out_path = Path(args.out) if args.out else (
        ROOT / ".vibe" / "delegate-ab" / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"model": config.model, "summary": summary, "report": str(out_path)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
