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
    parent_cancel: asyncio.Event | None = None,
    depth: int = 0,
    max_depth: int = 1,
    trace_path: str | Path | None = None,
    session_root: str | Path | None = None,
    parent_task_id: str | None = None,
) -> SubagentResult:
    """在父 worker 进程内运行只读子 Agent；写/执行在 registry subset 层被拒绝。

    session_root 提供时创建 kind=subagent 的 TaskRecord，并落 subagent-result.json。
    """
    if not isinstance(objective, str) or not objective.strip():
        raise SubagentError("objective must be a nonempty string")
    if depth > max_depth:
        raise SubagentError(f"subagent depth {depth} exceeds max_depth {max_depth}")
    workspace = Path(workspace)
    from app.runtime.tasks import TaskStore

    store = TaskStore(Path(session_root)) if session_root is not None else None
    child_task_id: str | None = None
    run_id = f"sub-{time.time_ns():x}"
    if store is not None:
        record = store.create(
            kind="subagent", objective=objective, workspace=str(workspace),
            mode="act", parent_id=parent_task_id,
        )
        child_task_id = record.task_id
        store.acquire_lock(child_task_id)
        store.start_segment(child_task_id, run_id=run_id)
    tools = ToolRegistry(workspace).subset(READ_ONLY_TOOLS)
    agent = Agent(
        provider, tools, approval=None, history=None,
        tools_enabled=True, max_steps=max_steps,
        max_run_tokens=max_run_tokens, max_run_seconds=max_run_seconds,
        completion_guard=False, workspace_audit=False,
    )
    watcher = None
    if parent_cancel is not None:
        if cancel is None:
            cancel = parent_cancel
        else:
            combined = asyncio.Event()

            async def forward() -> None:
                waiters = [asyncio.create_task(cancel.wait()), asyncio.create_task(parent_cancel.wait())]
                try:
                    await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in waiters:
                        task.cancel()
                combined.set()

            watcher = asyncio.create_task(forward())
            cancel = combined
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
        return _finish_subagent(
            store, child_task_id, run_id, workspace,
            SubagentResult(
                status="timeout", summary="subagent timed out", stop_reason="timeout",
                error="timeout", started_at=started, ended_at=_now(),
                trace_ref=str(trace_file) if trace_file else None,
                task_id=child_task_id,
            ), parent_task_id=parent_task_id,
        )
    except asyncio.CancelledError:
        raise
    finally:
        if watcher is not None:
            watcher.cancel()
        if trace_handle is not None:
            trace_handle.close()
    status_map = {"done": "ok", "cancelled": "cancelled", "stopped": "budget_exceeded", "error": "error"}
    status = status_map.get(result.status, "error")
    return _finish_subagent(
        store, child_task_id, run_id, workspace,
        SubagentResult(
            status=status, summary=(result.text or "")[:4000],
            usage=dict(result.usage or {}), cost_usd=float(result.cost_usd or 0.0),
            stop_reason=result.stop_reason or "", error=result.error,
            trace_ref=str(trace_file) if trace_file else None,
            started_at=started, ended_at=_now(), task_id=child_task_id,
        ), parent_task_id=parent_task_id,
    )


def _finish_subagent(
    store: Any, task_id: str | None, run_id: str, workspace: Path,
    result: SubagentResult, parent_task_id: str | None = None,
) -> SubagentResult:
    if store is None or task_id is None:
        return result
    mapping = {
        "ok": ("done", "completed"), "error": ("failed", "failed"),
        "cancelled": ("cancelled", "cancelled"), "budget_exceeded": ("stopped", "failed"),
        "timeout": ("failed", "failed"),
    }
    segment_status, task_status = mapping.get(result.status, ("failed", "failed"))
    try:
        if result.usage or result.cost_usd:
            try:
                from app.runtime.budget import BudgetLedger
                ledger = BudgetLedger(store.root)
                ledger.on_trace(
                    task_id=task_id, goal_id=None, parent_id=parent_task_id,
                    node_kind="subagent", segment=1, run_id=run_id,
                    event={
                        "type": "llm_end",
                        "prompt_tokens": int(result.usage.get("prompt_tokens", 0) or 0),
                        "completion_tokens": int(result.usage.get("completion_tokens", 0) or 0),
                        "cost_usd": float(result.cost_usd or 0.0),
                        "usage_estimated": bool(result.usage.get("estimated", False)),
                    },
                )
            except Exception:
                pass
        store.end_segment(task_id, run_id=run_id, status=segment_status,
                          stop_reason=result.stop_reason or None)
        store.transition(task_id, task_status, reason=result.status)
        (store.task_dir(task_id) / "subagent-result.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    finally:
        try:
            store.release_lock(task_id)
        except Exception:
            pass
    return result


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")
