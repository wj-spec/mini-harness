#!/usr/bin/env python3
"""W6 收益对照：三组真实任务 × 启用/停用个性化，逐个引导并自动取证。

用法：
    .venv/bin/python3 scripts/w6_run.py task-a-on
    .venv/bin/python3 scripts/w6_run.py task-a-off
    …（task-b-on/off、task-c-on/off）
    .venv/bin/python3 scripts/w6_report.py          # 全部跑完后汇总
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

W6_TEST_PREFERENCES = {
    "version": 2,
    "enabled": True,
    "entries": [
        {
            "id": "w6-explain", "kind": "preference", "scope": "workflow",
            "text": "解释代码时：先给结论，再给细节；用中文，注释简洁。",
            "triggers": ["解释", "说明", "怎么", "如何", "why", "how"],
            "source": "user", "status": "confirmed", "confidence": 1.0,
            "created_at": "2026-10-26T00:00:00Z", "updated_at": "2026-10-26T00:00:00Z",
            "last_confirmed_at": "2026-10-26T00:00:00Z",
        },
        {
            "id": "w6-change", "kind": "preference", "scope": "workflow",
            "text": "修改代码前：先说明方案、影响文件和验证方式。",
            "triggers": ["修改", "增加", "新增", "重构", "改造", "edit", "implement"],
            "source": "user", "status": "confirmed", "confidence": 1.0,
            "created_at": "2026-10-26T00:00:00Z", "updated_at": "2026-10-26T00:00:00Z",
            "last_confirmed_at": "2026-10-26T00:00:00Z",
        },
        {
            "id": "w6-test", "kind": "preference", "scope": "workflow",
            "text": "测试/诊断时：先复现失败，再改代码，最后重新运行验证。",
            "triggers": ["测试", "诊断", "验证", "失败", "复现", "bug", "修复"],
            "source": "user", "status": "confirmed", "confidence": 1.0,
            "created_at": "2026-10-26T00:00:00Z", "updated_at": "2026-10-26T00:00:00Z",
            "last_confirmed_at": "2026-10-26T00:00:00Z",
        },
    ],
}
EMPTY_PROFILE = {"version": 2, "enabled": False, "entries": []}

TASKS = {
    "task-a": {
        "prompt": "解释 Ink 的 /about 命令是怎么实现的：数据文件格式、状态过滤、以及注入到模型上下文的时机。不要改文件。",
        "skills": "结论先行 / 中文简洁",
    },
    "task-b": {
        "prompt": "给 /about 增加 /about edit <序号> <新文本> 命令，用于修改某条偏好的文本；补一个测试。只改必要文件。",
        "skills": "改动前先给方案 / 注释简洁",
    },
    "task-c": {
        "prompt": "检查 app/user_profile.py 的 load_user_profile：如果 entries 里有重复 text 或空白 text 条目会怎样？有问题就修复并补测试。",
        "skills": "根因定位 / 验证",
    },
}


def build_workspace(destination: Path, personalization: str) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    archive = subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(destination)  # 信任本仓库 git archive
    for relative in ("benchmarks", "scripts/bench_local.py", "docs/specs/local-coding-benchmark.md",
                     "docs/MILESTONES.md", "tests/test_bench_local.py"):
        target = destination / relative
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()
    for link, target in ((destination / ".venv", ROOT / ".venv"), (destination / "tui" / "node_modules", ROOT / "tui" / "node_modules")):
        if target.exists() and not link.exists():
            link.symlink_to(target)
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True)
    subprocess.run(["git", "add", "-A"], cwd=destination, check=True)
    subprocess.run(
        ["git", "-c", "user.email=w6@example.com", "-c", "user.name=w6", "commit", "-qm", "w6 seed"],
        cwd=destination, check=True,
    )
    w6_dir = ROOT / ".vibe" / "w6"
    w6_dir.mkdir(parents=True, exist_ok=True)
    if personalization == "on":
        source = ROOT / ".vibe" / "project-profile.json"
        if source.is_file():
            (destination / ".vibe").mkdir(exist_ok=True)
            shutil.copy(source, destination / ".vibe" / "project-profile.json")
        user_profile = w6_dir / "user-profile-on.json"
        user_profile.write_text(json.dumps(W6_TEST_PREFERENCES, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        user_profile = w6_dir / "user-profile-off.json"
        user_profile.write_text(json.dumps(EMPTY_PROFILE, ensure_ascii=False, indent=2), encoding="utf-8")
    return user_profile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("part", help="task-a-on / task-a-off / task-b-on / …")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    task_name, _, mode = args.part.rpartition("-")
    if task_name not in TASKS or mode not in ("on", "off"):
        raise SystemExit(f"未知部分：{args.part}；用法：task-a-on、task-a-off、task-b-on …")
    task = TASKS[task_name]
    workspace = ROOT / ".vibe" / "w6" / f"workspace-{args.part}"
    user_profile = build_workspace(workspace, mode)
    personalization_text = "启用（项目背景 + 3 条带触发词的偏好）" if mode == "on" else "停用（无背景、无偏好）"
    print("=" * 64)
    print(f"W6 对照：{args.part}")
    print(f"个性化：{personalization_text}")
    print(f"隔离工作区：{workspace}")
    print(f"用户偏好文件：{user_profile}")
    print(f"\n请发送这段提示词：\n\n    {task['prompt']}\n")
    print(f"观察点：{task['skills']}")
    print("操作步骤：")
    print("  1. 把提示词粘贴到 Ink 输入框并回车。")
    print("  2. 按需审批、补充说明或纠正；把每次补充/纠正记下来。")
    print("  3. 任务完成后 Ctrl+Q 退出 Ink。")
    print("=" * 64)
    if args.prepare_only:
        print(f"工作区已准备：{workspace}（--prepare-only）")
        return 0
    try:
        input("\n准备好后按 Enter 打开 Ink；完成操作并 Ctrl+Q 退出后会自动取证：")
    except EOFError:
        print(f"手动运行：VIBE_USER_PROFILE={user_profile} ./run_ink.sh --workspace {workspace}", file=sys.stderr)
        return 2
    env = {**os.environ, "VIBE_USER_PROFILE": str(user_profile)}
    result = subprocess.run(["./run_ink.sh", "--workspace", str(workspace)], cwd=ROOT, env=env)
    print(f"\nInk 已退出（exit={result.returncode}），自动取证……")
    subprocess.run([
        sys.executable, str(ROOT / "scripts" / "w6_evidence.py"),
        "--label", args.part, "--workspace", str(workspace), "--user-profile", str(user_profile),
    ], cwd=ROOT)
    print("\n请把你在这个会话里补充/纠正的次数和内容记下来；全部跑完后运行 scripts/w6_report.py 汇总。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
