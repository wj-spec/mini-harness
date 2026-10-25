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


def test_profile_allows_network_when_opted_in(tmp_path):
    profile = sandbox.build_seatbelt_profile(tmp_path, allow_network=True)
    assert "(deny network*)" not in profile
    assert "(deny file-write*)" in profile


def test_annotate_distinguishes_denied_and_unavailable():
    profile = Path("/tmp/seatbelt.sb")
    assert sandbox.annotate_sandbox_denial("ok", None) == "ok"
    denied = sandbox.annotate_sandbox_denial("touch: Operation not permitted", profile)
    assert denied.startswith("ERROR sandbox_denied:")
    unavailable = sandbox.annotate_sandbox_denial(
        "sandbox-exec: sandbox_apply: Operation not permitted", profile)
    assert unavailable.startswith("ERROR sandbox_unavailable:")
    assert sandbox.annotate_sandbox_denial("normal failure", profile) == "normal failure"


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
