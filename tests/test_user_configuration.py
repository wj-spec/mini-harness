import asyncio
import json
import stat

import pytest

from app.config import Config
from app.runtime.model_config import load_environment, save_model_config
from app.runtime.user_config import config_path, startup_environment
from app.runtime.model_settings import ModelSettings, ModelSettingsError
from app.safety.policy import ApprovalDecision, ApprovalGate


def save(path, model="chosen", endpoint="https://one.example/v1", key="private-test-key"):
    save_model_config(path, {"VIBE_MODEL": model, "VIBE_PROVIDER": "openai",
                            "VIBE_BASE_URL": endpoint, "VIBE_API_KEY": key, "VIBE_TRUST_ENV": "0"})


def test_user_settings_and_credentials_are_separate_and_reused(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    path = config_path(env)
    save(path)
    assert "private-test-key" not in path.read_text()
    assert stat.S_IMODE((path.parent / "credentials.json").stat().st_mode) == 0o600
    for name in ("project-a", "project-b"):
        workspace = tmp_path / name
        workspace.mkdir()
        loaded = startup_environment(tmp_path / "installation", workspace, env)
        assert loaded["VIBE_MODEL"] == "chosen"
        assert loaded["VIBE_API_KEY"] == "private-test-key"
        assert loaded["VIBE_CONFIG_FILE"] == str(path)
        assert not (workspace / ".env").exists()


def test_project_and_environment_precedence_and_endpoint_binding(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    save(config_path(env))
    project = tmp_path / "project"
    (project / ".mini-harness").mkdir(parents=True)
    file = project / ".mini-harness/config.toml"
    file.write_text('model = "project-model"\n')
    loaded = startup_environment(tmp_path / "app", project, env)
    assert loaded["VIBE_MODEL"] == "project-model"
    assert loaded["VIBE_API_KEY"] == "private-test-key"
    file.write_text('model = "project-model"\nbase_url = "https://other.example/v1"\n')
    loaded = startup_environment(tmp_path / "app", project, env)
    assert not loaded.get("VIBE_API_KEY")
    loaded = startup_environment(tmp_path / "app", project, dict(env, VIBE_MODEL="shell-model", OPENAI_API_KEY="shell-key"))
    assert loaded["VIBE_MODEL"] == "shell-model"
    assert loaded["OPENAI_API_KEY"] == "shell-key"


def test_legacy_fallback_does_not_read_workspace_env_or_modify_legacy(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    root, project = tmp_path / "app", tmp_path / "project"
    root.mkdir(); project.mkdir()
    (root / ".env").write_text('VIBE_MODEL=legacy\nVIBE_API_KEY=legacy-key\n')
    (project / ".env").write_text('VIBE_API_KEY=untrusted-key\n')
    loaded = startup_environment(root, project, env)
    assert loaded["VIBE_API_KEY"] == "legacy-key"
    assert loaded["VIBE_CONFIG_FILE"] == str(config_path(env))
    assert not config_path(env).exists()
    save(config_path(env))
    assert startup_environment(root, project, env)["VIBE_MODEL"] == "chosen"
    assert "legacy-key" in (root / ".env").read_text()


def test_project_secrets_and_malformed_config_are_rejected_without_echo(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    project = tmp_path / ".mini-harness"
    project.mkdir()
    for content in ('api_key = "do-not-echo"', 'model = "do-not-echo'):
        (project / "config.toml").write_text(content)
        with pytest.raises(ValueError) as error:
            startup_environment(tmp_path / "app", tmp_path, env)
        assert "do-not-echo" not in str(error.value)


def test_model_save_approval_covers_both_files_and_credential_revision(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    path = config_path(env)
    save(path)
    calls = []
    async def approve(call, preview):
        calls.append((call, preview))
        return ApprovalDecision(True, "once")
    config = Config(model="chosen", api_key="private-test-key", base_url="https://one.example/v1", workspace=tmp_path)
    settings = ModelSettings(config, environ=env, approval=ApprovalGate(approve))
    values = dict(model="next", api_key="new-key", base_url=config.base_url, trust_env=False,
                  revision=settings.revision())
    asyncio.run(settings.save(values))
    assert "credentials.json" in calls[0][1]
    assert calls[1][0].args["path"] == str(path.parent / "credentials.json")
    assert "new-key" not in calls[0][1]
    assert load_environment(path, {})["VIBE_API_KEY"] == "new-key"
    values["revision"] = settings.revision()
    credential = path.parent / "credentials.json"
    credential.write_text(credential.read_text() + "\n")
    with pytest.raises(ModelSettingsError, match="已变化"):
        asyncio.run(settings.save(values))


def test_source_describes_effective_config_not_unsaved_disk(tmp_path):
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    path = config_path(env)
    save(path)
    loaded = startup_environment(tmp_path / "app", tmp_path, env)
    config = Config(model=loaded["VIBE_MODEL"], api_key=loaded["VIBE_API_KEY"])
    settings = ModelSettings(config, environ=loaded)
    save(path, model="external-change")
    assert settings.overview()["model"] == "chosen"
    assert settings.overview()["saved_model"] == "external-change"
    assert settings.overview()["sources"]["model"] == "用户配置"


def test_failed_config_commit_keeps_original_credential_active(tmp_path, monkeypatch):
    from app.runtime import user_config
    path = tmp_path / "config.toml"
    save(path)
    original = user_config.atomic_private_write
    def fail_config(target, content):
        if target == path:
            raise OSError("disk full")
        original(target, content)
    monkeypatch.setattr(user_config, "atomic_private_write", fail_config)
    with pytest.raises(OSError):
        save(path, model="next", key="next-secret")
    assert load_environment(path, {})["VIBE_API_KEY"] == "private-test-key"
    assert load_environment(path, {})["VIBE_MODEL"] == "chosen"


def test_bootstrap_saved_settings_replace_inherited_file_values(tmp_path):
    from app.runtime.bootstrap import Bootstrap
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    path = config_path(env)
    save(path)
    loaded = startup_environment(tmp_path / "app", tmp_path, env)
    state = Bootstrap(Config(workspace=tmp_path), lambda _: None, environ=loaded)
    save(path, model="next", key="next-secret")
    activated = state.activation_env(load_environment(path, {}))
    assert activated["VIBE_MODEL"] == "next"
    assert activated["VIBE_API_KEY"] == "next-secret"


def test_start_passes_loaded_user_config_to_ink(tmp_path, monkeypatch):
    from scripts import start
    env = {"XDG_CONFIG_HOME": str(tmp_path / "config")}
    save(config_path(env))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(start, "prepare_dependencies", lambda *args: None)
    calls = []
    monkeypatch.setattr(start.Actions, "commands", lambda self, commands, **kwargs: calls.append((commands, kwargs)) or 0)
    assert start.main([], root=tmp_path / "installation", environ=env) == 0
    command, options = calls[0]
    assert command[0][-2:] == ["--workspace", str(tmp_path)]
    assert options["env"]["VIBE_MODEL"] == "chosen"
    assert options["env"]["VIBE_CONFIG_LOADED"] == "1"


def test_ink_public_entry_routes_through_start_without_changing_cwd(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path
    root = tmp_path / "app"
    workspace = tmp_path / "project with spaces"
    root.mkdir(); workspace.mkdir()
    shutil.copy(Path(__file__).resolve().parents[1] / "run_ink.sh", root)
    launcher = root / "start.sh"
    launcher.write_text('#!/bin/sh\npwd\nprintf "%s\\n" "$@"\n')
    launcher.chmod(0o755)
    result = subprocess.run(["bash", str(root / "run_ink.sh"), "--model", "chosen"],
                            cwd=workspace, env={}, capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.splitlines() == [str(workspace), "--model", "chosen"]


def test_cli_endpoint_override_does_not_send_saved_credentials(tmp_path, monkeypatch):
    import sys
    from app import __main__ as entry
    from app.runtime import bootstrap
    config = Config(workspace=tmp_path, api_key="saved-private-key", base_url="https://one.example/v1")
    monkeypatch.setattr(entry.Config, "from_env", lambda: config)
    monkeypatch.setenv("VIBE_MODEL_ENV_KEYS", "[]")
    monkeypatch.setattr(sys, "argv", ["mini-harness", "--stdio", "--base-url", "https://other.example/v1"])
    seen = []
    monkeypatch.setattr(bootstrap, "run_bootstrap", lambda c: seen.append(c.api_key))
    entry.main()
    assert seen == [""]


def test_denied_credentials_approval_writes_neither_file(tmp_path):
    path = tmp_path / "config.toml"
    async def approve(call, preview):
        allowed = call.args["path"] == str(path)
        return ApprovalDecision(allowed, "once" if allowed else "deny")
    settings = ModelSettings(Config(api_key="private-test-key"), path=path, environ={}, approval=ApprovalGate(approve))
    values = dict(model="chosen", api_key="", base_url=settings.config.base_url,
                  trust_env=False, revision=settings.revision())
    with pytest.raises(ModelSettingsError, match="未获允许"):
        asyncio.run(settings.save(values))
    assert not path.exists()
    assert not (path.parent / "credentials.json").exists()
