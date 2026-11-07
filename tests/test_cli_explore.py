"""`--explore` 只读子 Agent CLI 测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import __main__ as entrypoint
from app.config import Config


def _fake_result(status="ok", tokens=15):
    return SimpleNamespace(
        status=status, summary="摘要", task_id="t-sub-1",
        usage={"total_tokens": tokens}, cost_usd=0.0, error=None,
    )


@pytest.fixture()
def patched(monkeypatch):
    captured = {}

    def fake_provider(config, fake, debug_path=None):
        return object()

    async def fake_run_subagent(provider, workspace, objective, **kwargs):
        captured.update({"objective": objective, "workspace": str(workspace), **kwargs})
        return _fake_result()

    monkeypatch.setattr(entrypoint, "build_provider", fake_provider)
    monkeypatch.setattr("app.runtime.subagents.run_subagent", fake_run_subagent)
    return captured


def test_explore_cli_outputs_json(tmp_path, monkeypatch, capsys, patched):
    monkeypatch.setattr(entrypoint.Config, "from_env",
                        lambda: Config(workspace=tmp_path, api_key="sk-test"))
    monkeypatch.setattr("sys.argv", ["vibe", "--explore", "读 README", "--fake",
                                     "--workspace", str(tmp_path)])
    assert entrypoint.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["tokens"] == 15
    assert patched["objective"] == "读 README"


def test_explore_cli_uses_env_budgets(tmp_path, monkeypatch, capsys, patched):
    monkeypatch.setenv("VIBE_EXPLORE_MAX_STEPS", "9")
    monkeypatch.setenv("VIBE_EXPLORE_MAX_TOKENS", "12345")
    monkeypatch.setenv("VIBE_EXPLORE_MAX_SECONDS", "30")
    monkeypatch.setattr(entrypoint.Config, "from_env",
                        lambda: Config(workspace=tmp_path, api_key="sk-test"))
    monkeypatch.setattr("sys.argv", ["vibe", "--explore", "读", "--fake",
                                     "--workspace", str(tmp_path)])
    entrypoint.main()
    assert patched["max_steps"] == 9
    assert patched["max_run_tokens"] == 12345
    assert patched["max_run_seconds"] == 30.0


def test_explore_cli_failure_status_exits_one(tmp_path, monkeypatch, capsys, patched):
    async def failing(provider, workspace, objective, **kwargs):
        return _fake_result(status="error")

    monkeypatch.setattr("app.runtime.subagents.run_subagent", failing)
    monkeypatch.setattr(entrypoint.Config, "from_env",
                        lambda: Config(workspace=tmp_path, api_key="sk-test"))
    monkeypatch.setattr("sys.argv", ["vibe", "--explore", "读", "--fake",
                                     "--workspace", str(tmp_path)])
    assert entrypoint.main() == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_explore_cli_exception_exits_two(tmp_path, monkeypatch, capsys, patched):
    async def exploding(provider, workspace, objective, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.runtime.subagents.run_subagent", exploding)
    monkeypatch.setattr(entrypoint.Config, "from_env",
                        lambda: Config(workspace=tmp_path, api_key="sk-test"))
    monkeypatch.setattr("sys.argv", ["vibe", "--explore", "读", "--fake",
                                     "--workspace", str(tmp_path)])
    assert entrypoint.main() == 2
    assert "boom" in capsys.readouterr().out


def test_explore_cli_rejects_other_modes(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["vibe", "--explore", "读", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--explore cannot be combined" in capsys.readouterr().err
