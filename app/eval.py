from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.agent import Agent
from app.policy import ApprovalDecision, ApprovalGate
from app.sandbox import run_command
from app.tools import ToolRegistry
from app.trace import Pricing
from core.budget import BudgetPolicy
from core.context import ContextBuilder
from core.state import TaskState

DEFAULT_TASKS = Path(__file__).resolve().parent.parent / "evals" / "tasks.json"


@dataclass
class EvalTask:
    id: str
    prompt: str
    files: dict[str, str] = field(default_factory=dict)
    checks: list[dict] = field(default_factory=list)
    max_steps: int = 15


@dataclass
class EvalResult:
    id: str
    passed: bool
    status: str
    steps: int
    duration_ms: float
    usage: dict
    cost_usd: float
    checks: list[dict]
    error: str | None = None
    workspace: str = ""


@dataclass
class EvalReport:
    timestamp: str
    model: str
    total: int
    passed: int
    previous_passed: int | None
    delta: int | None
    results: list[EvalResult]
    run_dir: str = ""


def load_tasks(path: str | Path = DEFAULT_TASKS) -> list[EvalTask]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [EvalTask(**item) for item in data]


def run_checks(task: EvalTask, workspace: Path) -> list[dict]:
    results: list[dict] = []
    for check in task.checks:
        kind = check.get("type", "unknown")
        ok = False
        detail = ""
        try:
            if kind == "file_exists":
                ok = (workspace / check["path"]).exists()
                detail = f"{check['path']} exists={ok}"
            elif kind == "file_equals":
                actual = (workspace / check["path"]).read_text(encoding="utf-8").strip()
                ok = actual == check["text"].strip()
                detail = f"actual={actual[:120]!r}"
            elif kind == "file_contains":
                actual = (workspace / check["path"]).read_text(encoding="utf-8")
                ok = check["text"] in actual
                detail = f"contains={ok}"
            elif kind == "file_not_contains":
                actual = (workspace / check["path"]).read_text(encoding="utf-8")
                ok = check["text"] not in actual
                detail = f"not_contains={ok}"
            elif kind == "command":
                code, output, timed_out = run_command(
                    check["command"], workspace, timeout=float(check.get("timeout", 30))
                )
                ok = not timed_out and code == int(check.get("exit", 0))
                if check.get("contains"):
                    ok = ok and check["contains"] in output
                if check.get("not_contains"):
                    ok = ok and check["not_contains"] not in output
                detail = f"exit={code} timed_out={timed_out} output={output[-200:]!r}"
            else:
                detail = f"unknown check type: {kind}"
        except Exception as exc:
            ok = False
            detail = f"{type(exc).__name__}: {exc}"
        results.append({"type": kind, "ok": ok, "detail": detail[:400]})
    return results


def _previous_passed(base: Path) -> int | None:
    reports = sorted(
        base.glob("*/report.json"), key=lambda p: p.stat().st_mtime
    )
    if not reports:
        return None
    try:
        return int(json.loads(reports[-1].read_text(encoding="utf-8"))["passed"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _write_report(report: EvalReport, run_dir: Path) -> None:
    (run_dir / "report.json").write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# Eval report",
        "",
        f"- model: `{report.model}`",
        f"- passed: **{report.passed}/{report.total}**",
        f"- previous: {report.previous_passed} (delta {report.delta})",
        "",
        "| task | result | status | steps | tokens | cost |",
        "|---|---|---|---|---|---|",
    ]
    for result in report.results:
        mark = "PASS" if result.passed else "FAIL"
        lines.append(
            f"| {result.id} | {mark} | {result.status} | {result.steps} | "
            f"{result.usage.get('total_tokens', 0)} | ${result.cost_usd:.6f} |"
        )
    lines.append("")
    for result in report.results:
        if not result.passed:
            failed = [c for c in result.checks if not c["ok"]]
            lines.append(f"## {result.id}")
            lines.append(f"- error: {result.error}")
            for check in failed:
                lines.append(f"- {check['type']}: {check['detail']}")
            lines.append("")
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run_eval(
    tasks: list[EvalTask],
    provider_factory: Callable[[EvalTask], object],
    workspace_root: str | Path,
    pricing: Pricing | None = None,
    model: str = "",
    limit: int | None = None,
) -> EvalReport:
    base = Path(workspace_root) / ".vibe" / "evals"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir = base / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    previous = _previous_passed(base)
    selected = tasks[:limit] if limit else tasks
    results: list[EvalResult] = []

    for task in selected:
        task_dir = run_dir / task.id
        task_dir.mkdir(parents=True, exist_ok=True)
        for path, content in task.files.items():
            target = task_dir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        state = TaskState(goal=task.prompt)
        context = ContextBuilder(
            policy=BudgetPolicy.for_context_window(),
            keep_recent_groups=4,
            offload_dir=task_dir / ".vibe" / "offload",
        )
        tools = ToolRegistry(task_dir, context=context, state=state)

        async def allow(call, preview):
            return ApprovalDecision(True, "once")

        provider = provider_factory(task)
        agent = Agent(
            provider,
            tools,
            max_steps=task.max_steps,
            max_run_seconds=120,
            context=context,
            state=state,
            approval=ApprovalGate(handler=allow),
            pricing=pricing,
        )

        started = time.monotonic()
        error = None
        try:
            result = asyncio.run(agent.run(task.prompt))
        except Exception as exc:
            result = None
            error = f"{type(exc).__name__}: {exc}"

        checks = run_checks(task, task_dir)
        passed = error is None and all(check["ok"] for check in checks)
        results.append(
            EvalResult(
                id=task.id,
                passed=passed,
                status=result.status if result else "error",
                steps=result.steps if result else 0,
                duration_ms=round((time.monotonic() - started) * 1000, 1),
                usage=result.usage if result else {},
                cost_usd=result.cost_usd if result else 0.0,
                checks=checks,
                error=error,
                workspace=str(task_dir),
            )
        )

    passed = sum(1 for result in results if result.passed)
    report = EvalReport(
        timestamp=stamp,
        model=model,
        total=len(results),
        passed=passed,
        previous_passed=previous,
        delta=(passed - previous) if previous is not None else None,
        results=results,
        run_dir=str(run_dir),
    )
    _write_report(report, run_dir)
    return report
