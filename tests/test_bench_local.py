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


def test_failure_class_distinguishes_incomplete_fixes():
    bench = _bench_module()
    assert bench.failure_class({"passed": True}) is None
    assert bench.failure_class({"passed": False, "agent_error": "boom"}) == "agent_error"
    assert bench.failure_class({"passed": False, "changed_files": []}) == "no_change"
    assert bench.failure_class({"passed": False, "changed_files": ["tests/test_tools.py"], "test_edits": ["tests/test_tools.py"]}) == "edited_tests"
    assert bench.failure_class({"passed": False, "changed_files": ["core/context.py"], "agent_status": "done"}) == "incomplete_fix"
