#!/usr/bin/env python3
"""B2 手工验收：本地 fixture → Ink 逐次审批 → 退出后收证。"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import Config
from scripts.browser_actions_smoke import fixture_server

ROOT = Path(__file__).resolve().parents[1]
LIMITS = {"VIBE_MAX_RUN_TOKENS": "100000", "VIBE_MAX_RUN_SECONDS": "180"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve-only", action="store_true", help="仅运行 fixture，在另一个终端启动 Ink")
    parser.add_argument("--no-sandbox", action="store_true", help="仅当本机无法初始化 Chrome sandbox 时显式关闭")
    args = parser.parse_args(argv)
    config = Config.from_env()
    if not args.serve_only and config.provider != "fake" and not config.api_key:
        print("缺少 API key：请在已配置 VIBE_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY 的终端运行；"
              "不要将 key 发到聊天。也可用 --serve-only 仅启动本地 fixture。", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / ".vibe/browser/acceptance" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True)
    sessions = workspace / ".vibe/sessions"
    before_ids = {path.name for path in sessions.iterdir() if path.is_dir()} if sessions.is_dir() else set()
    template = json.loads((ROOT / "examples/browser-actions-mcp.json").read_text(encoding="utf-8"))
    browser = template["mcpServers"]["browser"]
    mcp = {"mcpServers": {"browser": browser}}
    browser["cwd"] = str(ROOT)
    if args.no_sandbox:
        browser.setdefault("env", {})["VIBE_BROWSER_NO_SANDBOX"] = "1"
    config_path = run_dir / "mcp.json"
    command = [str(ROOT / "run_ink.sh"), "--workspace", str(workspace), "--test-session", "--max-steps", "12"]
    exit_code = None
    with fixture_server() as (origin, state):
        browser["allowed_origins"] = [origin]
        config_path.write_text(json.dumps(mcp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        prompts = (
            f"任务一：打开 {origin}/，点击 Increment 一次，在 Note 中填入“B2 验收草稿”，"
            "Shade 选择 Green。不要提交任何表单。最后读取最新 snapshot，报告实际观察到的值。\n\n"
            "任务二（任务一完成后，在同一个 Ink 窗口单独发送）：把 Note 改为“拒绝写入”。我会在这次输入审批时拒绝；不要重试被拒动作。"
            "随后读取最新 snapshot，确认 Note 仍为“B2 验收草稿”，不要提交。\n"
        )
        (run_dir / "prompts.txt").write_text(prompts, encoding="utf-8")
        print(f"B2 本地验收：{origin}/\n独立工作区：{workspace}\n提示词：{run_dir / 'prompts.txt'}\n")
        print(prompts)
        print("两段任务要在同一个 Ink 窗口分两次发送，期间保持窗口和浏览器运行。"
              "启动服务及任务一的动作逐次按 y；任务二的 Note 输入审批按 n。"
              "任务二快照核对完成后再 Ctrl+Q 退出，自动生成 report.json。退出会清空临时浏览器页面。")
        try:
            if args.serve_only:
                shell_command = shlex.join(["env", f"VIBE_MCP_CONFIG={config_path}",
                                            *(f"{key}={value}" for key, value in LIMITS.items()), *command])
                print(f"\n在已配置模型的另一个终端运行：\n{shell_command}\n", flush=True)
                input("服务运行中；完成 Ink 操作后按 Enter 或 Ctrl+C 结束并收证：")
            else:
                input("保存上面两段提示词后，按 Enter 打开 Ink（先发任务一，完成后在同窗口发任务二）：")
                result = subprocess.run(command, cwd=ROOT, env={
                    **os.environ, **LIMITS, "VIBE_MCP_CONFIG": str(config_path),
                    "VIBE_MCP_SERVERS": "",
                })
                exit_code = result.returncode
        except (KeyboardInterrupt, EOFError):
            if not args.serve_only:
                exit_code = 130
            print("\n验收入口结束，收集已有证据。")
        except OSError as exc:
            exit_code = 1
            print(f"Ink 启动失败：{type(exc).__name__}", file=sys.stderr)

        from scripts.browser_acceptance_evidence import collect_report

        report = collect_report(workspace, before_ids, origin=origin,
                                submissions=state["submissions"], exit_code=exit_code)
        report["launcher"] = {
            "provider": None if args.serve_only else config.provider,
            "model": None if args.serve_only else config.model,
            "mcp_config": str(config_path), "workspace": str(workspace),
            "max_steps": 12, "max_run_tokens": int(LIMITS["VIBE_MAX_RUN_TOKENS"]), "max_run_seconds": 180,
            "chrome_sandbox": not args.no_sandbox, "serve_only": args.serve_only,
        }
        report_path = run_dir / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n自动证据：{report.get('status', 'unknown')}\n报告：{report_path}")
        print("真实模型表现及审批可读性仍需人工确认，自动证据不代替使用体验验收。")
        return 0 if report.get("passed") and exit_code in (None, 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
