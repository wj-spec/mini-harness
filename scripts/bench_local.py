#!/usr/bin/env python3
"""本地编码基准：在仓库副本里注入已知 bug，让 Agent 修复，再用隐藏测试评分。

用法：
    python3 scripts/bench_local.py --prepare          # 只校验任务夹具（不需要 API key）
    python3 scripts/bench_local.py                    # 跑全部任务（需要已配置的模型环境）
    python3 scripts/bench_local.py --tasks recall-guard --timeout 600

运行时产物写到 .vibe/bench-local/<时间戳>/<任务>/：workspace、report.json、agent.log。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = ROOT / "benchmarks" / "local"
RUN_ROOT = ROOT / ".vibe" / "bench-local"
IGNORED_DIRS = {
    ".git", ".venv", ".pylibs", "node_modules", "dist", ".vibe", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
}


def task_ids() -> list[str]:
    return sorted(path.name for path in TASKS_DIR.iterdir() if (path / "task.md").is_file())


def build_workspace(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", "HEAD"], cwd=ROOT, check=True, capture_output=True, timeout=120,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(destination)  # 信任本仓库自己的 git archive；兼容 Python < 3.12
    # 基准自身不能出现在工作区，否则 Agent 可以直接读隐藏测试或评分逻辑。
    for relative in ("benchmarks", "scripts/bench_local.py", "docs/specs/local-coding-benchmark.md",
                     "docs/MILESTONES.md", "tests/test_bench_local.py"):
        target = destination / relative
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    for link, target in (
        (destination / ".venv", ROOT / ".venv"),
        (destination / "tui" / "node_modules", ROOT / "tui" / "node_modules"),
    ):
        if target.exists() and not link.exists():
            link.symlink_to(target)
    # 独立 git 仓库：Agent 的 git status/diff 指向副本本身，不会跑到外层仓库。
    subprocess.run(["git", "init", "-q"], cwd=destination, check=True, timeout=60)
    subprocess.run(["git", "add", "-A"], cwd=destination, check=True, timeout=120)
    subprocess.run(
        ["git", "-c", "user.email=bench@example.com", "-c", "user.name=benchmark",
         "commit", "-qm", "benchmark seed"],
        cwd=destination, check=True, timeout=120,
    )


def file_hashes(workspace: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        if any(part in IGNORED_DIRS for part in relative.parts):
            continue
        try:
            hashes[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return hashes


def changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(
        path for path in set(before) | set(after)
        if before.get(path) != after.get(path)
    )


def run_script(script: Path, workspace: Path, task_dir: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update({
        "BENCH_TASK_DIR": str(task_dir),
        "BENCH_REPO_ROOT": str(ROOT),
        "VIBE_MCP_CONFIG": "",
        "VIBE_MCP_SERVERS": "",
    })
    return subprocess.run(
        ["bash", str(script)], cwd=workspace, env=env, capture_output=True, text=True, timeout=600,
    )


def approval_decision(event: dict) -> str:
    decisions = event.get("decisions") or []
    for candidate in ("workspace", "session", "once"):
        if candidate in decisions:
            return candidate
    return "deny"


def run_agent(workspace: Path, prompt: str, timeout: float, log_path: Path, fake: bool = False) -> dict:
    command = [str(ROOT / "run_vibe.sh"), "--stdio", "--workspace", str(workspace), "--test-session"]
    if fake:
        command.append("--fake")
    process = subprocess.Popen(
        command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    assert process.stdin is not None and process.stdout is not None
    started = time.monotonic()
    deadline = started + timeout
    result: dict | None = None
    error: str | None = None
    approvals: list[dict] = []
    events = 0
    log_lines: list[str] = []

    def send(frame: dict) -> None:
        process.stdin.write(json.dumps(frame, ensure_ascii=False) + "\n")
        process.stdin.flush()

    send({"v": 1, "type": "run", "id": "bench-run", "text": prompt})
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([process.stdout], [], [], min(1.0, max(0.0, deadline - time.monotonic())))
            if not ready:
                continue
            line = process.stdout.readline()
            if not line:
                break
            log_lines.append(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            events += 1
            if event.get("type") == "approval_request":
                decision = approval_decision(event)
                approvals.append({"tool": event.get("call", {}).get("name"), "decision": decision})
                send({"v": 1, "type": "approval", "id": f"ap-{len(approvals)}",
                      "approval_id": event["approval_id"], "decision": decision})
            elif event.get("type") == "done" and event.get("id") == "bench-run":
                result = event.get("result")
                break
            elif event.get("type") == "error":
                error = f"{event.get('code')}: {event.get('message')}"
                if event.get("id") == "bench-run":
                    break
            elif event.get("type") == "closed":
                break
    finally:
        try:
            send({"v": 1, "type": "shutdown", "id": "bench-shutdown"})
            process.stdin.close()
        except (BrokenPipeError, ValueError):
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    stderr = process.stderr.read() if process.stderr is not None else ""
    log_path.write_text("".join(log_lines) + "\n--- stderr ---\n" + stderr, encoding="utf-8")
    return {
        "result": result,
        "error": error,
        "approvals": approvals,
        "events": events,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "stderr": stderr[-4000:],
    }


def failure_class(report: dict) -> str | None:
    if report.get("passed"):
        return None
    if report.get("agent_error"):
        return "agent_error"
    if not report.get("changed_files"):
        return "no_change"
    if report.get("test_edits"):
        return "edited_tests"
    if report.get("outside_workspace_commands") and not report.get("changed_files"):
        return "wrong_directory"
    if report.get("agent_status") != "done":
        return f"run_{report.get('agent_status')}"
    return "incomplete_fix"


def trace_commands(workspace: Path, run_id: str | None) -> list[str]:
    sessions = workspace / ".vibe" / "sessions"
    if not sessions.is_dir():
        return []
    commands: list[str] = []
    for path in sorted(sessions.glob("*/trace.jsonl"), key=lambda item: item.stat().st_mtime):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "command_result" and (run_id is None or event.get("run_id") == run_id):
                commands.append(str(event.get("command", "")))
    return commands


def outside_workspace_commands(workspace: Path, commands: list[str]) -> list[str]:
    prefix = str(workspace.resolve())
    suspicious: list[str] = []
    for command in commands:
        for match in re.findall(r"/[^\s\"'`;|&()]+", command):
            if match.startswith(("/Users/", "/home/")) and not match.startswith(prefix):
                suspicious.append(command[:200])
                break
    return suspicious


def latest_run_end(workspace: Path, run_id: str | None) -> dict | None:
    sessions = workspace / ".vibe" / "sessions"
    if not sessions.is_dir():
        return None
    candidates = sorted(sessions.glob("*/trace.jsonl"), key=lambda path: path.stat().st_mtime)
    for path in reversed(candidates):
        try:
            for line in reversed(path.read_text(encoding="utf-8").splitlines()):
                event = json.loads(line)
                if event.get("type") == "run_end" and (run_id is None or event.get("run_id") == run_id):
                    return event
        except (OSError, json.JSONDecodeError):
            continue
    return None


def run_task(task: str, prepare: bool, timeout: float, stamp: str, fake: bool = False, clean: bool = False) -> dict:
    task_dir = TASKS_DIR / task
    workdir = RUN_ROOT / stamp / task
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    # 工作区放在仓库外，避免父级 .git 让 Agent 的 git 命令看到外层仓库。
    workspace = Path(tempfile.mkdtemp(prefix=f"bench-{stamp}-{task}-"))
    build_workspace(workspace)
    prompt = (task_dir / "task.md").read_text(encoding="utf-8")
    prompt += (
        "\n\n## 工作区\n"
        f"- 唯一工作目录（仓库副本）：`{workspace}`\n"
        "- 所有读写与命令都在该目录内执行；不要访问或修改该目录之外的其它仓库或文件。\n"
        "- 完成后报告实际改动文件、运行过的命令与退出码。\n"
    )
    leaked = [path for path in ("benchmarks", "scripts/bench_local.py") if (workspace / path).exists()]
    report: dict = {"task": task, "workspace": str(workspace), "prompt": prompt,
                    "workspace_clean": not leaked, "leaked_paths": leaked}

    setup = subprocess.run(
        [sys.executable, str(task_dir / "setup.py")], cwd=workspace,
        capture_output=True, text=True, timeout=120,
    )
    report["setup"] = {"exit_code": setup.returncode, "output": (setup.stdout + setup.stderr)[-2000:]}
    if setup.returncode != 0:
        report.update(passed=False, reason="setup failed")
        (workdir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    red = run_script(task_dir / "red.sh", workspace, task_dir)
    report["red"] = {"exit_code": red.returncode, "output": (red.stdout + red.stderr)[-3000:]}
    if prepare:
        report["prepared"] = True
        report["red_expected"] = red.returncode != 0
        (workdir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    before = file_hashes(workspace)
    run = run_agent(workspace, prompt, timeout, workdir / "agent.log", fake)
    after = file_hashes(workspace)
    changed = changed_files(before, after)
    result = run["result"] or {}
    run_id = result.get("run_id")
    trace = latest_run_end(workspace, run_id) or {}
    commands = trace_commands(workspace, run_id)
    outside = outside_workspace_commands(workspace, commands)
    check = run_script(task_dir / "check.sh", workspace, task_dir)

    report.update({
        "agent_status": result.get("status"),
        "agent_steps": result.get("steps"),
        "agent_tokens": result.get("usage", {}),
        "agent_text": result.get("text", "")[-4000:],
        "agent_error": run["error"],
        "duration_ms": run["duration_ms"],
        "tool_calls": trace.get("tool_calls"),
        "trace_duration_ms": trace.get("duration_ms"),
        "approvals": run["approvals"],
        "changed_files": changed,
        "command_count": len(commands),
        "outside_workspace_commands": outside,
        "test_edits": [path for path in changed if path.startswith(("tests/", "tui/tests/"))],
        "check": {"exit_code": check.returncode, "output": (check.stdout + check.stderr)[-6000:]},
        "hidden_leak_attempts": [line for line in (workdir / "agent.log").read_text(encoding="utf-8", errors="replace").splitlines()
                                 if "benchmarks/local" in line][:5],
        "passed": check.returncode == 0,
    })
    if run["error"]:
        report["reason"] = run["error"]
    report["failure_class"] = failure_class(report)
    (workdir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if clean:
        shutil.rmtree(workspace, ignore_errors=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", help="逗号分隔的任务 id，默认全部")
    parser.add_argument("--prepare", action="store_true", help="只注入 bug 并确认初始失败，不调用模型")
    parser.add_argument("--timeout", type=float, default=900, help="单个任务的 Agent 超时秒数")
    parser.add_argument("--fake", action="store_true", help="用 fake provider 验证跑分链路（必然不通过）")
    parser.add_argument("--clean", action="store_true", help="跑完删除临时工作区（默认保留供复查）")
    args = parser.parse_args()
    selected = [item.strip() for item in args.tasks.split(",")] if args.tasks else task_ids()
    unknown = [task for task in selected if task not in task_ids()]
    if unknown:
        parser.error(f"unknown tasks: {', '.join(unknown)}")
    if not args.prepare and not args.fake and not any(os.environ.get(name) for name in ("VIBE_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")):
        print("未检测到 API key；先用 --prepare 校验夹具，或用你的模型环境运行。", file=sys.stderr)
        return 2

    stamp = time.strftime("%Y%m%d-%H%M%S")
    reports = []
    for task in selected:
        print(f"[{task}] 准备 workspace...", flush=True)
        report = run_task(task, args.prepare, args.timeout, stamp, args.fake, args.clean)
        reports.append(report)
        if args.prepare:
            marker = "RED" if report.get("red_expected") else "UNEXPECTED PASS"
            print(f"[{task}] {marker}（setup rc={report['setup']['exit_code']}，red rc={report['red']['exit_code']}）", flush=True)
        else:
            status = "PASS" if report.get("passed") else "FAIL"
            tokens = report.get("agent_tokens", {}).get("total_tokens")
            print(
                f"[{task}] {status} status={report.get('agent_status')} steps={report.get('agent_steps')} "
                f"tokens={tokens} tool_calls={report.get('tool_calls')} "
                f"changed={len(report.get('changed_files', []))} check_rc={report.get('check', {}).get('exit_code')} "
                f"class={report.get('failure_class')}",
                flush=True,
            )
    summary = {
        "stamp": stamp,
        "prepare": args.prepare,
        "fake": args.fake,
        "tasks": reports,
        "passed": sum(1 for report in reports if report.get("passed")),
        "total": len(reports),
    }
    summary_path = RUN_ROOT / stamp / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{summary_path}", flush=True)
    if args.prepare:
        return 0 if all(report.get("red_expected") for report in reports) else 1
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
