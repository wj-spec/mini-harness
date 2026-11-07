from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bench_long.py"


def _load():
    spec = importlib.util.spec_from_file_location("bench_long", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_bench_long_scenarios_are_well_formed():
    bench = _load()
    scenarios = bench.load_scenarios()
    assert {"detach-continue", "approval-timeout", "kill-switch", "restart-attach"} <= set(scenarios)
    for scenario in scenarios.values():
        assert scenario["id"] == Path(scenario["_path"]).stem
        assert scenario["action"] in {"wait", "attach", "stop", "approval_timeout", "crash", "two_slot", "criteria"}
        assert isinstance(scenario["expected"], dict)


def test_bench_long_validation_rejects_mismatch():
    bench = _load()
    scenario = {"id": "x", "expected": {"task_status": ["completed"], "worker_status": ["exited"]}}
    result = {"task_status": "failed", "worker_status": "killed", "segments": 1,
              "approval_statuses": [], "segment_statuses": [], "stop_file": False, "changed_files": []}
    errors = bench.validate(scenario, result)
    assert any("task_status" in error for error in errors)
    assert any("worker_status" in error for error in errors)


def test_bench_long_list_works():
    proc = subprocess.run([sys.executable, str(SCRIPT), "--list"], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "detach-continue" in proc.stdout
    assert "approval-timeout" in proc.stdout


def test_bench_long_report_marks_repeatability(tmp_path):
    bench = _load()
    results = [
        {"scenario": "a", "run_index": 1, "ok": True, "task_status": "completed", "worker_status": "exited",
         "segments": 1, "changed_files": [], "duration_seconds": 1},
        {"scenario": "a", "run_index": 2, "ok": True, "task_status": "completed", "worker_status": "exited",
         "segments": 1, "changed_files": [], "duration_seconds": 1},
    ]
    path = bench.write_report(tmp_path, results, ["a"])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["summary"]["passed"] == 2
    assert data["summary"]["repeated_scenarios"]["a"] is True


def test_bench_long_report_includes_scenario_stats(tmp_path):
    bench = _load()
    results = [
        {"scenario": "a", "run_index": 1, "ok": True, "task_status": "completed", "worker_status": "exited",
         "segments": 1, "changed_files": [], "duration_seconds": 2},
        {"scenario": "a", "run_index": 2, "ok": False, "task_status": "failed", "worker_status": "exited",
         "segments": 1, "changed_files": [], "duration_seconds": 4, "errors": ["forced"]},
        {"scenario": "b", "run_index": 1, "ok": True, "task_status": "completed", "worker_status": "exited",
         "segments": 2, "changed_files": [], "duration_seconds": 6},
    ]
    path = bench.write_report(tmp_path, results, ["a", "b"])
    data = json.loads(path.read_text(encoding="utf-8"))
    stats = data["summary"]["scenario_stats"]
    assert stats["a"]["runs"] == 2 and stats["a"]["passed"] == 1 and stats["a"]["failed"] == 1
    assert stats["a"]["pass_rate"] == 0.5
    assert stats["a"]["avg_duration_seconds"] == 3.0
    assert stats["a"]["max_duration_seconds"] == 4.0
    assert stats["b"]["pass_rate"] == 1.0
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## scenario stats" in md
    assert "| a | 2 | 1 | 1 | 50% | 3.0s | 4.0s |" in md
