#!/usr/bin/env python3
"""W6 v2 汇总：按 task/mode 聚合多次运行，输出分布而不是单次结论。

用法：
    python3 scripts/w6_report_v2.py
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".vibe" / "w6" / "evidence"
TASKS = ("task-a", "task-b", "task-c")


def summary(runs: list[dict]) -> dict:
    user_messages = [int(run.get("user_message_count") or 0) for run in runs]
    steps = [sum(event.get("steps") or 0 for event in run.get("run_ends", [])) for run in runs]
    tokens = [sum(event.get("total_tokens") or 0 for event in run.get("run_ends", [])) for run in runs]
    tools = [sum(event.get("tool_calls") or 0 for event in run.get("run_ends", [])) for run in runs]
    tests = [len([c for c in run.get("commands", []) if "test" in (c.get("command") or "") and c.get("exit_code") == 0]) for run in runs]
    selected = []
    selected_runs = 0
    for run in runs:
        for event in run.get("profile_selections", []):
            entries = event.get("selected") or []
            if entries:
                selected_runs += 1
                selected.extend(entries)
    return {
        "runs": len(runs),
        "user_messages": user_messages,
        "steps": steps,
        "tokens": tokens,
        "tools": tools,
        "tests": tests,
        "selected": selected,
        "selected_runs": selected_runs,
    }


def group_runs(evidence_dir: Path = EVIDENCE) -> tuple[dict[tuple[str, str], list[dict]], int]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    ignored_v1 = 0
    if not evidence_dir.is_dir():
        return groups, ignored_v1
    for path in sorted(evidence_dir.glob("*.json")):
        try:
            evidence = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        profile = evidence.get("user_profile") or {}
        # 旧 v1 证据缺少 v2 selection schema，不能与 v2 混在一起算 n。
        if profile.get("version") != 2 or "profile_selections" not in evidence:
            ignored_v1 += 1
            continue
        label = str(evidence.get("label") or "")
        for task in TASKS:
            for mode in ("on", "off"):
                prefix = f"{task}-{mode}"
                if label == prefix or label.startswith(prefix + "-") or label.startswith(prefix + "_"):
                    groups[(task, mode)].append(evidence)
    return groups, ignored_v1


def fmt(values: list[int]) -> str:
    if not values:
        return "—"
    if len(values) == 1:
        return str(values[0])
    return f"{min(values)} / {int(statistics.median(values))} / {max(values)}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-v1", action="store_true", help="同时纳入旧 v1 证据（仅用于排查，不建议做 v2 结论）")
    args = parser.parse_args()
    groups, ignored_v1 = group_runs()
    print("# W6 v2 收益对照（按运行分布）\n")
    print("格式：min / median / max；单次运行不足以比较成本。\n")
    print(f"仅统计 v2 证据；已忽略 {ignored_v1} 份旧 v1/无 selection 证据（旧 v1 汇总用 w6_report.py）。\n")
    for task in TASKS:
        print(f"## {task}\n")
        rows = []
        for mode in ("on", "off"):
            runs = groups.get((task, mode), [])
            if not runs:
                print(f"- {mode}: 证据缺失；运行 `w6_run.py {task}-{mode}`（建议每侧 ≥3 次）")
                continue
            rows.append((mode, summary(runs)))
        if len(rows) < 2:
            print()
            continue
        print("| 指标 | 启用（n） | 停用（n） |")
        print("|---|---|---|")
        on = rows[0][1]
        off = rows[1][1]
        print(f"| 运行数 | {on['runs']} | {off['runs']} |")
        print(f"| 用户消息（补充/纠正） | {fmt(on['user_messages'])} | {fmt(off['user_messages'])} |")
        print(f"| 步数 | {fmt(on['steps'])} | {fmt(off['steps'])} |")
        print(f"| tokens | {fmt(on['tokens'])} | {fmt(off['tokens'])} |")
        print(f"| 工具调用 | {fmt(on['tools'])} | {fmt(off['tools'])} |")
        print(f"| 测试通过命令数 | {fmt(on['tests'])} | {fmt(off['tests'])} |")
        selected_texts = [item.get("text", "") for item in on["selected"]]
        off = rows[1][1]
        off_selected = [item.get("text", "") for item in off["selected"]]
        print(f"| 实际选中偏好 | {on['selected_runs']} 次选择 / {len(set(selected_texts))} 条 | {off['selected_runs']} 次选择 / {len(set(off_selected))} 条 |")
        for text in sorted(set(selected_texts)):
            print(f"|   · {text} | | |")
        print()
    print("判定：多数任务重复说明/纠正下降且正确性未回退才认定收益；单次 token 差异不作结论。\n")


if __name__ == "__main__":
    main()
