#!/usr/bin/env python3
"""引导 W2 停止边界验收：独立工作区 → 打开 Ink → 操作提示 → 自动取证。

用法：
    .venv/bin/python3 scripts/w2w3_run.py model
    .venv/bin/python3 scripts/w2w3_run.py approval
    .venv/bin/python3 scripts/w2w3_run.py command
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w2w3_setup import prepare  # noqa: E402

PARTS = {
    "model": {
        "label": "w2-stop-model",
        "prompt": "用至少 1500 字详细解释 ReAct 循环，不要调用任何工具。",
        "steps": [
            "把提示词粘贴到 Ink 输入框并回车。",
            "等待 1–2 秒，看到文字开始流式输出后立刻按 Ctrl+C。",
            "确认底部从“正在停止”变为“已取消”，然后按 Ctrl+Q 退出 Ink。",
        ],
        "expect": "取消发生在模型流式输出期间；run 没有工具调用，工作区无改动。",
    },
    "approval": {
        "label": "w2-stop-approval",
        "prompt": "修复 calc.py 的 add 并运行测试。",
        "steps": [
            "把提示词粘贴到 Ink 输入框并回车。",
            "等待编辑审批面板出现（显示 calc.py 的 diff）。",
            "直接按 Ctrl+C，不要按 y / s / p。",
            "确认任务已取消，然后按 Ctrl+Q 退出 Ink。",
        ],
        "expect": "审批审计出现 edit_file deny；calc.py 哈希与初始一致。",
    },
    "command": {
        "label": "w2-stop-command",
        "prompt": "运行 python3 slow_command.py 并报告结果。",
        "steps": [
            "把提示词粘贴到 Ink 输入框并回车。",
            "在 bash 审批面板按 y 允许一次。",
            "等 3–5 秒，确认命令正在运行，然后按 Ctrl+C。",
            "确认任务已取消，然后按 Ctrl+Q 退出 Ink。",
        ],
        "expect": "审计允许 bash；run_end 为 cancelled；无 slow_command.py 残留进程；工作区无改动。",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("part", choices=sorted(PARTS))
    parser.add_argument("--prepare-only", action="store_true", help="只准备隔离工作区，不打开 Ink（本地自检用）")
    args = parser.parse_args()
    part = PARTS[args.part]
    workspace = ROOT / ".vibe" / "w2w3" / f"workspace-{args.part}"
    prepare(workspace)
    print("=" * 64)
    print(f"W2 验收：{args.part}")
    print(f"隔离工作区：{workspace}")
    print(f"\n请发送这段提示词：\n\n    {part['prompt']}\n")
    print("操作步骤：")
    for index, step in enumerate(part["steps"], 1):
        print(f"  {index}. {step}")
    print(f"\n预期：{part['expect']}")
    print("=" * 64)
    if args.prepare_only:
        print(f"工作区已准备：{workspace}（--prepare-only，不打开 Ink）")
        return 0
    try:
        input("\n准备好后按 Enter 打开 Ink；完成操作并 Ctrl+Q 退出后会自动取证：")
    except EOFError:
        print("没有交互终端；请手动运行：", file=sys.stderr)
        print(f"  ./run_ink.sh --workspace {workspace}", file=sys.stderr)
        print(f"  .venv/bin/python3 scripts/w2w3_evidence.py --label {part['label']} --workspace {workspace}", file=sys.stderr)
        return 2
    result = subprocess.run(["./run_ink.sh", "--workspace", str(workspace)], cwd=ROOT)
    print(f"\nInk 已退出（exit={result.returncode}），等待 1 秒后自动取证……")
    time.sleep(1)
    subprocess.run([
        sys.executable, str(ROOT / "scripts" / "w2w3_evidence.py"),
        "--label", part["label"], "--workspace", str(workspace),
    ], cwd=ROOT)
    print("\n请把上面的证据 JSON 路径发回；如果结果不符合预期，把界面最后几行也一并贴出来。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
