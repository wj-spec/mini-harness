import sys

import pytest

from app import __main__ as entrypoint
from app import eval as evaluation
from app.config import Config


@pytest.mark.parametrize("passed,total,exit_code", [(1, 1, 0), (0, 1, 1)])
def test_eval_cli_passes_budget_and_returns_failure_code(tmp_path, monkeypatch, passed, total, exit_code):
    config = Config(workspace=tmp_path, max_run_tokens=12345, max_run_seconds=17)
    seen = []

    def run_eval(*args, **kwargs):
        seen.append(kwargs)
        return evaluation.EvalReport(
            timestamp="test", model="fake", passed=passed, total=total,
            previous_passed=None, delta=None, results=[], run_dir="result",
        )

    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: config)
    monkeypatch.setattr(evaluation, "run_eval", run_eval)
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake", "--eval", "--eval-limit", "1"])
    if exit_code:
        with pytest.raises(SystemExit) as error:
            entrypoint.main()
        assert error.value.code == exit_code
    else:
        entrypoint.main()
    assert seen[0]["config"] is config
    assert seen[0]["model"] == "fake"


@pytest.mark.parametrize(
    "status,stop_reason,error,checks,exit_code,details",
    [
        ("stopped", "no_action", None,
         [{"type": "file_equals", "ok": False, "detail": "hello.txt missing"}],
         1, ["no_action", "file_equals", "hello.txt missing"]),
        ("error", "", "HTTP 400: invalid request", [],
         2, ["HTTP 400: invalid request"]),
        ("done", "", None,
         [{"type": "file_equals", "ok": False, "detail": "actual='wrong'"}],
         1, ["file_equals", "actual='wrong'"]),
    ],
)
def test_eval_cli_explains_failed_case_and_distinguishes_runtime_errors(
    tmp_path, monkeypatch, capsys, status, stop_reason, error, checks, exit_code, details,
):
    result = evaluation.EvalResult(
        id="create_file", passed=False, status=status, steps=1, duration_ms=1,
        usage={}, cost_usd=0, checks=checks, error=error, stop_reason=stop_reason,
        text="error: HTTP 400: invalid request" if error else "I'll create the file.",
    )
    report = evaluation.EvalReport(
        timestamp="test", model="fake", passed=0, total=1,
        previous_passed=None, delta=None, results=[result], run_dir="result",
    )
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr(evaluation, "run_eval", lambda *args, **kwargs: report)
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake", "--eval", "--eval-limit", "1"])

    with pytest.raises(SystemExit) as caught:
        entrypoint.main()

    assert caught.value.code == exit_code
    output = capsys.readouterr().out
    assert "eval 0/1 -> result" in output
    assert "create_file" in output
    for detail in details:
        assert detail in output


def test_invalid_eval_limit_does_not_create_runtime_files(tmp_path, monkeypatch):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake", "--eval", "--eval-limit", "0"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert not (tmp_path / ".vibe").exists()
