#!/usr/bin/env python3
"""引导 W2 停止边界验收：独立工作区 → 打开 Ink → 操作提示 → 自动取证。

用法：
    .venv/bin/python3 scripts/w2w3_run.py model
    .venv/bin/python3 scripts/w2w3_run.py approval
    .venv/bin/python3 scripts/w2w3_run.py command
"""
from __future__ import annotations

import argparse
import os
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
        "prompt": "用至少 5000 字详细解释 ReAct 循环，不要调用任何工具；逐节展开，不要提前收尾。",
        "steps": [
            "把提示词粘贴到 Ink 输入框并回车。",
            "看到第一段文字开始流式输出后立刻（1–2 秒内）按 Ctrl+C，不要等它写完。",
            "确认底部从“正在停止”变为“已取消”，然后按 Ctrl+Q 退出 Ink。",
        ],
        "expect": "取消发生在模型流式输出期间（run_end 早于或打断 llm_end）；没有工具调用，工作区无改动。",
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
    "timeout": {
        "label": "w2-stop-timeout",
        "prompt": "运行 python3 slow_command.py，使用默认 30 秒超时，不要加大 timeout，也不要手动取消；等它超时后报告结果。",
        "steps": [
            "把提示词粘贴到 Ink 输入框并回车。",
            "在 bash 审批面板按 y 允许一次。",
            "不要按 Ctrl+C；等待约 35 秒，直到工具显示超时/进程被回收。",
            "确认结果说明超时而不是正常完成，然后按 Ctrl+Q 退出 Ink。",
        ],
        "expect": "审计允许 bash；command_result 为 timed_out、退出状态来自进程组回收；无 slow_command.py 残留、工作区无改动。",
    },
    "afterwrite": {
        "label": "w2-cancel-after-write",
        "prompt": "修复 calc.py 的 add 并运行测试。",
        "steps": [
            "把提示词粘贴到 Ink 输入框并回车。",
            "允许编辑审批（p 工作区或 y 一次）。",
            "等 bash 审批出现时按 Ctrl+C 取消，不要允许执行测试。",
            "取消后不要退出，再发送：检查当前工作区，报告已发生的改动；不要撤销、重写或提交。",
            "看到它报告 calc.py 已修改后，按 Ctrl+Q 退出 Ink。",
        ],
        "expect": "第一次 run 为 cancelled 但 calc.py 已修改；第二次任务能报告该改动；取消不等于撤销。",
    },
    "w3-interrupt-read": {
        "label": "w3-interrupt-read",
        "workspace": "w3",
        "reset": True,
        "prompt": "先读取 calc.py 和 test_calc.py，解释 add 的问题；读完后先停下，不要修改任何文件。",
        "steps": [
            "把提示词粘贴到 Ink 输入框并回车。",
            "看到 read_file 的返回、但还没给出最终解释时按 Ctrl+C。",
            "Ctrl+Q 退出 Ink。",
        ],
        "expect": "在读取工具附近中断；没有写入；会话已保存，供下一步恢复。",
        "next": "w3-resume",
    },
    "w3-resume": {
        "label": "w3-resume",
        "workspace": "w3",
        "reset": False,
        "continue": True,
        "steps": [
            "打开后先发送：先核对当前状态，说明上次做到哪里、哪些工具结果未知；不要修改文件。",
            "看到核对结果后输入 /resume 继续任务；允许编辑与测试审批，等待验证完成。",
            "确认能看到原目标、未知结果与最终验证后，Ctrl+Q 退出。",
        ],
        "expect": "恢复后能说明原目标、未知结果；/resume 继续完成修复并实际运行测试。",
        "next": "w3-newtask",
    },
    "w3-newtask": {
        "label": "w3-newtask",
        "workspace": "w3",
        "reset": False,
        "continue": False,
        "prompt": "解释 README 的用途，不要修改任何文件。",
        "steps": [
            "新会话发送提示词，等待回答。",
            "确认没有沿用旧目标和旧完成标记，然后 Ctrl+Q 退出。",
        ],
        "expect": "新会话只处理新任务，不自动续做旧任务。",
        "next": "w3-external",
    },
    "w3-external": {
        "label": "w3-external",
        "workspace": "w3-external",
        "reset": True,
        "prompt": "先读取 calc.py，解释 add 的问题，先不要修改。",
        "steps": [
            "把提示词粘贴到 Ink 输入框并回车。",
            "在读取过程中按 Ctrl+C 中断，然后 Ctrl+Q 退出。",
        ],
        "expect": "中断后脚本会模拟外部修改 calc.py，并提示下一步恢复。",
        "external_change": True,
        "next": "w3-external-resume",
    },
    "w3-external-resume": {
        "label": "w3-external-resume",
        "workspace": "w3-external",
        "reset": False,
        "continue": True,
        "steps": [
            "打开后发送：继续上次任务；先检查 calc.py 与上次结果是否有外部变化，再决定是否继续修复。",
            "确认它重新读取/确认了差异后，允许必要审批完成任务。",
            "Ctrl+Q 退出。",
        ],
        "expect": "恢复前文件已被外部改变；Agent 先重新读取或确认差异，不拿过期内容直接写。",
        "next": "w3-normal",
    },
    "w3-compaction": {
        "label": "w3-compaction",
        "workspace": "w3-compaction",
        "reset": True,
        "env": {"VIBE_COMPACT_INTERVAL_TOKENS": "6000", "VIBE_CONTEXT_WINDOW": "24000", "VIBE_KEEP_RECENT_GROUPS": "2"},
        "prompt": "分段读取 big_notes.md 的全部内容并总结其主题与结构；不要修改任何文件。",
        "steps": [
            "把提示词粘贴到 Ink 输入框并回车。",
            "等待它分段读取 big_notes.md 并给出总结（会触发真正的上下文压缩）。",
            "再发送：现在说明这个任务的原目标、关键约束、已完成内容和未完成项；不要修改文件。",
            "看到总结后按 Ctrl+Q 退出。",
        ],
        "expect": "trace 出现实压缩（before>after 或 summary_layers/offloaded 非空）；压缩后仍能说明原目标、约束与未完成项。",
        "big_notes": True,
    },
    "w3-normal": {
        "label": "w3-normal",
        "workspace": "w3-normal",
        "reset": True,
        "prompt": "修复 calc.py 的 add 并运行测试。",
        "steps": [
            "发送提示词；允许编辑与测试审批。",
            "等待验证命令退出 0、任务正常结束，然后 Ctrl+Q 退出。",
        ],
        "expect": "正常完成一次修复，作为第三次真实小任务。",
        "next": "w3-normal-resume",
    },
    "w3-normal-resume": {
        "label": "w3-normal-resume",
        "workspace": "w3-normal",
        "reset": False,
        "continue": True,
        "steps": [
            "打开后发送：总结上次的改动、验证结果与未完成项；不要修改文件。",
            "确认能给出目标、改动、验证结果和下一步，然后 Ctrl+Q 退出。",
        ],
        "expect": "正常退出后恢复，能看到上次目标、改动与验证结果。",
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
    workspace = ROOT / ".vibe" / "w2w3" / ("workspace-" + part.get("workspace", args.part))
    if part.get("reset", True):
        prepare(workspace)
    elif not workspace.is_dir():
        raise SystemExit(f"工作区不存在：{workspace}；请先运行前置引导（例如 w3-interrupt-read）")
    if part.get("big_notes"):
        lines: list[str] = []
        for index in range(1, 1201):
            lines.append(f"## 段落 {index}")
            lines.append("这是用于上下文压缩验收的模拟项目笔记，重复内容用于让上下文增长到需要压缩。")
        (workspace / "big_notes.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"已生成压缩验收素材：{workspace / 'big_notes.md'}（1200 段）")
    print("=" * 64)
    print(f"验收：{args.part}")
    print(f"隔离工作区：{workspace}")
    if part.get("prompt"):
        print(f"\n请发送这段提示词：\n\n    {part['prompt']}\n")
    print("操作步骤：")
    for index, step in enumerate(part["steps"], 1):
        print(f"  {index}. {step}")
    print(f"\n预期：{part['expect']}")
    print("=" * 64)
    if args.prepare_only:
        state = "已准备" if part.get("reset", True) else "使用已有工作区"
        print(f"{state}：{workspace}（--prepare-only，不打开 Ink）")
        return 0
    try:
        input("\n准备好后按 Enter 打开 Ink；完成操作并 Ctrl+Q 退出后会自动取证：")
    except EOFError:
        print("没有交互终端；请手动运行：", file=sys.stderr)
        print(f"  ./run_ink.sh --workspace {workspace}", file=sys.stderr)
        print(f"  .venv/bin/python3 scripts/w2w3_evidence.py --label {part['label']} --workspace {workspace}", file=sys.stderr)
        return 2
    command = ["./run_ink.sh", "--workspace", str(workspace)]
    if part.get("continue"):
        command.append("--continue")
    environment = {**os.environ, **part.get("env", {})}
    result = subprocess.run(command, cwd=ROOT, env=environment)
    if part.get("external_change"):
        calc = workspace / "calc.py"
        if calc.is_file():
            calc.write_text(calc.read_text(encoding="utf-8").rstrip("\n") + "\n# external change\n", encoding="utf-8")
            print(f"已在工作区外修改 {calc}（追加 # external change），用于下一步恢复核对。")
    print(f"\nInk 已退出（exit={result.returncode}），等待 1 秒后自动取证……")
    time.sleep(1)
    subprocess.run([
        sys.executable, str(ROOT / "scripts" / "w2w3_evidence.py"),
        "--label", part["label"], "--workspace", str(workspace),
    ], cwd=ROOT)
    print("\n请把上面的证据 JSON 路径发回；如果结果不符合预期，把界面最后几行也一并贴出来。")
    if part.get("next"):
        print(f"下一步：.venv/bin/python3 scripts/w2w3_run.py {part['next']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
