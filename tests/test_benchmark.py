import asyncio
import json
from pathlib import Path

import pytest

from app.config import Config
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from core.models import Response, ToolCall


TASK = {
    "instance_id": "example__repo-1", "repo": "example/repo",
    "base_commit": "a" * 40, "problem_statement": "Fix the addition function.",
    "patch": "PRIVATE GOLD PATCH", "test_patch": "PRIVATE TEST PATCH",
    "FAIL_TO_PASS": '["hidden_test"]', "PASS_TO_PASS": "[]",
}


class Sandbox:
    opened = []
    closed = []
    commands = []
    image_id = "sha256:fixture"

    def __init__(self, workspace, image, **kwargs):
        self.workspace = Path(workspace)
        self.image = image
        self.exports = 0

    async def open(self):
        self.opened.append(self)
        self.workspace.mkdir(parents=True)
        (self.workspace / "calc.py").write_text("def add(a,b): return a-b\n")

    async def run(self, command, cwd=".", timeout=30):
        self.commands.append(command)
        return 0, TASK["base_commit"] + "\n", False

    async def export_patch(self):
        self.exports += 1
        if self.exports == 1:
            return ""
        return "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-def add(a,b): return a-b\n+def add(a,b): return a+b\n"

    async def close(self):
        self.closed.append(self)


def test_swe_run_uses_actual_agent_and_exports_ungraded_prediction(tmp_path):
    from app.benchmark import run_swebench

    provider = FakeProvider([
        Response(tool_calls=[ToolCall("read_file", {"path": "calc.py"}, "read")]),
        Response(tool_calls=[ToolCall("edit_file", {"path": "calc.py", "old": "a-b", "new": "a+b"}, "edit")]),
        "fixed",
    ])
    report = asyncio.run(run_swebench(
        [TASK], Config(model="fake", api_key="must-not-be-saved"), tmp_path,
        fake=True, provider_factory=lambda task: provider, sandbox_factory=Sandbox,
    ))
    assert report["total"] == 1 and report["resolved"] is None
    assert report["results"][0]["status"] == "ungraded"
    assert report["metadata"]["fake"] is True
    run_dir = Path(report["run_dir"])
    prediction = json.loads((run_dir / "predictions.jsonl").read_text())
    assert prediction["instance_id"] == TASK["instance_id"]
    assert "a+b" in prediction["model_patch"]
    assert "fake" in prediction["model_name_or_path"]
    assert Sandbox.closed[-1] is Sandbox.opened[-1]
    assert "PRIVATE" not in "\n".join(m.content for messages in provider.seen for m in messages)
    assert "web_search" not in {tool["function"]["name"] for tool in provider.seen_tools[0]}
    assert "load_skill" not in {tool["function"]["name"] for tool in provider.seen_tools[0]}
    assert not (run_dir / TASK["instance_id"] / "workspace" / "test_patch").exists()
    for filename in ("report.json", "metadata.json", "audit.jsonl"):
        assert "must-not-be-saved" not in (run_dir / filename).read_text()
    assert (run_dir / TASK["instance_id"] / "messages.jsonl").exists()
    assert (run_dir / TASK["instance_id"] / "trace.jsonl").exists()


def test_environment_failure_is_recorded_without_fake_success(tmp_path):
    from app.benchmark import run_swebench

    class Broken(Sandbox):
        async def open(self):
            raise RuntimeError("Docker is unavailable")

    report = asyncio.run(run_swebench([TASK], Config(), tmp_path, fake=True, sandbox_factory=Broken))
    result = report["results"][0]
    assert result["status"] == "environment_error"
    assert "unavailable" in result["error"]
    assert report["resolved"] is None
    assert (Path(report["run_dir"]) / "predictions.jsonl").read_text() == ""


def test_mismatched_base_commit_never_runs_model(tmp_path):
    from app.benchmark import run_swebench

    class WrongBase(Sandbox):
        async def run(self, *args, **kwargs):
            return 0, "b" * 40, False

    def forbidden(task):
        pytest.fail("wrong base must fail before model call")

    report = asyncio.run(run_swebench([TASK], Config(), tmp_path, fake=True, sandbox_factory=WrongBase, provider_factory=forbidden))
    assert report["results"][0]["status"] == "environment_error"


def test_official_setup_commit_is_accepted_only_above_exact_base(tmp_path):
    from app.benchmark import run_swebench

    class OfficialSetup(Sandbox):
        async def run(self, command, **kwargs):
            return 0, ("b" * 40 if command == "git rev-parse HEAD" else TASK["base_commit"] + "\nSWE-bench"), False

    report = asyncio.run(run_swebench([TASK], Config(), tmp_path, fake=True, sandbox_factory=OfficialSetup))
    assert report["results"][0]["status"] == "ungraded"
    assert report["results"][0]["repository_head"] == "b" * 40


def test_dirty_image_never_produces_a_model_prediction(tmp_path):
    from app.benchmark import run_swebench

    class Dirty(Sandbox):
        async def export_patch(self):
            return "preexisting image changes"

    def forbidden(task):
        pytest.fail("dirty baseline must fail before model request")

    report = asyncio.run(run_swebench([TASK], Config(), tmp_path, fake=True, sandbox_factory=Dirty, provider_factory=forbidden))
    assert report["results"][0]["status"] == "environment_error"
    assert "dirty" in report["results"][0]["error"]


def test_denied_container_setup_never_starts_process(tmp_path, monkeypatch):
    from app import benchmark

    async def deny(call, preview):
        return ApprovalDecision(False, "deny")

    monkeypatch.setattr(benchmark, "auto_gate", lambda path: ApprovalGate(deny))
    before = len(Sandbox.opened)
    with pytest.raises(PermissionError):
        asyncio.run(benchmark.run_swebench([TASK], Config(), tmp_path, fake=True, sandbox_factory=Sandbox))
    assert len(Sandbox.opened) == before


def test_empty_patch_remains_unresolved_until_official_grade(tmp_path):
    from app.benchmark import run_swebench

    class Empty(Sandbox):
        async def export_patch(self):
            return ""

    report = asyncio.run(run_swebench([TASK], Config(), tmp_path, fake=True, sandbox_factory=Empty))
    assert report["results"][0]["status"] == "no_patch"
    assert report["resolved"] is None


def test_agent_error_is_preserved_when_official_grade_is_unresolved(tmp_path, monkeypatch):
    from app import benchmark
    from app.events import StreamError

    class BrokenProvider(FakeProvider):
        async def stream(self, messages, tools):
            yield StreamError("HTTP 400: reasoning_content is required")

    class Empty(Sandbox):
        async def export_patch(self):
            return ""

    report = asyncio.run(benchmark.run_swebench(
        [TASK], Config(llm_retries=0), tmp_path, fake=True,
        provider_factory=lambda task: BrokenProvider([]), sandbox_factory=Empty,
    ))
    row = report["results"][0]
    assert row["agent_status"] == "error"
    assert "reasoning_content" in row["error"]
    assert row["inference_error"] == row["error"]
    assert row["status"] == "no_patch"

    class Process:
        returncode = 0

        async def wait(self):
            return 0

    async def spawn(*args, **kwargs):
        run_id = args[args.index("--run_id") + 1]
        official = Path(kwargs["cwd"]) / f"mini-harness-fake.{run_id}.json"
        official.write_text(json.dumps({"empty_patch_ids": [TASK["instance_id"]]}))
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    graded = asyncio.run(benchmark.grade_run(Path(report["run_dir"])))
    assert graded["resolved"] == 0
    assert graded["results"][0]["status"] == "unresolved"
    assert "reasoning_content" in graded["results"][0]["error"]
    assert "agent error:" in (Path(report["run_dir"]) / "report.md").read_text()


def test_agent_error_remains_visible_when_patch_export_also_fails(tmp_path):
    from app import benchmark
    from app.events import StreamError

    class BrokenProvider(FakeProvider):
        async def stream(self, messages, tools):
            yield StreamError("HTTP 400: reasoning_content is required")

    class BrokenExport(Sandbox):
        async def export_patch(self):
            self.exports += 1
            if self.exports == 1:
                return ""
            raise RuntimeError("patch export failed")

    report = asyncio.run(benchmark.run_swebench(
        [TASK], Config(llm_retries=0), tmp_path, fake=True,
        provider_factory=lambda task: BrokenProvider([]), sandbox_factory=BrokenExport,
    ))
    row = report["results"][0]
    assert row["status"] == "environment_error"
    assert "patch export failed" in row["error"]
    assert "reasoning_content" in row["inference_error"]
    diagnostics = "\n".join(benchmark._diagnostics(report))
    assert "agent error: HTTP 400" in diagnostics
    assert "environment_error: RuntimeError: patch export failed" in diagnostics


def test_budget_stop_does_not_hide_simultaneous_model_error(tmp_path):
    from app import benchmark
    from app.events import StreamDone, StreamError

    class BrokenProvider:
        async def stream(self, messages, tools):
            yield StreamError("HTTP 400: protocol failed")
            yield StreamDone(usage={"total_tokens": 10000})

    report = asyncio.run(benchmark.run_swebench(
        [TASK], Config(max_run_tokens=10000), tmp_path, fake=True,
        provider_factory=lambda task: BrokenProvider(), sandbox_factory=Sandbox,
    ))
    row = report["results"][0]
    assert row["agent_status"] == "stopped"
    assert row["stop_reason"] == "token_budget"
    assert row["inference_error"] == "HTTP 400: protocol failed"
    assert report["execution_summary"]["runtime_errors"] == 1


@pytest.mark.parametrize("row, expected", [
    ({"agent_status": "error", "error": None, "text": "error: HTTP 400: reasoning_content is required"},
     "agent error: HTTP 400: reasoning_content is required"),
    ({"agent_status": "paused", "stop_reason": "approval_required", "text": "waiting"},
     "agent paused: approval_required"),
    ({"agent_status": "stopped", "stop_reason": "max_run_tokens", "text": "stopped"},
     "agent stopped: max_run_tokens"),
    ({"status": "environment_error", "error": "Docker unavailable"},
     "environment_error: Docker unavailable"),
    ({"status": "grading_error", "error": None}, "grading_error:"),
])
def test_cli_prints_per_instance_diagnostics_for_current_and_legacy_reports(tmp_path, monkeypatch, capsys, row, expected):
    from app import benchmark

    result = {"instance_id": TASK["instance_id"], "status": "unresolved", **row}
    report = {"run_dir": str(tmp_path), "total": 1, "resolved": 0, "results": [result]}

    async def grade(*args, **kwargs):
        return report

    monkeypatch.setattr(benchmark.Config, "from_env", lambda: Config())
    monkeypatch.setattr(benchmark, "grade_run", grade)
    expected_code = 2 if row.get("agent_status") == "error" or row.get("status") in {"environment_error", "grading_error"} else 1
    assert benchmark.main(["grade", "--run-dir", str(tmp_path)]) == expected_code
    output = capsys.readouterr().out
    assert output.startswith("resolved 0/1")
    assert f"{TASK['instance_id']}: {expected}" in output


def test_cli_prints_global_grading_failure(tmp_path, monkeypatch, capsys):
    from app import benchmark

    async def grade(*args, **kwargs):
        return {"run_dir": str(tmp_path), "total": 1, "resolved": None,
                "results": [], "grading_error": "official grader exit=1; see grader.log"}

    monkeypatch.setattr(benchmark.Config, "from_env", lambda: Config())
    monkeypatch.setattr(benchmark, "grade_run", grade)
    assert benchmark.main(["grade", "--run-dir", str(tmp_path)]) == 2
    assert "grading error: official grader exit=1; see grader.log" in capsys.readouterr().out


def test_source_fingerprint_includes_nested_provider_code(tmp_path, monkeypatch):
    from app import benchmark

    source = tmp_path / "app" / "providers" / "model.py"
    source.parent.mkdir(parents=True)
    source.write_text("version = 1\n")
    (tmp_path / "core").mkdir()
    monkeypatch.setattr(benchmark, "__file__", str(tmp_path / "app" / "benchmark.py"))
    original = benchmark._source_hash()
    source.write_text("version = 2\n")
    assert benchmark._source_hash() != original


@pytest.mark.parametrize("task", [{**TASK, "instance_id": "../escape"}, {**TASK, "base_commit": "invalid"}])
def test_invalid_task_rejected_before_artifacts(tmp_path, task):
    from app.benchmark import run_swebench

    with pytest.raises(ValueError):
        asyncio.run(run_swebench([task], Config(), tmp_path, fake=True, sandbox_factory=Sandbox))
    assert not list(tmp_path.iterdir())


def test_real_run_without_credentials_fails_before_setup(tmp_path):
    from app.benchmark import run_swebench

    with pytest.raises(ValueError, match="API"):
        asyncio.run(run_swebench([TASK], Config(api_key=""), tmp_path, sandbox_factory=Sandbox))
    assert not list(tmp_path.iterdir())


def test_grade_classification_keeps_missing_results_as_errors():
    from app.benchmark import classify_grade

    report = {"resolved_ids": ["a"], "unresolved_ids": ["b"], "error_ids": ["c"]}
    assert classify_grade(report, ["a", "b", "c", "d"]) == {
        "a": "resolved", "b": "unresolved", "c": "environment_error", "d": "grading_error",
    }
    with pytest.raises(ValueError):
        classify_grade({"resolved_ids": ["a"], "error_ids": ["a"]}, ["a"])


def test_patch_failure_does_not_erase_agent_trace_or_cleanup(tmp_path):
    from app.benchmark import run_swebench

    class BrokenPatch(Sandbox):
        async def export_patch(self):
            self.exports += 1
            if self.exports == 1:
                return ""
            raise RuntimeError("patch too large")

    report = asyncio.run(run_swebench([TASK], Config(), tmp_path, fake=True, sandbox_factory=BrokenPatch))
    assert report["results"][0]["status"] == "environment_error"
    assert "too large" in report["results"][0]["error"]
    assert Sandbox.closed[-1] is Sandbox.opened[-1]


def test_doctor_is_read_only_and_reports_missing_prerequisites(tmp_path, monkeypatch):
    from app import benchmark

    monkeypatch.setattr(benchmark, "docker_ready", lambda host: False)
    result = benchmark.doctor(Config(api_key=""), "")
    assert result["docker_ready"] is False
    assert result["api_key_configured"] is False
    assert not list(tmp_path.iterdir())


def _prior_report(root):
    root.mkdir(exist_ok=True)
    report = {"run_dir": str(root), "total": 1, "resolved": 1,
              "metadata": {"fake": True, "model": "fake"},
              "results": [{"instance_id": TASK["instance_id"], "status": "resolved"}]}
    (root / "report.json").write_text(json.dumps(report))
    (root / "dataset.json").write_text(json.dumps([TASK]))
    (root / "predictions.jsonl").write_text("{}\n")
    return report


def test_failed_regrading_invalidates_old_pass(tmp_path, monkeypatch):
    from app import benchmark

    _prior_report(tmp_path)

    class Process:
        returncode = 1

        async def wait(self):
            return 1

    async def spawn(*args, **kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    report = asyncio.run(benchmark.grade_run(tmp_path))
    assert report["resolved"] is None
    assert report["results"][0]["status"] == "grading_error"
    assert report["grading_error"]


def test_regrading_always_uses_unique_run_id_and_directory(tmp_path, monkeypatch):
    from app import benchmark

    _prior_report(tmp_path)
    seen = []

    class Process:
        returncode = 0

        async def wait(self):
            return 0

    async def spawn(*args, **kwargs):
        run_id = args[args.index("--run_id") + 1]
        directory = Path(kwargs["cwd"])
        seen.append((run_id, directory))
        assert kwargs["start_new_session"] is True
        assert "SECRET_API_KEY" not in kwargs["env"]
        (directory / f"mini-harness-fake.{run_id}.json").write_text(json.dumps({"resolved_ids": [TASK["instance_id"]]}))
        return Process()

    monkeypatch.setenv("SECRET_API_KEY", "do-not-forward")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    for _ in range(2):
        report = asyncio.run(benchmark.grade_run(tmp_path))
        assert report["resolved"] == 1
    assert seen[0][0] != seen[1][0] and seen[0][1] != seen[1][1]


def test_grade_timeout_stops_its_process_group_and_exact_containers(tmp_path, monkeypatch):
    from app import benchmark

    _prior_report(tmp_path)
    killed, calls = [], []

    class Process:
        pid = 43210
        returncode = None

        async def wait(self):
            if not killed:
                await asyncio.Event().wait()
            self.returncode = -9
            return -9

    class Cleanup:
        returncode = 0

        async def wait(self):
            return 0

    async def spawn(*args, **kwargs):
        calls.append(args)
        return Cleanup() if args[0] == "docker" else Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(benchmark.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(benchmark.grade_run(tmp_path, "unix:///test.sock", timeout=0.01))
    assert killed[0][0] == 43210
    run_id = calls[0][calls[0].index("--run_id") + 1]
    assert calls[1] == ("docker", "--host", "unix:///test.sock", "rm", "-f", f"sweb.eval.{TASK['instance_id']}.{run_id}")
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["resolved"] is None and saved["grading_error"]
