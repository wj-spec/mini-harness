"""子 Agent 任务节点 P0：harness 内部只读 runner、结构化结果、trace。"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.runtime.agent import Agent
from app.runtime.modes import READ_ONLY_TOOLS
from app.tools.registry import ToolRegistry

SUBAGENT_SCHEMA_VERSION = 1
SUBAGENT_STATUSES = ("ok", "error", "cancelled", "budget_exceeded", "timeout")


class SubagentError(ValueError):
    pass


@dataclass
class SubagentResult:
    schema_version: int = SUBAGENT_SCHEMA_VERSION
    status: str = "error"
    summary: str = ""
    findings: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    stop_reason: str = ""
    error: str | None = None
    trace_ref: str | None = None
    task_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "SubagentResult":
        if not isinstance(value, dict):
            raise SubagentError("subagent result must be an object")
        if value.get("schema_version") != SUBAGENT_SCHEMA_VERSION:
            raise SubagentError("unsupported subagent result schema_version")
        status = value.get("status")
        if status not in SUBAGENT_STATUSES:
            raise SubagentError(f"invalid subagent status: {status!r}")
        for name in ("findings", "evidence", "usage"):
            if not isinstance(value.get(name, []), type({}) if name == "usage" else list):
                raise SubagentError(f"{name} has invalid type")
        return cls(
            schema_version=SUBAGENT_SCHEMA_VERSION, status=status,
            summary=str(value.get("summary", "")),
            findings=[dict(item) for item in value.get("findings", []) if isinstance(item, dict)],
            evidence=[dict(item) for item in value.get("evidence", []) if isinstance(item, dict)],
            usage=dict(value.get("usage", {})),
            cost_usd=float(value.get("cost_usd", 0.0) or 0.0),
            stop_reason=str(value.get("stop_reason", "")),
            error=value.get("error"),
            trace_ref=value.get("trace_ref"), task_id=value.get("task_id"),
            started_at=value.get("started_at"), ended_at=value.get("ended_at"),
        )


async def run_subagent(
    provider: Any,
    workspace: str | Path,
    objective: str,
    *,
    max_steps: int = 8,
    max_run_tokens: int = 40_000,
    max_run_seconds: float = 120.0,
    cancel: asyncio.Event | None = None,
    trace_path: str | Path | None = None,
) -> SubagentResult:
    """在父 worker 进程内运行只读子 Agent；写/执行在 registry subset 层被拒绝。"""
    if not isinstance(objective, str) or not objective.strip():
        raise SubagentError("objective must be a nonempty string")
    workspace = Path(workspace)
    tools = ToolRegistry(workspace).subset(READ_ONLY_TOOLS)
    agent = Agent(
        provider, tools, approval=None, history=None,
        tools_enabled=True, max_steps=max_steps,
        max_run_tokens=max_run_tokens, max_run_seconds=max_run_seconds,
        completion_guard=False, workspace_audit=False,
    )
    started = _now()
    trace_file = Path(trace_path) if trace_path is not None else None
    trace_handle = None
    if trace_file is not None:
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        trace_handle = trace_file.open("a", encoding="utf-8")

    def trace(event: dict[str, Any]) -> None:
        if trace_handle is not None:
            trace_handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            trace_handle.flush()

    try:
        result = await asyncio.wait_for(
            agent.run(objective, trace=trace, cancel=cancel),
            timeout=max_run_seconds + 30,
        )
    except asyncio.TimeoutError:
        return SubagentResult(
            status="timeout", summary="subagent timed out", stop_reason="timeout",
            error="timeout", started_at=started, ended_at=_now(),
            trace_ref=str(trace_file) if trace_file else None,
        )
    except asyncio.CancelledError:
        raise
    finally:
        if trace_handle is not None:
            trace_handle.close()
    status_map = {"done": "ok", "cancelled": "cancelled", "stopped": "budget_exceeded", "error": "error"}
    status = status_map.get(result.status, "error")
    return SubagentResult(
        status=status, summary=(result.text or "")[:4000],
        usage=dict(result.usage or {}), cost_usd=float(result.cost_usd or 0.0),
        stop_reason=result.stop_reason or "", error=result.error,
        trace_ref=str(trace_file) if trace_file else None,
        started_at=started, ended_at=_now(),
    )


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")
