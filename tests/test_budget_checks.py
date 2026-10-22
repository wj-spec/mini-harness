import asyncio
import json
from pathlib import Path

import pytest

from app.budget_checks import run_budget_checks


def test_context_layer_accepts_expected_stop_without_model_request(tmp_path):
    report = asyncio.run(run_budget_checks(tmp_path, "context"))

    assert report["fake"] is True
    assert report["passed"] == report["total"] == 1
    result = report["results"][0]
    assert result["status"] == "passed" and all(result["checks"].values())
    assert result["agent_status"] == "stopped"
    assert result["stop_reason"] == "context_window"
    assert result["evidence"]["requests"] == 0
    assert result["usage"]["total_tokens"] == 0
    saved = json.loads(Path(report["run_dir"], "report.json").read_text())
    assert saved == report
    assert Path(report["run_dir"]).is_relative_to(tmp_path / "budget-checks")


def test_invalid_level_leaves_no_artifacts(tmp_path):
    with pytest.raises(ValueError, match="level"):
        asyncio.run(run_budget_checks(tmp_path, "unknown"))
    assert list(tmp_path.iterdir()) == []


def test_compaction_layer_repairs_and_really_verifies_in_one_run(tmp_path):
    report = asyncio.run(run_budget_checks(tmp_path, "compaction"))

    assert report["passed"] == 1
    result = report["results"][0]
    assert result["agent_status"] == "done" and result["stop_reason"] == ""
    assert result["status"] == "passed" and all(result["checks"].values())
    evidence = result["evidence"]
    assert evidence["requests"] == 4 and evidence["compactions"] == 2
    assert evidence["offload_refs"]
    assert evidence["command_results"][-1]["verification"] is True
    assert evidence["command_results"][-1]["exit_code"] == 0
    assert "checks passed" in evidence["command_results"][-1]["output"]
    assert result["usage"]["total_tokens"] == 16400
    traces = [json.loads(line) for line in Path(evidence["trace"]).read_text().splitlines()]
    assert len({event["run_id"] for event in traces}) == 1
    audit = [json.loads(line) for line in Path(report["run_dir"], "audit.jsonl").read_text().splitlines()]
    assert all(event["decision"] == "once" for event in audit)
    assert any(event["tool"] == "edit_file" for event in audit)
    assert any(event["tool"] == "bash" for event in audit)


def test_total_layer_accepts_exhaustion_and_blocks_write(tmp_path):
    report = asyncio.run(run_budget_checks(tmp_path, "total"))

    assert report["passed"] == 1
    result = report["results"][0]
    assert result["agent_status"] == "stopped" and result["stop_reason"] == "token_budget"
    assert result["status"] == "passed" and result["error"] is None
    assert all(result["checks"].values())
    assert not Path(result["evidence"]["workspace"], "never.txt").exists()
    assert not Path(result["evidence"]["workspace"], "never-command.txt").exists()
    assert result["usage"]["total_tokens"] == result["budget"]["max_run_tokens"] == 5000


def test_all_levels_are_deterministic_and_do_not_read_model_configuration(tmp_path, monkeypatch):
    from app.config import Config

    def unexpected_config(*args, **kwargs):
        raise AssertionError("offline check must not load model configuration")

    monkeypatch.setattr(Config, "from_env", unexpected_config)
    report = asyncio.run(run_budget_checks(tmp_path))

    assert report["total"] == report["passed"] == 3
    assert [result["level"] for result in report["results"]] == ["context", "compaction", "total"]


def test_fixture_approval_denial_is_error_and_leaves_fixture_unwritten(tmp_path, monkeypatch):
    import app.budget_checks as checks
    from app.policy import ApprovalDecision

    async def deny_fixture(call, preview):
        return ApprovalDecision(not call.args.get("path", "").endswith("calc.py"), "once")

    monkeypatch.setattr(checks, "_allow_check", deny_fixture)
    report = asyncio.run(run_budget_checks(tmp_path, "compaction"))

    result = report["results"][0]
    assert report["passed"] == 0 and result["status"] == "error"
    assert "PermissionError" in result["error"]
    assert not Path(report["run_dir"], "compaction", "calc.py").exists()
    audit = [json.loads(line) for line in Path(report["run_dir"], "audit.jsonl").read_text().splitlines()]
    assert any(event["decision"] == "deny" for event in audit)


def test_false_tool_success_cannot_hide_real_verification_failure(tmp_path, monkeypatch):
    from app.tools import ToolRegistry

    monkeypatch.setattr(ToolRegistry, "edit_file", lambda self, **kwargs: "edited")
    report = asyncio.run(run_budget_checks(tmp_path, "compaction"))

    result = report["results"][0]
    assert report["passed"] == 0 and result["status"] == "error"
    assert result["checks"]["verification_succeeded"] is False
    assert result["checks"]["code_repaired"] is False
    assert result["evidence"]["command_results"][-1]["exit_code"] != 0
    assert "bash" in result["error"]


def test_layer_exception_is_recorded_as_error(tmp_path, monkeypatch):
    from app.agent import Agent

    async def broken(*args, **kwargs):
        raise RuntimeError("unexpected agent failure")

    monkeypatch.setattr(Agent, "run", broken)
    report = asyncio.run(run_budget_checks(tmp_path, "total"))

    assert report["passed"] == 0
    assert report["results"][0]["status"] == "error"
    assert "unexpected agent failure" in report["results"][0]["error"]


def test_all_stops_progressing_after_failed_layer(tmp_path, monkeypatch):
    import app.budget_checks as checks

    async def unexpected(*args):
        raise RuntimeError("layer broken")

    monkeypatch.setattr(checks, "_run_layer", unexpected)
    report = asyncio.run(run_budget_checks(tmp_path))

    assert report["total"] == 3 and report["passed"] == 0
    assert [result["status"] for result in report["results"]] == ["error", "skipped", "skipped"]
    assert all(result["stop_reason"] == "previous_layer_failed" for result in report["results"][1:])


def test_budget_stop_with_real_error_cannot_pass(tmp_path, monkeypatch):
    from app.agent import Agent, AgentResult

    async def stopped_with_error(*args, **kwargs):
        result = AgentResult("stopped", 1, "stopped", {"total_tokens": 5000}, stop_reason="token_budget")
        result.error = "HTTP 400"
        return result

    monkeypatch.setattr(Agent, "run", stopped_with_error)
    report = asyncio.run(run_budget_checks(tmp_path, "total"))

    assert report["passed"] == 0
    assert report["results"][0]["status"] == "error"
    assert report["results"][0]["error"] == "HTTP 400"
