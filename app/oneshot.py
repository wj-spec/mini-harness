"""非交互跑分入口：一次 Agent.run，自动审批、输出 telemetry。

用于 Harbor / SWE-bench / Terminal-Bench 适配；不启动 UI、不读用户偏好、
不发现技能或 MCP。`bench=True` 时额外放宽工具路径限制并自动批准动作，
只应在一次性容器中使用。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent import Agent, AgentResult
from app.bench_profile import BENCH_PROMPT
from app.config import Config
from app.policy import ApprovalDecision, ApprovalGate
from app.session import SessionStore
from app.trace import Pricing
from core.context import ContextBuilder
from core.state import TaskState
from core.summarize import summarize_messages

DISABLED_TOOLS = frozenset({"web_search", "list_skills", "load_skill", "unload_skill"})


@dataclass
class OneShotOptions:
    task: str
    telemetry_out: Path | None = None
    runtime_dir: Path | None = None
    bench: bool = False
    require_tool_use: bool = True
    max_steps: int | None = None
    max_run_seconds: float | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _agent_version() -> str:
    override = os.getenv("VIBE_BENCH_AGENT_VERSION", "").strip()
    if override:
        return override
    root = Path(__file__).resolve().parent.parent
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        version = out.stdout.strip()
        return version if out.returncode == 0 and version else "unknown"
    except Exception:
        return "unknown"


def _clip(value: object, limit: int = 2000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _write_telemetry(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


async def _auto_approve(call, preview):
    return ApprovalDecision(True, "once")


async def _run_agent(agent: Agent, task: str, *, on_tool, on_result, on_trace, on_message,
                     on_state, on_history, on_context) -> AgentResult:
    return await agent.run(
        task,
        on_tool=on_tool,
        on_result=on_result,
        trace=on_trace,
        on_message=on_message,
        on_state=on_state,
        on_history=on_history,
        on_context=on_context,
    )


def run_oneshot(config: Config, provider: Any, options: OneShotOptions) -> tuple[AgentResult, int]:
    """执行一次任务并写 telemetry；返回 (result, exit_code)。"""
    task = (options.task or "").strip()
    if not task:
        raise ValueError("task must not be empty")
    workspace = Path(config.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace}")
    # provider 已持有 key；不要让它继承给 benchmark 里的 bash 子进程。
    scrubbed_env = {
        name: os.environ.pop(name)
        for name in ("VIBE_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")
        if name in os.environ
    }
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    runtime = Path(options.runtime_dir).expanduser() if options.runtime_dir else None
    runtime = (runtime or workspace / ".vibe" / "bench-run" / stamp).resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    telemetry_path = (
        Path(options.telemetry_out).expanduser().resolve()
        if options.telemetry_out else runtime / "telemetry.json"
    )
    skills_config = runtime / "skills.json"
    skills_config.write_text('{"discover": false, "paths": []}\n', encoding="utf-8")

    session = SessionStore.new(
        workspace, config.model, purpose="test", sessions_root=runtime / "sessions",
    )
    state = TaskState(goal=task)
    context = ContextBuilder(
        policy=config.budget_policy(),
        keep_recent_groups=config.keep_recent_groups,
        offload_dir=runtime / "offload",
        summarizer=summarize_messages,
    )
    from app.tools import ToolRegistry

    tools = ToolRegistry(
        workspace, context=context, state=state,
        skills_config=str(skills_config), bench_mode=options.bench,
    )
    for name in DISABLED_TOOLS:
        tools._tools.pop(name, None)
    approval = ApprovalGate(
        _auto_approve if options.bench else None,
        audit_path=session.audit_path,
    )
    agent = Agent(
        provider, tools,
        max_steps=config.max_steps if options.max_steps is None else options.max_steps,
        max_run_seconds=config.max_run_seconds if options.max_run_seconds is None else options.max_run_seconds,
        llm_timeout=config.llm_timeout,
        llm_retries=config.llm_retries,
        llm_retry_backoff=config.llm_retry_backoff,
        max_continuations=config.max_continuations,
        max_no_progress=config.max_no_progress,
        max_calls_without_progress=config.max_calls_without_progress,
        max_reads_per_file=config.max_reads_per_file,
        max_progress_warnings=config.max_progress_warnings,
        max_repeated_calls=config.max_repeated_calls,
        max_run_tokens=config.max_run_tokens,
        compact_interval_tokens=config.compact_interval_tokens,
        max_run_cost_usd=config.max_run_cost_usd,
        tool_retries=config.tool_retries,
        context=context,
        state=state,
        approval=approval,
        history=[],
        pricing=Pricing(config.input_price_per_1m, config.output_price_per_1m),
        require_tool_use=options.require_tool_use,
    )
    if options.bench:
        agent.add_system_part(
            kind="bench_profile", label="跑分模式", source="bench",
            content="\n\n" + BENCH_PROMPT,
        )

    tool_calls: list[tuple[str, bool | None]] = []
    started = _now()
    clock = time.monotonic()
    error: str | None = None

    def on_tool(call):
        tool_calls.append((call.name, None))
        print(f"[tool] {call.name} {_clip(call.args, 800)}", flush=True)

    def on_result(call, ok, output):
        if tool_calls:
            name, _ = tool_calls[-1]
            tool_calls[-1] = (name, bool(ok))
        print(f"[tool] {call.name} -> {'ok' if ok else 'fail'} {_clip(output, 1200)}", flush=True)

    def on_trace(event):
        try:
            session.append_trace(event)
        except OSError as exc:
            print(f"[warn] trace save failed: {exc}", file=sys.stderr, flush=True)

    def on_state(value):
        try:
            session.save_state(value)
        except OSError as exc:
            print(f"[warn] state save failed: {exc}", file=sys.stderr, flush=True)

    def on_history(value):
        try:
            session.save_context(value)
        except OSError as exc:
            print(f"[warn] context save failed: {exc}", file=sys.stderr, flush=True)

    def on_context(value):
        try:
            session.append_event("context", total_tokens=value.tokens, sections=value.sections)
        except OSError as exc:
            print(f"[warn] context event save failed: {exc}", file=sys.stderr, flush=True)

    try:
        result = asyncio.run(_run_agent(
            agent, task,
            on_tool=on_tool, on_result=on_result, on_trace=on_trace,
            on_message=session.append_message, on_state=on_state,
            on_history=on_history, on_context=on_context,
        ))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        print(f"[error] {error}", file=sys.stderr, flush=True)
        result = AgentResult(f"error: {error}", 0, "error")
    finally:
        os.environ.update(scrubbed_env)
        for label, save, value in (
            ("context", session.save_context, agent.history),
            ("state", session.save_state, agent.state),
        ):
            try:
                save(value)
            except OSError as exc:
                print(f"[warn] {label} save failed: {exc}", file=sys.stderr, flush=True)

    ended = _now()
    telemetry = {
        "schema_version": 1,
        "status": result.status,
        "stop_reason": result.stop_reason,
        "steps": result.steps,
        "error": error or result.error,
        "usage": result.usage,
        "cost_usd": result.cost_usd,
        "run_id": result.run_id,
        "model": getattr(provider, "model", "") or config.model,
        "provider": "fake" if type(provider).__name__ == "FakeProvider" else config.provider,
        "tool_calls": len(tool_calls),
        "tool_calls_ok": sum(1 for _, ok in tool_calls if ok),
        "duration_ms": round((time.monotonic() - clock) * 1000, 1),
        "started_at": _iso(started),
        "ended_at": _iso(ended),
        "workspace": str(workspace),
        "runtime_dir": str(runtime),
        "session_dir": str(session.root),
        "agent_version": _agent_version(),
        "bench": options.bench,
        "limits": {
            "max_steps": agent.max_steps,
            "max_run_seconds": agent.max_run_seconds,
            "max_run_tokens": config.max_run_tokens,
            "context_window": config.context_window,
        },
    }
    _write_telemetry(telemetry_path, telemetry)
    usage = result.usage or {}
    print(
        f"[vibe] status={result.status} stop={result.stop_reason or '-'} "
        f"steps={result.steps} tokens={usage.get('total_tokens', 0)} "
        f"cost=${result.cost_usd:.4f} telemetry={telemetry_path}",
        flush=True,
    )
    return result, 0 if result.status in {"done", "stopped"} else 1
