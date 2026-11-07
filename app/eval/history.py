"""长任务 benchmark 历史：只读扫描 .vibe/bench-long 下的 report.json。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_REPORTS = 200


def bench_reports(
    workspace: str | Path, *, limit: int = 50,
    stats: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    root = Path(workspace) / ".vibe" / "bench-long"
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/report.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            if stats is not None:
                stats["skipped"] = stats.get("skipped", 0) + 1
            continue
        if not isinstance(payload, dict):
            if stats is not None:
                stats["skipped"] = stats.get("skipped", 0) + 1
            continue
        summary = payload.get("summary") or {}
        rows.append({
            "path": str(path),
            "generated_at": payload.get("generated_at"),
            "mode": payload.get("mode"),
            "agent_version": payload.get("agent_version"),
            "scenarios": payload.get("scenarios") or [],
            "total": summary.get("total"),
            "passed": summary.get("passed"),
            "failed": summary.get("failed"),
            "scenario_stats": summary.get("scenario_stats") or {},
        })
    rows.sort(key=lambda item: str(item.get("generated_at") or ""))
    if limit > 0:
        rows = rows[-min(limit, MAX_REPORTS):]
    return rows


def latest_regression(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """比较最近两次报告：返回通过数下降/失败增加的信息；无回归返回 None。"""
    if len(rows) < 2:
        return None
    previous, latest = rows[-2], rows[-1]
    prev_total = int(previous.get("total") or 0)
    prev_passed = int(previous.get("passed") or 0)
    total = int(latest.get("total") or 0)
    passed = int(latest.get("passed") or 0)
    failed = int(latest.get("failed") or 0)
    if total <= 0:
        return None
    if failed <= 0 and (prev_total <= 0 or passed >= prev_passed):
        return None
    return {
        "previous_passed": prev_passed,
        "previous_total": prev_total,
        "latest_passed": passed,
        "latest_total": total,
        "latest_failed": failed,
        "previous_agent_version": previous.get("agent_version"),
        "latest_agent_version": latest.get("agent_version"),
    }


def render_bench_history(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "当前工作区还没有长任务 benchmark 报告（先运行 scripts/bench_long.py）。"
    lines = [f"长任务 benchmark 历史（{len(rows)} 次）："]
    for row in rows:
        total = row.get("total")
        passed = row.get("passed")
        rate = f"{(int(passed) / int(total) * 100):.0f}%" if total else "-"
        lines.append(
            f"- {row.get('generated_at') or '?'} · {row.get('mode') or '?'} · "
            f"agent {row.get('agent_version') or '?'} · "
            f"{passed}/{total}（{rate}）"
        )
    regression = latest_regression(rows)
    if regression:
        lines.append(
            f"- ⚠ 最近一次较前一次下降：{regression['previous_passed']}/{regression['previous_total']}"
            f" → {regression['latest_passed']}/{regression['latest_total']}"
            f"（agent {regression.get('previous_agent_version') or '?'} → "
            f"{regression.get('latest_agent_version') or '?'}）"
        )
    latest = rows[-1]
    stats = latest.get("scenario_stats") or {}
    if stats:
        lines.append("- 最近一次场景通过率：")
        for name, item in stats.items():
            lines.append(
                f"  - {name}：{item.get('passed')}/{item.get('runs')}"
                + (f" · avg {item.get('avg_duration_seconds')}s" if item.get("avg_duration_seconds") else "")
            )
    return "\n".join(lines)
