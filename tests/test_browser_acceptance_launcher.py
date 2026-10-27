import contextlib
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Config
from app.agent import Agent
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry, ToolSpec
from core.models import Response, ToolCall
from scripts import browser_acceptance as launcher


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root = tmp_path / "project with spaces"
    (root / "examples").mkdir(parents=True)
    (root / "examples/browser-actions-mcp.json").write_text(json.dumps({"mcpServers": {"browser": {
        "command": "npx", "args": ["--offline"], "cwd": ".", "env": {},
        "browser_actions": True, "allowed_origins": ["http://localhost:3000"],
    }}}))
    monkeypatch.setattr(launcher, "ROOT", root)
    monkeypatch.setattr(launcher.Config, "from_env", lambda: Config(api_key="test-key", model="test-model"))
    monkeypatch.setenv("VIBE_API_KEY", "test-key")
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    state = {"submissions": 0}

    @contextlib.contextmanager
    def fixture():
        yield "http://127.0.0.1:45678", state

    monkeypatch.setattr(launcher, "fixture_server", fixture)
    evidence = []

    def collect(workspace, before_ids, **kwargs):
        evidence.append((workspace, before_ids, kwargs))
        return {"passed": True, "status": "passed", "real_model": "requires_user_confirmation",
                "user_experience": "requires_user_confirmation"}

    monkeypatch.setitem(sys.modules, "scripts.browser_acceptance_evidence", SimpleNamespace(collect_report=collect))
    return root, state, evidence


def test_missing_key_does_not_start_fixture_or_ink(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher.Config, "from_env", lambda: Config(api_key=""))
    monkeypatch.setattr(launcher, "fixture_server", lambda: pytest.fail("must not open fixture"))
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not launch Ink"))

    assert launcher.main([]) == 2
    assert "key" in capsys.readouterr().err.lower()
    assert not (tmp_path / ".vibe").exists()


def test_launcher_isolates_workspace_and_inherits_key_without_recording_it(prepared, monkeypatch, capsys):
    root, state, evidence = prepared
    calls = []
    monkeypatch.setenv("VIBE_MAX_RUN_TOKENS", "999999")
    monkeypatch.setenv("VIBE_MAX_RUN_SECONDS", "999999")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        workspace = Path(argv[argv.index("--workspace") + 1])
        (workspace / ".vibe/sessions/new-session").mkdir(parents=True)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launcher.subprocess, "run", run)

    assert launcher.main([]) == 0
    argv, options = calls[0]
    assert argv[0] == str(root / "run_ink.sh")
    assert "--test-session" in argv and argv[argv.index("--max-steps") + 1] == "12"
    assert options["cwd"] == root and options["env"]["VIBE_API_KEY"] == "test-key"
    assert options["env"]["VIBE_MAX_RUN_TOKENS"] == "100000"
    assert options["env"]["VIBE_MAX_RUN_SECONDS"] == "180"
    config_path = Path(options["env"]["VIBE_MCP_CONFIG"])
    config = json.loads(config_path.read_text())["mcpServers"]["browser"]
    assert config["allowed_origins"] == ["http://127.0.0.1:45678"]
    assert config["cwd"] == str(root)
    assert "VIBE_BROWSER_NO_SANDBOX" not in config["env"]
    workspace, before_ids, facts = evidence[0]
    assert workspace == config_path.parent / "workspace" and before_ids == set()
    assert (workspace / ".vibe/sessions/new-session").exists()
    assert facts == {"origin": "http://127.0.0.1:45678", "submissions": 0, "exit_code": 0}
    report_text = (config_path.parent / "report.json").read_text()
    assert "test-key" not in report_text and "test-key" not in capsys.readouterr().out
    assert "B2 验收草稿" in (config_path.parent / "prompts.txt").read_text()
    report = json.loads(report_text)
    assert report["real_model"] == report["user_experience"] == "requires_user_confirmation"


def test_explicit_no_sandbox_changes_only_browser_configuration(prepared, monkeypatch):
    root, _, _ = prepared
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    assert launcher.main(["--no-sandbox"]) == 0
    path = next((root / ".vibe/browser/acceptance").glob("*/mcp.json"))
    assert json.loads(path.read_text())["mcpServers"]["browser"]["env"]["VIBE_BROWSER_NO_SANDBOX"] == "1"


def test_serve_only_collects_report_after_ctrl_c_without_key_or_ink(prepared, monkeypatch, capsys):
    _, _, evidence = prepared
    monkeypatch.setattr(launcher.Config, "from_env", lambda: Config(api_key=""))
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: pytest.fail("serve-only must not launch Ink"))
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(KeyboardInterrupt()))

    assert launcher.main(["--serve-only"]) == 0
    assert evidence[0][2]["exit_code"] is None
    output = capsys.readouterr().out
    assert "VIBE_MCP_CONFIG=" in output and "--workspace" in output and "--test-session" in output


def test_incomplete_evidence_returns_failure_and_keeps_report(prepared, monkeypatch):
    root, _, _ = prepared
    monkeypatch.setitem(sys.modules, "scripts.browser_acceptance_evidence", SimpleNamespace(
        collect_report=lambda *args, **kwargs: {"passed": False, "status": "incomplete"},
    ))
    monkeypatch.setattr(launcher.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    assert launcher.main([]) == 1
    path = next((root / ".vibe/browser/acceptance").glob("*/report.json"))
    assert json.loads(path.read_text())["status"] == "incomplete"


def test_module_entry_help_works_without_pythonpath():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "scripts.browser_acceptance", "--help"],
        cwd=root, env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--serve-only" in result.stdout and "--no-sandbox" in result.stdout


def test_acceptance_budget_fits_observed_real_model_tool_chain(tmp_path):
    # 真实 055548 场次普通请求用量；排除因 30k 上限额外产生的收尾请求。
    usage = [10201, 11464, 13225, 14524, 11824, 13244]
    calls = []
    registry = ToolRegistry(tmp_path)
    registry.register(ToolSpec("observe_fixture", "Read fixture state", {
        "type": "object", "properties": {"step": {"type": "integer"}}, "required": ["step"],
    }, lambda step: calls.append(step) or "fixture observed"))
    responses = [Response(tool_calls=[ToolCall("observe_fixture", {"step": step}, f"c{step}")],
                          usage={"total_tokens": tokens})
                 for step, tokens in enumerate(usage[:-1])]
    responses.append(Response(content="completed", usage={"total_tokens": usage[-1]}))
    result = asyncio.run(Agent(FakeProvider(responses), registry,
                               max_run_tokens=int(launcher.LIMITS["VIBE_MAX_RUN_TOKENS"]),
                               max_steps=12).run("Observe the local fixture and report results"))
    assert result.status == "done", result.stop_reason
    assert calls == [0, 1, 2, 3, 4]
    assert result.usage["total_tokens"] == 74482
