"""汇总 Harbor 跑分结果：每题分数、Agent 错误、失败测试、耗时、日志路径。

用法:
    python3 scripts/bench_report.py jobs/agent-batch20b-...
    python3 scripts/bench_report.py jobs/agent-batch20b-... --failures-only
    python3 scripts/bench_report.py jobs/agent-batch20b-... --format csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _seconds(timer: dict | None) -> float | None:
    if not isinstance(timer, dict):
        return None
    try:
        from datetime import datetime

        start = datetime.fromisoformat(str(timer["started_at"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(timer["finished_at"]).replace("Z", "+00:00"))
        return round((end - start).total_seconds(), 1)
    except (KeyError, TypeError, ValueError):
        return None


def _failures(report: dict | None) -> list[str]:
    if not report:
        return []
    item = next(iter(report.values()), None)
    if not isinstance(item, dict):
        return []
    tests = item.get("tests_status") or {}
    names: list[str] = []
    for kind in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        names.extend((tests.get(kind) or {}).get("failure") or [])
    return names


def collect_trials(job_dir: str | Path) -> list[dict]:
    job_dir = Path(job_dir)
    rows: list[dict] = []
    for trial_dir in sorted(path for path in job_dir.iterdir() if path.is_dir()):
        result = _read_json(trial_dir / "result.json")
        if result is None:
            continue
        telemetry = _read_json(trial_dir / "agent" / "telemetry.json") or {}
        report = _read_json(trial_dir / "verifier" / "report.json")
        item = next(iter(report.values()), {}) if report else {}
        rewards = (result.get("verifier_result") or {}).get("rewards") or {}
        exception = result.get("exception_info") or {}
        agent_result = result.get("agent_result") or {}
        patch = trial_dir / "agent" / "model.patch"
        rows.append(
            {
                "instance": result.get("task_name") or trial_dir.name.split("__")[0],
                "trial": result.get("trial_name") or trial_dir.name,
                "reward": rewards.get("reward"),
                "resolved": item.get("resolved"),
                "agent_status": telemetry.get("status"),
                "agent_error": telemetry.get("error"),
                "steps": telemetry.get("steps"),
                "tool_calls": telemetry.get("tool_calls"),
                "tokens": (telemetry.get("usage") or {}).get("total_tokens"),
                "input_tokens": agent_result.get("n_input_tokens"),
                "output_tokens": agent_result.get("n_output_tokens"),
                "exception": exception.get("exception_type") or exception.get("type"),
                "exception_message": exception.get("exception_message") or exception.get("message"),
                "failed_tests": _failures(report),
                "agent_setup_sec": _seconds(result.get("agent_setup")),
                "agent_sec": _seconds(result.get("agent_execution")),
                "verifier_sec": _seconds(result.get("verifier")),
                "patch_bytes": patch.stat().st_size if patch.is_file() else None,
                "agent_log": str(trial_dir / "agent" / "agent.log"),
                "trial_dir": str(trial_dir),
            }
        )
    return rows


def render(rows: list[dict], *, fmt: str, failures_only: bool) -> str:
    if failures_only:
        rows = [
            row
            for row in rows
            if row["resolved"] is not True
            or row["exception"]
            or row["agent_status"] == "error"
        ]
    if fmt == "json":
        return json.dumps(rows, indent=2, ensure_ascii=False)
    if fmt == "csv":
        out = sys.stdout
        fields = list(rows[0].keys()) if rows else []
        writer = csv.DictWriter(out, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row[key] for key in fields})
        return ""

    lines = [
        "| instance | reward | resolved | agent | steps | tokens | exception | patch |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {instance} | {reward} | {resolved} | {agent_status} | {steps} | {tokens} | {exception} | {patch_bytes} |".format(
                **{key: ("" if row.get(key) is None else row.get(key)) for key in row}
            )
        )
    failed = [row for row in rows if row.get("failed_tests")]
    if failed:
        lines += ["", "## 失败测试", ""]
        for row in failed:
            lines.append(f"### {row['instance']}")
            for name in row["failed_tests"][:20]:
                lines.append(f"- `{name}`")
            if len(row["failed_tests"]) > 20:
                lines.append(f"- ... 共 {len(row['failed_tests'])} 项")
            if row.get("agent_error"):
                lines.append(f"- agent error: `{row['agent_error']}`")
            lines.append(f"- log: `{row['agent_log']}`")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_dir", type=Path, help="Harbor job 目录")
    parser.add_argument("--format", choices=("md", "csv", "json"), default="md")
    parser.add_argument("--failures-only", action="store_true")
    args = parser.parse_args(argv)

    if not args.job_dir.is_dir():
        print(f"not a directory: {args.job_dir}", file=sys.stderr)
        return 2
    rows = collect_trials(args.job_dir)
    if not rows:
        print("no trial result.json found", file=sys.stderr)
        return 2

    resolved = sum(1 for row in rows if row["resolved"] is True)
    print(f"# {args.job_dir}")
    print(f"trials={len(rows)} resolved={resolved} unresolved={len(rows) - resolved}")
    print()
    print(render(rows, fmt=args.format, failures_only=args.failures_only))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
