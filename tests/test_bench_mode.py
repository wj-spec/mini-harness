import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import __main__ as entrypoint
from app.config import Config
from app.oneshot import OneShotOptions, run_oneshot
from app.providers.fake import FakeProvider
from app.session import SessionStore
from app.tools import ToolRegistry
from core.models import Response, ToolCall

REPO_ROOT = Path(__file__).resolve().parents[1]


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")
    return workspace


def _write_provider() -> FakeProvider:
    return FakeProvider([
        Response(tool_calls=[ToolCall("write_file", {"path": "out.txt", "content": "hi"}, id="c1")]),
        Response(content="done"),
    ])


def test_oneshot_bench_writes_telemetry_and_approves_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_API_KEY", "sk-test")
    workspace = _workspace(tmp_path)
    runtime = tmp_path / "runtime"
    telemetry = tmp_path / "telemetry.json"
    provider = _write_provider()

    result, code = run_oneshot(
        Config(workspace=workspace, model="fake", provider="fake"),
        provider,
        OneShotOptions(
            task="create out.txt", telemetry_out=telemetry, runtime_dir=runtime, bench=True,
        ),
    )

    assert result.status == "done" and code == 0
    assert (workspace / "out.txt").read_text(encoding="utf-8") == "hi"
    assert not (workspace / ".vibe").exists()
    payload = json.loads(telemetry.read_text(encoding="utf-8"))
    assert payload["status"] == "done" and payload["bench"] is True
    assert payload["tool_calls"] == 1 and payload["usage"]["total_tokens"] > 0
    session_dir = Path(payload["session_dir"])
    assert session_dir.is_relative_to(runtime / "sessions")
    audit = (session_dir / "audit.jsonl").read_text(encoding="utf-8")
    assert '"decision": "once"' in audit
    system = provider.seen[0][0]
    assert system.role == "system" and "[BENCH MODE]" in system.content
    assert "[USER PROFILE]" not in system.content
    assert "[SKILL LIFECYCLE]" not in system.content
    assert os.environ["VIBE_API_KEY"] == "sk-test"


def test_oneshot_without_bench_fails_closed(tmp_path):
    workspace = _workspace(tmp_path)
    provider = _write_provider()

    result, code = run_oneshot(
        Config(workspace=workspace, model="fake", provider="fake"),
        provider,
        OneShotOptions(
            task="create out.txt", telemetry_out=tmp_path / "t.json",
            runtime_dir=tmp_path / "run", bench=False,
        ),
    )

    assert code == 0 and result.status == "done"
    assert not (workspace / "out.txt").exists()
    session_dir = Path(json.loads((tmp_path / "t.json").read_text())["session_dir"])
    assert "deny_no_handler" in (session_dir / "audit.jsonl").read_text(encoding="utf-8")


def test_bench_mode_relaxes_workspace_paths_only_when_enabled(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    normal = ToolRegistry(workspace)
    with pytest.raises(PermissionError):
        normal.bash(f"echo hi > {outside / 'x.txt'}")

    bench = ToolRegistry(workspace, bench_mode=True)
    output = bench.bash(f"echo hi > {outside / 'x.txt'}")
    assert "exit=0" in str(output)
    assert (outside / "x.txt").read_text(encoding="utf-8").strip() == "hi"
    assert bench.read_file(str(outside / "x.txt")).endswith("hi")


def test_session_store_accepts_runtime_sessions_root(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    root = tmp_path / "runtime" / "sessions"
    session = SessionStore.new(workspace, "fake", sessions_root=root)
    assert session.root.is_relative_to(root)
    assert not (workspace / ".vibe").exists()


def test_cli_task_mode_fake_writes_telemetry(tmp_path):
    workspace = _workspace(tmp_path)
    telemetry = tmp_path / "telemetry.json"
    runtime = tmp_path / "runtime"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.pop("VIBE_API_KEY", None)
    process = subprocess.run(
        [
            sys.executable, "-m", "app", "--fake", "--task", "say hi", "--bench",
            "--workspace", str(workspace), "--telemetry-out", str(telemetry),
            "--runtime-dir", str(runtime),
        ],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert process.returncode == 0, process.stderr
    payload = json.loads(telemetry.read_text(encoding="utf-8"))
    assert payload["status"] in {"done", "stopped"} and payload["bench"] is True
    assert not (workspace / ".vibe").exists()


def test_task_cli_rejects_conflicting_modes(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["vibe", "--task", "x", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2


def test_bench_cli_requires_task(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["vibe", "--bench"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
