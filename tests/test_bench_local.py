import importlib.util
from pathlib import Path


def _bench_module():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("bench_local", root / "scripts" / "bench_local.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_workspace_excludes_benchmark_fixtures(tmp_path):
    bench = _bench_module()
    workspace = tmp_path / "workspace"
    bench.build_workspace(workspace)

    assert (workspace / "core" / "context.py").is_file()
    assert (workspace / ".git").is_dir()
    assert not workspace.is_relative_to(Path(__file__).resolve().parents[1])
    for relative in ("benchmarks", "scripts/bench_local.py", "docs/specs/local-coding-benchmark.md",
                     "docs/MILESTONES.md", "tests/test_bench_local.py"):
        assert not (workspace / relative).exists(), relative


def test_outside_workspace_commands_are_flagged(tmp_path):
    bench = _bench_module()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    commands = ["pwd", "npm test", "cd /Users/endless/Desktop/self_project/mini-harness && npm test"]
    assert bench.outside_workspace_commands(workspace, commands) == [commands[-1]]


def test_failure_class_distinguishes_incomplete_fixes():
    bench = _bench_module()
    assert bench.failure_class({"passed": True}) is None
    assert bench.failure_class({"passed": False, "agent_error": "boom"}) == "agent_error"
    assert bench.failure_class({"passed": False, "changed_files": []}) == "no_change"
    assert bench.failure_class({"passed": False, "changed_files": ["tests/test_tools.py"], "test_edits": ["tests/test_tools.py"]}) == "edited_tests"
    assert bench.failure_class({"passed": False, "changed_files": ["core/context.py"], "agent_status": "done"}) == "incomplete_fix"


def test_result_categories_separate_three_kinds():
    bench = _bench_module()
    good = {"red": {"exit_code": 1}, "check": {"exit_code": 0}, "agent_status": "done",
            "changed_files": ["a.txt"], "workspace_clean": True}
    cats = bench.result_categories(good)
    assert cats["artifact_correct"] is True
    assert cats["run_complete"] is True
    assert cats["verified_fix"] is True
    assert cats["comparable"] is True and cats["valid"] is True

    tampered = dict(good, test_edits=["tests/test_x.py"])
    cats2 = bench.result_categories(tampered)
    assert cats2["artifact_correct"] is True
    assert cats2["valid"] is False and "edited_tests" in cats2["invalid_reasons"]
    assert cats2["comparable"] is False

    trivial = {"red": {"exit_code": 0}, "check": {"exit_code": 0}, "agent_status": "done",
               "workspace_clean": True}
    cats3 = bench.result_categories(trivial)
    assert cats3["artifact_correct"] is True
    assert cats3["verified_fix"] is False and cats3["comparable"] is False

    cancelled = dict(good, agent_status="cancelled")
    cats4 = bench.result_categories(cancelled)
    assert cats4["run_complete"] is False
    assert cats4["valid"] is False and "cancelled" in cats4["invalid_reasons"]

    leaky = dict(good, outside_workspace_commands=["cat /Users/other/x"], hidden_leak_attempts=["benchmarks/local"])
    cats5 = bench.result_categories(leaky)
    assert cats5["valid"] is False and cats5["comparable"] is False
    assert "outside_workspace_commands" in cats5["invalid_reasons"]


def test_run_config_records_model_budget_and_version():
    bench = _bench_module()
    config = bench.run_config(fake=True)
    assert set(config) == {"model", "budget", "version", "fake"}
    assert config["fake"] is True

