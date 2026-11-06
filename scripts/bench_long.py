#!/usr/bin/env python3
"""长任务 P0 基准：fake worker 生命周期场景。

用法：
    ./.venv/bin/python3 scripts/bench_long.py --list
    ./.venv/bin/python3 scripts/bench_long.py --scenario detach-continue
    ./.venv/bin/python3 scripts/bench_long.py --scenario detach-continue --runs 3

P0 用 fake provider 驱动真实 worker/task store，覆盖 detach、审批超时、
kill switch、restart-attach 四类可确定性场景；结果写入
.vibe/bench-long/<timestamp>/report.{json,md}。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "benchmarks" / "long"
DEFAULT_ROOT = ROOT / ".vibe" / "bench-long"


def scenario_files() -> list[Path]:
    return sorted(SCENARIOS.glob("*.json"))


def load_scenarios() -> dict[str, dict]:
    result = {}
    for path in scenario_files():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["_path"] = str(path)
        result[data["id"]] = data
    return result


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, timeout=60)


def build_workspace(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], path)
    _git(["config", "user.email", "bench@example.com"], path)
    _git(["config", "user.name", "benchmark"], path)
    (path / ".gitignore").write_text(".vibe/\n", encoding="utf-8")
    (path / "README.md").write_text("long horizon benchmark workspace\n", encoding="utf-8")
    _git(["add", "."], path)
    _git(["commit", "-qm", "benchmark seed"], path)


def file_hashes(workspace: Path) -> dict[str, str]:
    hashes = {}
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        if ".git" in relative.parts or ".vibe" in relative.parts:
            continue
        try:
            hashes[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            pass
    return hashes


def create_task(workspace: Path, objective: str) -> tuple[str, str]:
    sys.path.insert(0, str(ROOT))
    from app.runtime.tasks import TaskStore
    from app.session.store import SessionStore

    session = SessionStore.new(workspace, "fake", purpose="test")
    task = TaskStore(session.root).create(
        kind="goal", objective=objective, workspace=str(workspace), mode="goal",
    )
    return session.id, task.task_id


def task_paths(workspace: Path, session_id: str, task_id: str) -> tuple[Path, Path]:
    root = workspace / ".vibe" / "sessions" / session_id / "tasks" / task_id
    return root, root / "meta.json"


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def wait_for(predicate, timeout: float, interval: float = 0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def summary(workspace: Path, session_id: str, task_id: str, before: dict[str, str]) -> dict:
    task_dir, meta_path = task_paths(workspace, session_id, task_id)
    meta = read_json(meta_path) or {}
    worker = read_json(task_dir / "worker.json") or {}
    approvals = []
    approval_path = task_dir / "approvals.jsonl"
    if approval_path.is_file():
        for line in approval_path.read_text(encoding="utf-8").splitlines():
            try:
                approvals.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    after = file_hashes(workspace)
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    segments = meta.get("segments") or []
    return {
        "task_status": meta.get("status"),
        "segments": len(segments),
        "segment_statuses": [(item.get("segment"), item.get("status"), item.get("stop_reason")) for item in segments],
        "worker_status": worker.get("status"),
        "worker_exit_code": worker.get("exit_code"),
        "approval_statuses": [item.get("status") or item.get("type") for item in approvals],
        "stop_file": (workspace / ".vibe" / "stop").exists(),
        "changed_files": changed,
        "duration_seconds": None,
    }


def run_scenario(scenario: dict, run_dir: Path, run_index: int) -> dict:
    scenario_dir = run_dir / f"{scenario['id']}-{run_index}"
    workspace = scenario_dir / "workspace"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    build_workspace(workspace)
    before = file_hashes(workspace)
    session_id, task_id = create_task(workspace, scenario.get("objective", scenario["id"]))
    task_dir, _ = task_paths(workspace, session_id, task_id)
    from app.runtime.worker import socket_path
    worker_socket = socket_path(task_dir, task_id)

    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(ROOT),
        "VIBE_TASK_RUNTIME": "1",
        "VIBE_WORKER_APPROVAL_TIMEOUT": "1" if scenario["id"] == "approval-timeout" else "60",
    })
    env.update({str(k): str(v) for k, v in scenario.get("worker_env", {}).items()})
    log = (scenario_dir / "worker.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app", "--worker", "--fake",
         "--workspace", str(workspace), "--session", session_id, "--task-id", task_id],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    started = time.monotonic()
    action = scenario.get("action", "wait")
    attach_ok = None
    try:
        if action == "stop":
            assert wait_for(lambda: worker_socket.exists(), 10), "worker socket did not appear"
            time.sleep(0.5)
            from app.runtime.killswitch import stop_workers
            result = stop_workers(workspace)
            attach_ok = bool(result)
            proc.wait(timeout=15)
        elif action == "attach":
            assert wait_for(lambda: worker_socket.exists(), 10), "worker socket did not appear"
            probe = subprocess.Popen(
                [sys.executable, str(ROOT / "scripts" / "acceptance_attach_probe.py"),
                 "--workspace", str(workspace), "--session", session_id, "--task-id", task_id,
                 "--mode", "observe", "--request", "snapshot", "--listen-seconds", "1"],
                cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            probe.wait(timeout=10)
            proc.wait(timeout=scenario.get("timeout_seconds", 60))
        else:
            proc.wait(timeout=scenario.get("timeout_seconds", 60))
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        log.close()

    result = summary(workspace, session_id, task_id, before)
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    result["attach_ok"] = attach_ok
    result["session_id"] = session_id
    result["task_id"] = task_id
    result["scenario"] = scenario["id"]
    result["run_index"] = run_index
    errors = validate(scenario, result)
    result["ok"] = not errors
    result["errors"] = errors
    return result


def validate(scenario: dict, result: dict) -> list[str]:
    expected = scenario.get("expected", {})
    errors = []
    if "task_status" in expected and result["task_status"] not in expected["task_status"]:
        errors.append(f"task_status={result['task_status']} expected {expected['task_status']}")
    if "worker_status" in expected and result["worker_status"] not in expected["worker_status"]:
        errors.append(f"worker_status={result['worker_status']} expected {expected['worker_status']}")
    if "min_segments" in expected and result["segments"] < expected["min_segments"]:
        errors.append(f"segments={result['segments']} < {expected['min_segments']}")
    if "approval_status" in expected and expected["approval_status"] not in result["approval_statuses"]:
        errors.append(f"approval_statuses={result['approval_statuses']} missing {expected['approval_status']}")
    if "stop_reason" in expected:
        reasons = {item[2] for item in result["segment_statuses"]}
        if expected["stop_reason"] not in reasons:
            errors.append(f"stop_reason missing {expected['stop_reason']}; got {sorted(reasons)}")
    if expected.get("stop_file") and not result["stop_file"]:
        errors.append("stop file missing")
    if expected.get("no_changed_files") and result["changed_files"]:
        errors.append(f"unexpected changed files: {result['changed_files']}")
    return errors


def _repeatable(results: list[dict]) -> dict[str, bool]:
    grouped: dict[str, list[dict]] = {}
    for item in results:
        grouped.setdefault(item["scenario"], []).append(item)
    keys = ("task_status", "worker_status", "segments", "changed_files")
    return {
        name: all(items[0][key] == item[key] for item in items for key in keys)
        for name, items in grouped.items()
    }


def write_report(root: Path, results: list[dict], scenario_ids: list[str]) -> Path:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "fake-worker-p0",
        "scenarios": scenario_ids,
        "runs": results,
        "summary": {
            "total": len(results),
            "passed": sum(1 for item in results if item["ok"]),
            "failed": sum(1 for item in results if not item["ok"]),
            "repeated_scenarios": _repeatable(results),
        },
    }
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "report.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# long-horizon P0 report", "", f"- mode: {report['mode']}", f"- scenarios: {', '.join(scenario_ids)}", ""]
    lines += ["| scenario | run | ok | task | worker | segments | duration |", "|---|---|---|---|---|---|---|"]
    for item in results:
        lines.append(
            f"| {item['scenario']} | {item['run_index']} | {'yes' if item['ok'] else 'NO'} | "
            f"{item['task_status']} | {item['worker_status']} | {item['segments']} | {item['duration_seconds']}s |"
        )
    lines += ["", "## failures"]
    failures = [item for item in results if not item["ok"]]
    if not failures:
        lines.append("none")
    for item in failures:
        lines.append(f"- {item['scenario']}#{item['run_index']}: {item['errors']}")
    md_path = root / "report.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--run-root", type=Path, default=None)
    args = parser.parse_args()
    scenarios = load_scenarios()
    if args.list:
        for scenario in scenarios.values():
            print(f"{scenario['id']}\t{scenario.get('description', '')}")
        return 0
    selected = args.scenario or list(scenarios)
    missing = [item for item in selected if item not in scenarios]
    if missing:
        print(f"unknown scenario: {', '.join(missing)}", file=sys.stderr)
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = (args.run_root or DEFAULT_ROOT) / stamp
    results = []
    for scenario_id in selected:
        for index in range(max(1, args.runs)):
            result = run_scenario(scenarios[scenario_id], run_root, index + 1)
            results.append(result)
            print(f"{scenario_id}#{index + 1}: {'PASS' if result['ok'] else 'FAIL'} "
                  f"task={result['task_status']} worker={result['worker_status']} segments={result['segments']}")
    report = write_report(run_root, results, selected)
    print(f"report: {report}")
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
