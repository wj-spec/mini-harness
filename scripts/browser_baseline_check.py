#!/usr/bin/env python3
"""B2 浏览器基线自检：版本固定、依赖与验收入口；不联网、不调用模型。"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = {
    "playwright_mcp": "@playwright/mcp@0.0.83",
    "chromium_dir": "chromium-1247",
    "fastmcp": "fastmcp==4.1.1",
    "min_node_major": 22,
}
CONFIGS = ("examples/browser-mcp.json", "examples/browser-actions-mcp.json")
ENTRYPOINTS = ("scripts/browser_acceptance.py", "scripts/browser_actions_smoke.py",
               "scripts/browser_smoke.py", "app/browser_server.py")


def check(root: Path = ROOT) -> dict:
    problems: list[str] = []
    details: dict = {}
    for relative in CONFIGS:
        path = root / relative
        if not path.is_file():
            problems.append(f"缺少配置 {relative}")
            continue
        text = path.read_text(encoding="utf-8")
        if BASELINE["playwright_mcp"] not in text:
            problems.append(f"{relative} 未固定 {BASELINE['playwright_mcp']}")
        if "@latest" in text:
            problems.append(f"{relative} 使用了 @latest")
        details[relative] = "ok"
    node = shutil.which("node")
    if node is None:
        problems.append("缺少 node")
    else:
        try:
            version = subprocess.run(
                [node, "--version"], capture_output=True, text=True, timeout=10,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError) as exc:
            problems.append(f"node 版本读取失败：{type(exc).__name__}")
            version = "unknown"
        details["node"] = version
        try:
            major = int(version.lstrip("v").split(".")[0])
        except ValueError:
            major = 0
        if version != "unknown" and major < BASELINE["min_node_major"]:
            problems.append(f"node {version} 低于 {BASELINE['min_node_major']}")
    chromium_root = root / ".vibe" / "ms-playwright"
    details["chromium"] = (
        sorted(path.name for path in chromium_root.glob("chromium-*"))
        if chromium_root.is_dir() else []
    )
    if BASELINE["chromium_dir"] not in details["chromium"]:
        problems.append(f"缺少 {BASELINE['chromium_dir']}（用 Playwright MCP 自带安装器安装）")
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        pinned = BASELINE["fastmcp"] in pyproject.read_text(encoding="utf-8")
        details["fastmcp_pinned"] = pinned
        if not pinned:
            problems.append(f"pyproject 未固定 {BASELINE['fastmcp']}")
    else:
        problems.append("缺少 pyproject.toml")
    for relative in ENTRYPOINTS:
        if not (root / relative).is_file():
            problems.append(f"缺少 {relative}")
    return {"passed": not problems, "problems": problems, "details": details,
            "baseline": dict(BASELINE)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--allow-missing-chromium", action="store_true",
                        help="仅检查版本固定与入口，不要求本机已安装 Chromium")
    args = parser.parse_args(argv)
    report = check()
    if args.allow_missing_chromium:
        report["problems"] = [item for item in report["problems"] if "chromium" not in item.lower()]
        report["passed"] = not report["problems"]
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("B2 浏览器基线：" + ("PASS" if report["passed"] else "FAIL"))
        for key, value in report["details"].items():
            print(f"  {key}: {value}")
        for problem in report["problems"]:
            print(f"  ! {problem}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
