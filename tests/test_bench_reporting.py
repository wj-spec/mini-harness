import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_trial(job: Path, name: str, *, resolved: bool, telemetry: dict | None = None):
    trial = job / name
    (trial / "agent").mkdir(parents=True)
    (trial / "verifier").mkdir()
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": name.split("__")[0] + "__" + name.split("__")[1],
                "trial_name": name,
                "verifier_result": {"rewards": {"reward": 1.0 if resolved else 0.0}},
                "agent_result": {"n_input_tokens": 10, "n_output_tokens": 2},
                "exception_info": None,
                "agent_setup": {"started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:05Z"},
                "agent_execution": {"started_at": "2026-01-01T00:00:05Z", "finished_at": "2026-01-01T00:00:10Z"},
                "verifier": {"started_at": "2026-01-01T00:00:10Z", "finished_at": "2026-01-01T00:00:20Z"},
            }
        ),
        encoding="utf-8",
    )
    (trial / "agent" / "telemetry.json").write_text(
        json.dumps(telemetry or {"status": "done", "steps": 3, "tool_calls": 4, "usage": {"total_tokens": 12}}),
        encoding="utf-8",
    )
    (trial / "verifier" / "report.json").write_text(
        json.dumps(
            {
                "x": {
                    "resolved": resolved,
                    "tests_status": {
                        "FAIL_TO_PASS": {"success": [], "failure": [] if resolved else ["test_bad"]},
                        "PASS_TO_PASS": {"success": [], "failure": []},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (trial / "agent" / "model.patch").write_text("diff --git a/x b/x\n", encoding="utf-8")
    return trial


def test_bench_report_collects_rows(tmp_path):
    module = _load("bench_report", "scripts/bench_report.py")
    job = tmp_path / "job"
    job.mkdir()
    _make_trial(job, "astropy__astropy-7606__aaa", resolved=True)
    _make_trial(job, "django__django-10097__bbb", resolved=False)

    rows = module.collect_trials(job)
    assert len(rows) == 2
    by_instance = {row["instance"]: row for row in rows}
    assert by_instance["astropy__astropy-7606"]["resolved"] is True
    assert by_instance["django__django-10097"]["failed_tests"] == ["test_bad"]
    assert by_instance["astropy__astropy-7606"]["agent_setup_sec"] == 5.0
    assert by_instance["astropy__astropy-7606"]["patch_bytes"] > 0


def test_collect_predictions_writes_jsonl(tmp_path):
    module = _load("collect_predictions", "scripts/collect_predictions.py")
    job = tmp_path / "job"
    job.mkdir()
    _make_trial(job, "astropy__astropy-7606__aaa", resolved=True)
    rows = module.collect(job)
    assert len(rows) == 1
    assert rows[0]["instance_id"] == "astropy__astropy-7606"
    assert rows[0]["model_patch"].startswith("diff --git")


def test_adapter_saves_model_patch():
    source = (REPO_ROOT / "bench" / "adapter.py").read_text(encoding="utf-8")
    assert "model.patch" in source
    assert "git-status.txt" in source


def test_strip_test_file_hunks():
    module = _load("collect_predictions", "scripts/collect_predictions.py")
    patch = (
        "diff --git a/src/app.py b/src/app.py\n"
        "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/tests/test_app.py b/tests/test_app.py\n"
        "--- a/tests/test_app.py\n+++ b/tests/test_app.py\n@@ -1 +1 @@\n-old\n+new\n"
    )
    cleaned, removed = module.strip_test_file_hunks(patch)
    assert removed == 1
    assert "src/app.py" in cleaned
    assert "tests/test_app.py" not in cleaned
