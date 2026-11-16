import json
from pathlib import Path

from scripts import start
from scripts.workbench_acceptance import prepare
from app.tools.mcp import load_servers


def test_fixture_is_private_isolated_and_does_not_inherit_model_or_mcp(tmp_path):
    (tmp_path / ".env").write_text("not a parseable env")
    actions = start.Actions(tmp_path, yes=True)
    fixture, env = prepare(tmp_path, actions, {"PATH":"/bin", "VIBE_API_KEY":"real-secret",
        "OPENAI_API_KEY":"real-secret", "VIBE_MCP_SERVERS":"remote", "VIBE_NOTIFY_WEBHOOK":"remote"})
    assert fixture.is_relative_to(tmp_path / ".vibe")
    assert (tmp_path / ".env").read_text() == "not a parseable env"
    assert not any("real-secret" in value for value in env.values())
    assert "VIBE_NOTIFY_WEBHOOK" not in env
    assert env["VIBE_PROVIDER"] == "fake"
    assert env["VIBE_CONFIG_FILE"] == str(fixture / ".env")
    assert set(load_servers(env["VIBE_MCP_CONFIG"], None)) == {"alpha","beta"}
    assert (fixture / ".env").stat().st_mode & 0o777 == 0o600
    assert (fixture / "mcp.json").stat().st_mode & 0o777 == 0o600


def test_acceptance_entry_skips_user_env_and_uses_fake_workspace(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("invalid shell expression!")
    monkeypatch.setattr(start, "prepare_dependencies", lambda *args: None)
    calls = []
    monkeypatch.setattr(start.Actions, "commands", lambda self, commands, **kw: calls.append((commands, kw)) or 0)
    assert start.main(["--acceptance"], root=tmp_path, environ={"PATH":"/bin"}) == 0
    commands, kwargs = calls[0]
    assert "--fake" in commands[0]
    workspace = Path(commands[0][commands[0].index("--workspace")+1])
    assert workspace.is_relative_to(tmp_path / ".vibe")
    assert kwargs["env"]["VIBE_FAKE_DEMO"] == "1"


def test_acceptance_environment_is_labeled_separately(tmp_path):
    actions = start.Actions(tmp_path, yes=True)
    _, env = prepare(tmp_path, actions, {"PATH": "/bin"})
    assert env["VIBE_ACCEPTANCE"] == "1"
