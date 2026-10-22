import json

import pytest

from app import benchmark


def test_doctor_exposes_configured_budget_without_overriding_it(monkeypatch, capsys):
    monkeypatch.setenv("VIBE_MAX_RUN_TOKENS", "42000")
    monkeypatch.setenv("VIBE_MAX_STEPS", "17")
    monkeypatch.setenv("VIBE_MAX_RUN_SECONDS", "91")
    monkeypatch.setenv("VIBE_COMPACT_INTERVAL_TOKENS", "12000")
    monkeypatch.setenv("VIBE_CONTEXT_WINDOW", "16384")
    monkeypatch.setattr(benchmark, "docker_ready", lambda host: False)

    assert benchmark.main(["doctor"]) == 0

    budget = json.loads(capsys.readouterr().out)["budget"]
    assert budget["max_run_tokens"] == 42000
    assert budget["max_steps"] == 17
    assert budget["max_run_seconds"] == 91
    assert budget["compact_interval_tokens"] == 12000
    assert budget["context_window"] == 16384


def test_explicit_token_override_preserves_other_configured_limits(monkeypatch, capsys):
    monkeypatch.setenv("VIBE_MAX_RUN_TOKENS", "42000")
    monkeypatch.setenv("VIBE_MAX_STEPS", "17")
    monkeypatch.setenv("VIBE_MAX_RUN_SECONDS", "91")
    monkeypatch.setattr(benchmark, "docker_ready", lambda host: False)

    assert benchmark.main(["doctor", "--max-tokens", "300000"]) == 0
    budget = json.loads(capsys.readouterr().out)["budget"]
    assert budget["max_run_tokens"] == 300000
    assert budget["max_steps"] == 17
    assert budget["max_run_seconds"] == 91


def test_zero_steps_and_tokens_keep_existing_unlimited_semantics(monkeypatch, capsys):
    monkeypatch.setenv("VIBE_MAX_STEPS", "0")
    monkeypatch.setenv("VIBE_MAX_RUN_TOKENS", "0")
    monkeypatch.setattr(benchmark, "docker_ready", lambda host: False)

    assert benchmark.main(["doctor"]) == 0
    budget = json.loads(capsys.readouterr().out)["budget"]
    assert budget["max_steps"] == budget["max_run_tokens"] == 0


@pytest.mark.parametrize("row,code,label", [
    ({"status": "unresolved", "agent_status": "stopped", "stop_reason": "token_budget"},
     1, "budget paused: token_budget"),
    ({"status": "unresolved", "agent_status": "error", "text": "error: HTTP 400"},
     2, "agent error: HTTP 400"),
    ({"status": "environment_error", "error": "Docker unavailable"},
     2, "environment_error: Docker unavailable"),
])
def test_cli_distinguishes_budget_pause_from_runtime_error(tmp_path, monkeypatch, capsys, row, code, label):
    async def grade(*args, **kwargs):
        return {"run_dir": str(tmp_path), "resolved": 0, "total": 1,
                "results": [{"instance_id": "case", **row}]}

    monkeypatch.setattr(benchmark, "grade_run", grade)
    assert benchmark.main(["grade", "--run-dir", str(tmp_path)]) == code
    output = capsys.readouterr().out
    assert "resolved 0/1" in output
    assert label in output
    assert "budget paused 1" in output if code == 1 else "runtime errors 1" in output


def test_check_budget_cli_runs_context_layer_without_model_config_or_docker(tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline budget checks must not load model config or Docker")

    monkeypatch.setattr(benchmark.Config, "from_env", forbidden)
    monkeypatch.setattr(benchmark, "docker_ready", forbidden)

    assert benchmark.main(["check-budget", "--level", "context", "--root", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert "budget mechanisms 1/1" in output
    assert "[PASS] context" in output
    report = json.loads(next((tmp_path / "budget-checks").glob("*/report.json")).read_text())
    assert report["fake"] is True
    assert report["results"][0]["agent_status"] == "stopped"


@pytest.mark.parametrize("options,count", [([], 1), (["--limit", "3"], 3)])
def test_public_evaluation_starts_with_one_task_unless_explicitly_expanded(tmp_path, monkeypatch, capsys, options, count):
    tasks = [{"instance_id": f"example__repo-{index}", "repo": "example/repo", "base_commit": "a" * 40}
             for index in range(3)]
    monkeypatch.setattr(benchmark, "prepare_dataset", lambda *args: tasks)

    assert benchmark.main(["prepare", "--root", str(tmp_path), *options]) == 0

    report = json.loads(capsys.readouterr().out)
    assert len(report["instances"]) == count


def test_invalid_environment_budget_is_reported_without_traceback(monkeypatch, capsys):
    monkeypatch.setenv("VIBE_MAX_RUN_TOKENS", "invalid")

    assert benchmark.main(["doctor"]) == 2
    error = capsys.readouterr().err
    assert "benchmark error:" in error
    assert "Traceback" not in error


@pytest.mark.parametrize("options", [
    ["--max-steps", "-1"], ["--max-tokens", "-1"], ["--max-seconds", "0"],
    ["--max-seconds", "nan"], ["--max-seconds", "inf"],
])
def test_invalid_explicit_budget_is_rejected_before_runtime_files(tmp_path, options):
    with pytest.raises(SystemExit) as error:
        benchmark.main(["prepare", "--root", str(tmp_path), *options])
    assert error.value.code == 2
    assert list(tmp_path.iterdir()) == []


def test_validate_grader_keeps_runtime_failure_distinct_from_failed_reference(tmp_path, monkeypatch):
    from app.config import Config
    import asyncio

    async def grade(root, docker_host):
        return {"run_dir": str(root), "total": 1, "resolved": None,
                "grading_error": "fixture grader failed", "results": []}

    monkeypatch.setattr(benchmark, "grade_run", grade)
    task = {"instance_id": "example__repo-1", "patch": "reference fixture"}
    assert asyncio.run(benchmark.validate_grader([task], Config(), tmp_path, "")) == 2
