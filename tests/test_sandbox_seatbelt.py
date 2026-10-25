import sys
from pathlib import Path

import pytest

from app import sandbox
from app.config import Config
from app.tools import ToolRegistry
from core.models import ToolCall

needs_seatbelt = pytest.mark.skipif(not sandbox.sandbox_available(), reason="sandbox-exec unavailable")
probe_ok, probe_detail = sandbox.probe_sandbox()
needs_applicable = pytest.mark.skipif(
    not probe_ok, reason=f"nested sandbox unavailable: {probe_detail[:120]}"
)


def test_profile_restricts_writes_and_network(tmp_path):
    profile = sandbox.build_seatbelt_profile(tmp_path)
    resolved = str(tmp_path.resolve())
    assert "(deny network*)" in profile
    assert "(deny file-write*)" in profile
    assert f'(allow file-write* (subpath "{resolved}"))' in profile
    assert f'(deny file-read* (subpath "{Path.home() / ".ssh"}"))' in profile
    assert '"/dev/null"' in profile
    assert f'(deny file-write* (subpath "{tmp_path.resolve() / ".git" / "hooks"}"))' in profile
    assert f'(deny file-write* (literal "{tmp_path.resolve() / ".git" / "config"}"))' in profile


def test_profile_allows_network_when_opted_in(tmp_path):
    profile = sandbox.build_seatbelt_profile(tmp_path, allow_network=True)
    assert "(deny network*)" not in profile
    assert "(deny file-write*)" in profile


def test_annotate_distinguishes_denied_and_unavailable():
    spec = sandbox.SandboxSpec(kind="seatbelt", workspace=Path("/tmp/ws"), profile=Path("/tmp/seatbelt.sb"))
    assert sandbox.annotate_sandbox_denial("ok", None) == "ok"
    denied = sandbox.annotate_sandbox_denial("touch: Operation not permitted", spec)
    assert denied.startswith("ERROR sandbox_denied:")
    unavailable = sandbox.annotate_sandbox_denial(
        "sandbox-exec: sandbox_apply: Operation not permitted", spec)
    assert unavailable.startswith("ERROR sandbox_unavailable:")
    assert sandbox.annotate_sandbox_denial("normal failure", spec) == "normal failure"


def test_config_reads_sandbox_env(monkeypatch):
    monkeypatch.setenv("VIBE_SANDBOX", "1")
    monkeypatch.delenv("VIBE_SANDBOX_NETWORK", raising=False)
    config = Config.from_env()
    assert config.sandbox is True and config.sandbox_network is False
    monkeypatch.setenv("VIBE_SANDBOX_NETWORK", "yes")
    assert Config.from_env().sandbox_network is True
    monkeypatch.setenv("VIBE_SANDBOX", "0")
    assert Config.from_env().sandbox is False


def test_registry_rejects_sandbox_when_binary_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("app.tools.sandbox_available", lambda: False)
    with pytest.raises(ValueError, match="sandbox requested"):
        ToolRegistry(tmp_path, sandbox=True)


@needs_seatbelt
def test_registry_reports_unusable_sandbox(tmp_path):
    if probe_ok:
        pytest.skip("sandbox is applicable here")
    with pytest.raises(ValueError, match="sandbox cannot be applied here"):
        ToolRegistry(tmp_path, sandbox=True)


@needs_applicable
def test_sandbox_allows_workspace_writes_and_denies_outside(tmp_path):
    registry = ToolRegistry(tmp_path, sandbox=True)
    ok, output = registry.execute(ToolCall("bash", {
        "command": "python3 -c \"open('inside.txt','w').write('x')\"",
    }))
    assert ok is True, output
    assert (tmp_path / "inside.txt").read_text() == "x"

    ok, output = registry.execute(ToolCall("bash", {
        "command": "python3 -c \"open('$HOME/mini-harness-sandbox-probe','w').write('x')\"",
    }))
    assert ok is False
    assert "sandbox_denied" in output
    assert not (Path.home() / "mini-harness-sandbox-probe").exists()


@needs_applicable
def test_sandbox_denies_network_by_default(tmp_path):
    registry = ToolRegistry(tmp_path, sandbox=True)
    ok, output = registry.execute(ToolCall("bash", {
        "command": "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), 1)\"",
    }))
    assert ok is False
    assert "sandbox_denied" in output or "Operation not permitted" in output


@needs_applicable
def test_sandbox_keeps_toolchain_working(tmp_path):
    registry = ToolRegistry(tmp_path, sandbox=True)
    for command in ("python3 -c 'print(1+1)'", "git --version", "node --version"):
        ok, output = registry.execute(ToolCall("bash", {"command": command}))
        assert ok is True, f"{command}: {output}"


@needs_applicable
def test_git_commit_works_but_hook_writes_are_denied(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, timeout=30)
    (tmp_path / "a.txt").write_text("x")
    registry = ToolRegistry(tmp_path, sandbox=True)
    ok, output = registry.execute(ToolCall("bash", {"command": "git add a.txt && git commit -qm t"}))
    assert ok is True, output
    hook = tmp_path / ".git" / "hooks" / "pre-commit"
    ok, output = registry.execute(ToolCall("bash", {
        "command": "python3 -c \"open('.git/hooks/pre-commit','w').write('#!/bin/sh')\"",
    }))
    assert ok is False
    assert "sandbox_denied" in output
    assert not hook.exists()


def test_bwrap_command_binds_workspace_readonly_root_and_blocks_network(tmp_path):
    argv = sandbox.build_bwrap_command("echo hi", tmp_path, allow_network=False)
    ws = str(tmp_path.resolve())
    assert argv[0] == sandbox.BWRAP_BINARY
    assert ["--ro-bind", "/", "/"] == argv[argv.index("--ro-bind"):argv.index("--ro-bind") + 3]
    assert ["--bind", ws, ws] == argv[argv.index("--bind"):argv.index("--bind") + 3]
    assert "--unshare-net" in argv
    assert argv[-4:] == ["--", "/bin/bash", "-lc", "echo hi"]
    allowed = sandbox.build_bwrap_command("echo hi", tmp_path, allow_network=True)
    assert "--unshare-net" not in allowed


def test_sandbox_wrapped_uses_the_backend_of_the_spec(tmp_path):
    seatbelt = sandbox.SandboxSpec(kind="seatbelt", workspace=tmp_path, profile=Path("/tmp/x.sb"))
    assert sandbox.sandbox_wrapped("echo", seatbelt)[:3] == [sandbox.SEATBELT_BINARY, "-f", "/tmp/x.sb"]
    bwrap = sandbox.SandboxSpec(kind="bubblewrap", workspace=tmp_path)
    assert sandbox.sandbox_wrapped("echo", bwrap)[0] == sandbox.BWRAP_BINARY
    assert sandbox.sandbox_wrapped("echo", None) == ["/bin/bash", "-lc", "echo"]

