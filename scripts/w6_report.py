#!/usr/bin/env python3
"""W6 收益对照汇总：把 .vibe/w6/evidence/ 下的证据合成一张启用/停用对照表。

用法：
    python3 scripts/w6_report.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".vibe" / "w6" / "evidence"
ORDER = ["task-a", "task-b", "task-c"]


def load_latest(label_prefix: str) -> dict | None:
    files = sorted(EVIDENCE.glob(f"{label_prefix}-*.json")) if EVIDENCE.is_dir() else []
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def summarize(evidence: dict | None) -> dict:
    if not evidence:
        return {}
    run_ends = evidence.get("run_ends", [])
    totals = {
        "runs": len(run_ends),
        "steps": sum(event.get("steps") or 0 for event in run_ends),
        "tokens": sum(event.get("total_tokens") or 0 for event in run_ends),
        "tool_calls": sum(event.get("tool_calls") or 0 for event in run_ends),
    }
    statuses = [event.get("status") for event in run_ends]
    commands = evidence.get("commands", [])
    return {
        **totals,
        "statuses": statuses,
        "tests": [c for c in commands if "test" in (c.get("command") or "") and c.get("exit_code") == 0],
        "user_messages": evidence.get("user_message_count", 0),
        "git_status": evidence.get("git", {}).get("status", ""),
        "project_background": bool(evidence.get("project_background")),
        "user_profile": evidence.get("user_profile"),
        "final_tail": evidence.get("final_answer_tail", ""),
    }


def main() -> None:
    rows = []
    for task in ORDER:
        on = summarize(load_latest(f"{task}-on"))
        off = summarize(load_latest(f"{task}-off"))
        rows.append((task, on, off))
    print("# W6 收益对照\n")
    for task, on, off in rows:
        print(f"## {task}\n")
        if not on or not off:
            print(f"证据不全：on={'有' if on else '缺'} off={'有' if off else '缺'}；先运行 `w6_run.py {task}-on` 和 `{task}-off`。\n")
            continue
        print("| 指标 | 启用个性化 | 停用个性化 |")
        print("|---|---|---|")
        print(f"| 用户消息数（补充/纠正） | {on['user_messages']} | {off['user_messages']} |")
        print(f"| 步数 | {on['steps']} | {off['steps']} |")
        print(f"| tokens | {on['tokens']} | {off['tokens']} |")
        print(f"| 工具调用 | {on['tool_calls']} | {off['tool_calls']} |")
        print(f"| 测试通过命令数 | {len(on['tests'])} | {len(off['tests'])} |")
        print(f"| git status | `{on['git_status'] or '无'}` | `{off['git_status'] or '无'}` |")
        print(f"| 注入背景/偏好 | 项目背景={on['project_background']}，偏好={bool((on.get('user_profile') or {}).get('entries'))} | 项目背景={off['project_background']}，偏好={bool((off.get('user_profile') or {}).get('entries'))} |")
        print(f"| run 状态 | {on['statuses']} | {off['statuses']} |")
        print(f"\n启用时结尾：{on['final_tail'][:300]}\n")
        print(f"停用时结尾：{off['final_tail'][:300]}\n")
        print("待你补充：实际少重复了哪些说明？纠正次数差异？评价。\n")
    print("结论：只有当重复交代/纠正减少且正确性、审批边界未回退，才认定有收益。小样本结论不外推。")


if __name__ == "__main__":
    main()
