import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import __main__ as entrypoint
from app.config import Config


def test_stdio_entry_reuses_agent_without_opening_ui(tmp_path, monkeypatch):
    import app.stdio

    calls = []
    monkeypatch.setattr(app.stdio, "run_stdio", lambda agent, config, session=None, **options: calls.append((agent, config, session)))
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr(sys, "argv", ["vibe", "--stdio", "--fake"])
    entrypoint.main()

    agent, config, session = calls[0]
    assert agent.approval is not None
    assert agent.tools.root == tmp_path
    assert agent.state is agent.tools.state
    assert session.root.parent == tmp_path / ".vibe" / "sessions"


@pytest.mark.parametrize("flags", [["--eval"], ["--eval-limit", "3"], ["--eval-tasks", "tasks.json"]])
def test_stdio_rejects_unsupported_modes_before_creating_session(tmp_path, monkeypatch, capsys, flags):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr(sys, "argv", ["vibe", "--stdio", "--fake", *flags])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--stdio cannot be combined" in capsys.readouterr().err
    assert not (tmp_path / ".vibe").exists()


def test_stdio_loads_mcp_configuration_for_backend_lifecycle(tmp_path, monkeypatch):
    import app.stdio

    opened = []
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(
        workspace=tmp_path, mcp_servers='{"external":{"command":"python3"}}',
    ))
    monkeypatch.setattr(app.stdio, "run_stdio", lambda agent, config, **options: opened.append(options))
    monkeypatch.setattr(sys, "argv", ["vibe", "--stdio", "--fake"])
    entrypoint.main()
    assert opened[0]["mcp"].servers["external"].command == "python3"
    assert not opened[0]["mcp"].clients


def test_default_entry_points_to_ink(tmp_path, monkeypatch, capsys):
    import app.stdio

    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr(app.stdio, "run_stdio", lambda *args, **kwargs: pytest.fail("backend started without --stdio"))
    monkeypatch.setattr(sys, "argv", ["vibe", "--fake"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "./run_ink.sh" in capsys.readouterr().err


def test_stdio_process_does_not_import_ui_modules(tmp_path):
    project = Path(__file__).resolve().parents[1]
    script = '''
import importlib.abc
import sys
class RejectUI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"app.tui", "app.context_view"} or fullname.startswith(("app.tui.", "app.context_view.")):
            raise RuntimeError("stdio imported a retired UI module")
sys.meta_path.insert(0, RejectUI())
from app.__main__ import main
main()
'''
    env = {key: value for key, value in os.environ.items() if not key.startswith('VIBE_')}
    result = subprocess.run(
        [sys.executable, "-c", script, "--stdio", "--fake", "--workspace", str(tmp_path)],
        input='{"v":1,"type":"shutdown","id":"close"}\n',
        capture_output=True, text=True, cwd=project, env=env, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[0]["type"] == "ready"
    assert events[-1]["type"] == "closed"
