from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

from app.agent import Agent
from app.config import Config
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.sandbox import check_command, run_command
from app.tools import ToolRegistry
from app.trace import Pricing
from core.budget import BudgetPolicy
from core.context import ContextBuilder
from core.models import ToolCall
from core.state import TaskState

DEFAULT_TASKS = Path(__file__).resolve().parent.parent / "evals" / "tasks.json"
PROTOCOL_VERSION = "internal-v3"
DISABLED_TOOLS = {"web_search", "list_skills", "load_skill", "unload_skill"}


@dataclass
class EvalTask:
    id: str
    prompt: str
    files: dict[str, str] = field(default_factory=dict)
    checks: list[dict] = field(default_factory=list)
    max_steps: int = 15
    protected_files: list[str] = field(default_factory=list)


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
    stop_reason: str = ""
    text: str = ""


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
    fingerprint: str = ""
    metadata: dict = field(default_factory=dict)


def eval_diagnostics(report: EvalReport) -> list[str]:
    lines = []
    for result in report.results:
        if result.passed:
            continue
        if result.error or result.status == "error":
            lines.append(f"{result.id}: runtime error: {result.error or result.text}")
        elif result.stop_reason == "no_action":
            lines.append(f"{result.id}: unfinished: no_action; 未发起工具调用")
        elif result.stop_reason in {"token_budget", "cost_budget", "context_window"}:
            lines.append(f"{result.id}: budget paused: {result.stop_reason}")
        elif result.status != "done":
            lines.append(f"{result.id}: unfinished: {result.stop_reason or result.status}")
        failed = [check for check in result.checks if not check["ok"]]
        for check in failed[:3]:
            lines.append(f"{result.id}: check failed: {check['type']}: {check.get('detail', '')}")
    return lines


def eval_exit_code(report: EvalReport) -> int:
    if any(result.error or result.status == "error" for result in report.results):
        return 2
    return 0 if report.passed == report.total else 1


def _relative_path(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value
            or "\x00" in value or PurePosixPath(value).is_absolute()
            or any(part in {"", ".", "..", ".vibe"} for part in value.split("/"))):
        raise ValueError(f"invalid eval path: {value!r}")
    return value


def _target(workspace: Path, value: str) -> Path:
    root = workspace.resolve()
    path = (root / _relative_path(value)).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"eval path outside workspace: {value}")
    return path


def _validate_task(task: EvalTask) -> None:
    if (not isinstance(task.id, str) or task.id in {"report.json", "report.md", "skills.json"}
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", task.id)):
        raise ValueError(f"invalid eval task id: {task.id!r}")
    if not task.checks:
        raise ValueError(f"eval task {task.id} requires at least one check")
    if not isinstance(task.max_steps, int) or task.max_steps < 0:
        raise ValueError("max_steps must be nonnegative")
    for path, content in task.files.items():
        _relative_path(path)
        if not isinstance(content, str):
            raise ValueError("eval file content must be text")
    for path in task.protected_files:
        _relative_path(path)
        if path not in task.files:
            raise ValueError(f"protected file has no initial content: {path}")
    for check in task.checks:
        kind = check.get("type")
        if kind in {"file_exists", "file_equals", "file_contains", "file_not_contains"}:
            _relative_path(check.get("path"))
        elif kind == "command":
            if not isinstance(check.get("command"), str) or not check["command"].strip():
                raise ValueError("command check requires a command")
            timeout = float(check.get("timeout", 30))
            if not 0 < timeout <= 3600:
                raise ValueError("command check timeout must be in (0, 3600]")
        else:
            raise ValueError(f"unknown check type: {kind}")


def load_tasks(path: str | Path = DEFAULT_TASKS) -> list[EvalTask]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tasks = [EvalTask(**item) for item in data]
    for task in tasks:
        _validate_task(task)
    if len({task.id for task in tasks}) != len(tasks):
        raise ValueError("duplicate eval task ids")
    return tasks


async def _allow_local_eval(call, preview):
    return ApprovalDecision(True, "once")


def run_checks(task: EvalTask, workspace: Path, *, approval: ApprovalGate | None = None) -> list[dict]:
    """受信本地检查；审批不构成系统沙箱。"""
    gate = approval or ApprovalGate(handler=_allow_local_eval)
    results: list[dict] = []
    if not task.checks:
        return [{"type": "configuration", "ok": False, "detail": "no checks configured"}]
    for check in task.checks:
        kind = check.get("type", "unknown")
        ok = False
        detail = ""
        try:
            if kind == "file_exists":
                ok = _target(workspace, check["path"]).exists()
                detail = f"{check['path']} exists={ok}"
            elif kind == "file_equals":
                actual = _target(workspace, check["path"]).read_text(encoding="utf-8").strip()
                ok = actual == check["text"].strip()
                detail = f"actual={actual[:120]!r}"
            elif kind in {"file_contains", "file_not_contains"}:
                actual = _target(workspace, check["path"]).read_text(encoding="utf-8")
                ok = (check["text"] in actual) == (kind == "file_contains")
                detail = f"{kind}={ok}"
            elif kind == "command":
                command = check["command"]
                call = ToolCall("bash", {"command": command, "cwd": "."})
                decision = asyncio.run(gate.request(call, f"eval grading: {command}", workspace))
                if not decision.allowed:
                    raise PermissionError("grading command denied by ApprovalGate")
                check_command(command)
                code, output, timed_out = run_command(
                    command, workspace, timeout=float(check.get("timeout", 30))
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


def _protected_paths(task: EvalTask) -> list[str]:
    paths = set(task.protected_files)
    if any(check.get("type") == "command" for check in task.checks):
        for path in task.files:
            name = PurePosixPath(path).name
            if (name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py")
                    or name in {"test.py", "test.sh"} or "tests" in PurePosixPath(path).parts):
                paths.add(path)
    return sorted(paths)


def _protected_checks(task: EvalTask, workspace: Path) -> list[dict]:
    results = []
    for path in _protected_paths(task):
        try:
            target = _target(workspace, path)
            ok = not (workspace / path).is_symlink() and target.read_text(encoding="utf-8") == task.files[path]
        except (OSError, UnicodeError, ValueError):
            ok = False
        results.append({"type": "protected_files", "ok": ok,
                        "detail": f"{path}: {'unchanged' if ok else 'modified or missing grading fixture'}"})
    return results


def _previous_passed(base: Path, fingerprint: str) -> int | None:
    reports = sorted(base.glob("*/report.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in reports:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if report.get("fingerprint") == fingerprint:
                return int(report["passed"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return None


def _append_json(path: Path, value: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def _write_report(report: EvalReport, run_dir: Path) -> None:
    (run_dir / "report.json").write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = ["# Eval report", "", f"- model: `{report.model}`",
             f"- mode: `{report.metadata['mode']}`",
             "- isolation: local process; no OS sandbox; shell network is not blocked",
             f"- protocol: `{report.metadata['protocol']}`", f"- fingerprint: `{report.fingerprint}`",
             f"- passed: **{report.passed}/{report.total}**",
             f"- comparable previous: {report.previous_passed} (delta {report.delta})", "",
             "| task | result | status | steps | tokens | cost |",
             "|---|---|---|---|---|---|"]
    for result in report.results:
        mark = "PASS" if result.passed else "FAIL"
        lines.append(f"| {result.id} | {mark} | {result.status} | {result.steps} | "
                     f"{result.usage.get('total_tokens', 0)} | ${result.cost_usd:.6f} |")
    diagnostics = eval_diagnostics(report)
    if diagnostics:
        lines.extend(["", "## Diagnostics", "", *[f"- {line}" for line in diagnostics]])
    (run_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_eval(
    tasks: list[EvalTask],
    provider_factory: Callable[[EvalTask], object],
    workspace_root: str | Path,
    pricing: Pricing | None = None,
    model: str = "",
    limit: int | None = None,
    *,
    config: Config | None = None,
) -> EvalReport:
    if limit is not None and limit <= 0:
        raise ValueError("eval limit must be positive")
    selected = tasks[:limit] if limit is not None else tasks
    if not selected or len({task.id for task in selected}) != len(selected):
        raise ValueError("eval requires nonempty tasks with unique ids")
    for task in selected:
        _validate_task(task)
    providers = [provider_factory(task) for task in selected]
    mode = "fake" if all(isinstance(provider, FakeProvider) for provider in providers) else "model"
    effective = config or Config(max_run_seconds=120, keep_recent_groups=4)
    pricing = pricing or Pricing(effective.input_price_per_1m, effective.output_price_per_1m)
    agent_options = {key: getattr(effective, key) for key in (
        "max_run_seconds", "llm_timeout", "llm_retries", "llm_retry_backoff", "max_continuations",
        "max_no_progress", "max_calls_without_progress", "max_reads_per_file", "max_progress_warnings", "max_repeated_calls",
        "max_run_tokens", "compact_interval_tokens", "max_run_cost_usd", "tool_retries",
    )}
    agent_options["require_tool_use"] = True
    steps = {task.id: min((cap for cap in (task.max_steps, effective.max_steps) if cap > 0), default=0)
             for task in selected}
    endpoint = urlsplit(effective.base_url)
    endpoint_label = urlunsplit((endpoint.scheme, endpoint.netloc.rsplit("@", 1)[-1], endpoint.path, "", ""))
    model = model or effective.model
    metadata = {
        "protocol": PROTOCOL_VERSION, "mode": mode,
        "isolation": "local_process_no_os_sandbox", "shell_network": "unrestricted",
        "disabled_tools": sorted(DISABLED_TOOLS), "personal_skills": False, "mcp": False,
        "approval": "trusted_local_eval_auto_approve_via_ApprovalGate",
        "protected_files": {task.id: _protected_paths(task) for task in selected},
        "providers": [f"{type(provider).__module__}.{type(provider).__qualname__}" for provider in providers],
        "model": model, "endpoint": endpoint_label,
        "agent": {**agent_options, "max_steps": steps},
        "context": {"policy": asdict(effective.budget_policy()),
                    "keep_recent_groups": effective.keep_recent_groups, "summarizer": "deterministic"},
        "pricing": asdict(pricing),
    }
    identity = {"metadata": metadata, "tasks": [asdict(task) for task in selected]}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    base = Path(workspace_root).resolve() / ".vibe" / "evals" / mode
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    run_dir = base / stamp
    run_dir.mkdir(parents=True, exist_ok=False)
    previous = _previous_passed(base, fingerprint)
    skills_config = run_dir / "skills.json"
    skills_config.write_text('{"discover": false, "paths": []}', encoding="utf-8")
    results: list[EvalResult] = []

    for task, provider in zip(selected, providers):
        task_dir = run_dir / task.id
        task_dir.mkdir()
        artifacts = task_dir / ".vibe"
        artifacts.mkdir()
        for name in ("trace.jsonl", "messages.jsonl", "audit.jsonl"):
            (artifacts / name).touch()
        approval = ApprovalGate(handler=_allow_local_eval, audit_path=artifacts / "audit.jsonl")
        for path, content in task.files.items():
            target = _target(task_dir, path)
            decision = asyncio.run(approval.request(
                ToolCall("write_file", {"path": path, "content": content}), f"eval fixture: {path}", task_dir))
            if not decision.allowed:
                raise PermissionError(f"eval fixture denied: {path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        state = TaskState(goal=task.prompt)
        context = ContextBuilder(policy=effective.budget_policy(),
                                 keep_recent_groups=effective.keep_recent_groups,
                                 offload_dir=artifacts / "offload")
        tools = ToolRegistry(task_dir, context=context, state=state, skills_config=str(skills_config))
        for name in DISABLED_TOOLS:
            tools._tools.pop(name, None)
        agent = Agent(provider, tools, max_steps=steps[task.id], context=context,
                      state=state, approval=approval, pricing=pricing, **agent_options)
        started = time.monotonic()
        error = None
        try:
            result = asyncio.run(agent.run(
                task.prompt,
                trace=lambda event: _append_json(artifacts / "trace.jsonl", event),
                on_message=lambda message: _append_json(artifacts / "messages.jsonl", asdict(message)),
            ))
        except Exception as exc:
            result = None
            error = f"{type(exc).__name__}: {exc}"
        if result is not None and (result.error or result.status == "error"):
            error = result.error or result.text
        checks = _protected_checks(task, task_dir)
        if all(check["ok"] for check in checks):
            checks.extend(run_checks(task, task_dir, approval=approval))
            checks.extend(_protected_checks(task, task_dir))
        passed = error is None and result is not None and result.status == "done" and all(check["ok"] for check in checks)
        results.append(EvalResult(
            id=task.id, passed=passed, status=result.status if result else "error",
            steps=result.steps if result else 0,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            usage=result.usage if result else {}, cost_usd=result.cost_usd if result else 0.0,
            checks=checks, error=error, workspace=str(task_dir),
            stop_reason=result.stop_reason if result else "exception", text=result.text if result else "",
        ))
    passed = sum(result.passed for result in results)
    report = EvalReport(timestamp=stamp, model=model, total=len(results), passed=passed,
                        previous_passed=previous, delta=passed - previous if previous is not None else None,
                        results=results, run_dir=str(run_dir), fingerprint=fingerprint, metadata=metadata)
    _write_report(report, run_dir)
    return report
