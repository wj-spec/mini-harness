#!/usr/bin/env python3
"""准备 W2/W3 真实验收工作区：demo-repo 副本 + 长测试 + 独立 git 仓库。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / ".vibe" / "w2w3" / "workspace"

SLOW_TEST = '''"""演示用的慢测试：先等 20 秒（默认 30 秒工具超时内），再检查 calc.add。"""
import time

import calc

print("slow integration test starting", flush=True)
for _ in range(20):
    time.sleep(1)
print("slow integration test checking", flush=True)
assert calc.add(2, 3) == 5, "add is broken"
print("slow integration test ok", flush=True)
'''


def main() -> None:
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    shutil.copytree(ROOT / "examples" / "demo-repo", WORKSPACE, ignore=shutil.ignore_patterns(".vibe"))
    (WORKSPACE / "slow_test.py").write_text(SLOW_TEST, encoding="utf-8")
    (WORKSPACE / "slow_command.py").write_text(
        '''"""演示用的长命令：用于验证命令运行中的停止边界。"""
import time

print("slow command running", flush=True)
for _ in range(120):
    time.sleep(1)
print("slow command done", flush=True)
''',
        encoding="utf-8",
    )
    (WORKSPACE / ".gitignore").write_text(".vibe/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=WORKSPACE, check=True)
    subprocess.run(["git", "add", "-A"], cwd=WORKSPACE, check=True)
    subprocess.run(
        ["git", "-c", "user.email=w2w3@example.com", "-c", "user.name=rehearsal",
         "commit", "-qm", "W2/W3 rehearsal seed"],
        cwd=WORKSPACE, check=True,
    )
    print(f"工作区已准备：{WORKSPACE}")
    print("初始失败可用：cd <workspace> && python3 test_calc.py   # add(2,3) != 5")


if __name__ == "__main__":
    main()
