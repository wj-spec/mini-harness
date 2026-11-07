"""只读会话观测：聚合 trace.jsonl 的计数、用量与停止原因。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_TRACE_LINES = 500_000


def summarize_trace(session_root: str | Path) -> dict[str, Any]:
    """返回 trace.jsonl 的聚合摘要；文件缺失/损坏时返回空摘要而不抛错。"""
    root = Path(session_root)
    path = root / "trace.jsonl"
    event_counts: dict[str, int] = {}
    runs = 0
    llm_calls = 0
    llm_retries = 0
    llm_errors = 0
    tool_calls = 0
    tool_failures = 0
    approvals = 0
    denials = 0
    prompt_tokens = 0
    completion_tokens = 0
    cost_usd = 0.0
    estimated = False
    malformed = 0
    stop_reasons: dict[str, int] = {}
    last_status: str | None = None
    first_ts: Any = None
    last_ts: Any = None
    lines: list[str] = []
    if path.is_file():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()[:MAX_TRACE_LINES]
        except OSError:
            lines = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(event, dict):
            malformed += 1
            continue
        kind = event.get("type")
        if not isinstance(kind, str):
            malformed += 1
            continue
        event_counts[kind] = event_counts.get(kind, 0) + 1
        ts = event.get("ts")
        if ts is not None:
            first_ts = first_ts if first_ts is not None else ts
            last_ts = ts
        if kind == "run_start":
            runs += 1
        elif kind == "llm_end":
            llm_calls += 1
            prompt_tokens += int(event.get("prompt_tokens") or 0)
            completion_tokens += int(event.get("completion_tokens") or 0)
            cost_usd += float(event.get("cost_usd") or 0.0)
            estimated = estimated or bool(event.get("usage_estimated", False))
        elif kind == "llm_retry":
            llm_retries += 1
        elif kind == "llm_error":
            llm_errors += 1
        elif kind == "tool_end":
            tool_calls += 1
            if event.get("ok") is False:
                tool_failures += 1
        elif kind == "approval":
            approvals += 1
            if event.get("allowed") is False:
                denials += 1
        elif kind == "run_end":
            status = event.get("status")
            if isinstance(status, str):
                last_status = status
            reason = event.get("stop_reason") or ""
            key = str(reason)
            stop_reasons[key] = stop_reasons.get(key, 0) + 1
    return {
        "session": root.name,
        "trace_file": str(path),
        "events": sum(event_counts.values()),
        "malformed": malformed,
        "runs": runs,
        "llm_calls": llm_calls,
        "llm_retries": llm_retries,
        "llm_errors": llm_errors,
        "tool_calls": tool_calls,
        "tool_failures": tool_failures,
        "approvals": approvals,
        "denials": denials,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": round(cost_usd, 6),
        "usage_estimated": estimated,
        "last_status": last_status,
        "stop_reasons": stop_reasons,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "event_counts": event_counts,
    }


def render_trace_summary(summary: dict[str, Any]) -> str:
    lines = [f"会话 {summary.get('session')} trace 摘要："]
    lines.append(f"- 事件：{summary.get('events', 0)}（损坏 {summary.get('malformed', 0)} 行）")
    lines.append(f"- runs：{summary.get('runs', 0)}（最近状态：{summary.get('last_status') or '未运行'}）")
    lines.append(
        f"- LLM：calls {summary.get('llm_calls', 0)} · retries {summary.get('llm_retries', 0)} · "
        f"errors {summary.get('llm_errors', 0)} · tokens {summary.get('total_tokens', 0):,} "
        f"(in {summary.get('prompt_tokens', 0):,} / out {summary.get('completion_tokens', 0):,}) · "
        f"cost ${float(summary.get('cost_usd') or 0.0):.6f}"
    )
    lines.append(
        f"- tools：calls {summary.get('tool_calls', 0)} · failures {summary.get('tool_failures', 0)} · "
        f"approvals {summary.get('approvals', 0)} · denied {summary.get('denials', 0)}"
    )
    reasons = summary.get("stop_reasons") or {}
    if reasons:
        rendered = "、".join(
            f"{reason or '(none)'}={count}" for reason, count in sorted(reasons.items())
        )
        lines.append(f"- 停止原因：{rendered}")
    window = ""
    if summary.get("first_ts") is not None or summary.get("last_ts") is not None:
        window = f"- 时间窗：{summary.get('first_ts')} → {summary.get('last_ts')}"
    if window:
        lines.append(window)
    return "\n".join(lines)
