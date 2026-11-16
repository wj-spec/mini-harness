from __future__ import annotations

import io
import os
import stat
import subprocess
import sys

import pytest

from app.runtime.user_config import config_path
from scripts import start
from scripts.launcher_config import load_environment, save_model_config


def test_env_is_data_and_shell_variables_win_including_aliases(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "VIBE_API_KEY='file-key'\nVIBE_BASE_URL=https://example.com/v1\n"
        "VIBE_MODEL='model #1'\nVIBE_AUTONOMY=ask\n"
        "VIBE_NOTE='$(touch never) $HOME'\nPATH=/bad\n"
    )
    env = load_environment(path, {"OPENAI_API_KEY": "shell-key", "OPENAI_BASE_URL": "http://localhost:8000/v1"})
    assert env["OPENAI_API_KEY"] == "shell-key"
    assert "VIBE_API_KEY" not in env
    assert "VIBE_BASE_URL" not in env
    assert env["VIBE_MODEL"] == "model #1"
    assert env["VIBE_NOTE"] == "$(touch never) $HOME"
    assert "PATH" not in env
    assert not (tmp_path / "never").exists()


def test_malformed_env_does_not_leak_its_content(tmp_path):
    path = tmp_path / ".env"
    path.write_text("VIBE_API_KEY='super-secret\n")
    with pytest.raises(ValueError) as exc:
        load_environment(path, {})
    assert "第 1 行" in str(exc.value)
    assert "super-secret" not in str(exc.value)


def test_save_preserves_settings_and_roundtrips_secret(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# 保留\nVIBE_AUTONOMY=ask\nVIBE_MODEL=old\nexport VIBE_API_KEY=old\nOPENAI_API_KEY=old\n")
    secret = "sk-a'b#c$()"
    save_model_config(path, {"VIBE_MODEL": "new", "VIBE_API_KEY": secret,
                             "VIBE_BASE_URL": "https://example.com/v1", "VIBE_PROVIDER": "openai"})
    assert load_environment(path, {})["VIBE_API_KEY"] == secret
    assert load_environment(path, {})["VIBE_MODEL"] == "new"
    assert "# 保留\nVIBE_AUTONOMY=ask\n" in path.read_text()
    assert "OPENAI_API_KEY=" not in path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_refuses_symlink_and_preserves_file_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "private"
    target.write_text("original")
    path = tmp_path / ".env"
    path.symlink_to(target)
    with pytest.raises(ValueError):
        save_model_config(path, {"VIBE_API_KEY": "new"})
    assert target.read_text() == "original"
    path.unlink()
    path.write_text("original")
    monkeypatch.setattr(os, "replace", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        save_model_config(path, {"VIBE_API_KEY": "new"})
    assert path.read_text() == "original"
    assert list(tmp_path.glob(".env.*")) == []


def test_configure_custom_service_and_redacted_audit(tmp_path, monkeypatch, capsys):
    answers = iter(["2", "https://example.com/v1", "my-model", "y"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(start.getpass, "getpass", lambda _: "sk-hidden-secret")
    assert start.configure(tmp_path, {}, start.Actions(tmp_path)) is False
    saved = load_environment(config_path({}), {})
    assert saved["VIBE_MODEL"] == "my-model"
    assert saved["VIBE_API_KEY"] == "sk-hidden-secret"
    assert "sk-hidden-secret" not in (tmp_path / ".vibe" / "setup-audit.jsonl").read_text()
    assert "sk-hidden-secret" not in capsys.readouterr().out


def test_cancel_configure_keeps_existing_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("VIBE_MODEL=old\n")
    answers = iter(["1", "n"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(start.getpass, "getpass", lambda _: "sk-new")
    with pytest.raises(start.Cancelled):
        start.configure(tmp_path, {}, start.Actions(tmp_path))
    assert path.read_text() == "VIBE_MODEL=old\n"


def test_demo_never_asks_for_key_or_writes_config(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "3")
    monkeypatch.setattr(start.getpass, "getpass", lambda _: pytest.fail("key prompt"))
    assert start.configure(tmp_path, {}, start.Actions(tmp_path)) is True
    assert not (tmp_path / ".env").exists()


@pytest.mark.parametrize("url", ["ftp://example.com", "https://user:secret@example.com/v1", "https://example.com/v1?key=secret", "bad"])
def test_invalid_endpoint_does_not_echo_secret(url):
    with pytest.raises(ValueError) as exc:
        start.validate_endpoint(url)
    assert "secret" not in str(exc.value)


def test_no_tty_missing_configuration_exits_without_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(start, "prepare_dependencies", lambda *args: None)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unexpected prompt"))
    assert start.main([], root=tmp_path, environ={}) == 2


def test_demo_loads_env_and_preserves_workspace_and_arguments(tmp_path, monkeypatch):
    root = tmp_path / "app"
    root.mkdir()
    (root / ".env").write_text("VIBE_MODEL=saved-model\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(start, "prepare_dependencies", lambda *args: None)
    calls = []
    monkeypatch.setattr(start.Actions, "commands", lambda self, commands, **kwargs: calls.append((commands, kwargs)) or 0)
    assert start.main(["--demo", "--yes", "--workspace", "my repo", "--autonomy", "ask"], root=root, environ={}) == 0
    command = calls[-1][0][0]
    assert command[-2:] == ["--autonomy", "ask"]
    assert str(tmp_path / "my repo") in command
    assert "--fake" in command
    assert calls[-1][1]["env"]["VIBE_MODEL"] == "saved-model"
    assert calls[-1][1]["env"]["VIBE_FAKE_DEMO"] == "1"


def test_explicit_model_and_endpoint_override_saved_config(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("VIBE_API_KEY=sk-saved\nVIBE_MODEL=old\n")
    monkeypatch.setattr(start, "prepare_dependencies", lambda *args: None)
    calls = []
    monkeypatch.setattr(start.Actions, "commands", lambda self, commands, **kwargs: calls.append(commands) or 0)
    assert start.main(["--model", "new", "--base-url", "https://example.com/v1"], root=tmp_path, environ={}) == 0
    assert calls[-1][0][-4:] == ["--model", "new", "--base-url", "https://example.com/v1"]


def test_doctor_does_not_install_or_configure(tmp_path, monkeypatch):
    monkeypatch.setattr(start, "prepare_dependencies", lambda *args: pytest.fail("install"))
    monkeypatch.setattr(start, "configure", lambda *args: pytest.fail("configure"))
    calls = []
    monkeypatch.setattr(start.Actions, "commands", lambda self, commands, **kwargs: calls.append(commands) or 0)
    assert start.main(["--doctor", "--json"], root=tmp_path, environ={}) == 0
    assert "--doctor" in calls[0][0]
    assert "--json" in calls[0][0]


def test_dependency_failure_stops_before_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(start, "prepare_dependencies", lambda *args: (_ for _ in ()).throw(start.SetupError("安装失败")))
    monkeypatch.setattr(start.Actions, "commands", lambda *args, **kwargs: pytest.fail("launch"))
    assert start.main(["--demo"], root=tmp_path, environ={}) == 2


def test_rejected_approval_runs_no_command(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("executed"))
    with pytest.raises(start.Cancelled):
        start.Actions(tmp_path).commands([["npm", "ci"]], env={}, confirm=True)


def test_install_uses_uv_and_npm_and_can_retry_after_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(start.shutil, "which", lambda name, **kw: "/bin/" + name if name in {"node", "npm", "uv"} else None)
    actions = start.Actions(tmp_path, yes=True)
    monkeypatch.setattr(actions, "probe", lambda command, env, **kw: subprocess.CompletedProcess(command, 0, "v22.0.0\n", ""))
    calls = []
    monkeypatch.setattr(actions, "commands", lambda commands, **kw: calls.append(commands) or 1)
    with pytest.raises(start.SetupError, match="安装失败"):
        start.prepare_dependencies(tmp_path, {}, actions)
    assert calls[0] == [["/bin/uv", "sync", "--frozen", "--inexact"], ["/bin/npm", "--prefix", "tui", "ci"]]
    monkeypatch.setattr(actions, "commands", lambda commands, **kw: calls.append(commands) or 0)
    env = {}
    start.prepare_dependencies(tmp_path, env, actions)
    assert calls[1] == calls[0]
    assert env["PYTHON"] == str(tmp_path / ".venv/bin/python3")


def test_without_uv_installs_declared_dependencies_in_local_venv(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = ["httpx==0.28.1", "fastmcp==4.1.1"]\n')
    monkeypatch.setattr(start.shutil, "which", lambda name, **kw: "/bin/" + name if name in {"node", "npm"} else None)
    actions = start.Actions(tmp_path, yes=True)
    monkeypatch.setattr(actions, "probe", lambda command, env, **kw: subprocess.CompletedProcess(command, 0, "v22.0.0\n", ""))
    calls = []
    monkeypatch.setattr(actions, "commands", lambda commands, **kw: calls.extend(commands) or 0)
    start.prepare_dependencies(tmp_path, {}, actions)
    assert calls[0] == [sys.executable, "-m", "venv", str(tmp_path / ".venv")]
    assert calls[2] == [str(tmp_path / ".venv/bin/python3"), "-m", "pip", "install", "httpx==0.28.1", "fastmcp==4.1.1"]


def test_ready_environment_does_not_reinstall_dependencies(tmp_path, monkeypatch):
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python3").touch()
    (tmp_path / "tui/node_modules/ink").mkdir(parents=True)
    monkeypatch.setattr(start.shutil, "which", lambda name, **kw: "/bin/" + name)
    actions = start.Actions(tmp_path)
    monkeypatch.setattr(actions, "probe", lambda command, env, **kw: subprocess.CompletedProcess(command, 0, "v22.0.0\n", ""))
    monkeypatch.setattr(actions, "commands", lambda *args, **kw: pytest.fail("reinstall"))
    start.prepare_dependencies(tmp_path, {}, actions)


def test_old_node_exits_before_install(tmp_path, monkeypatch):
    monkeypatch.setattr(start.shutil, "which", lambda name, **kw: "/bin/" + name)
    actions = start.Actions(tmp_path)
    monkeypatch.setattr(actions, "probe", lambda command, env, **kw: subprocess.CompletedProcess(command, 0, "v20.0.0\n", ""))
    monkeypatch.setattr(actions, "commands", lambda *args, **kw: pytest.fail("install"))
    with pytest.raises(start.SetupError, match="22"):
        start.prepare_dependencies(tmp_path, {}, actions)


def test_doctor_fake_mode_does_not_require_api_key():
    from scripts.doctor import check_api_key
    result = check_api_key({"VIBE_PROVIDER": "fake"})
    assert result.status == "ok"
    assert "无需" in result.label


def test_first_run_interactive_opens_ink_instead_of_terminal_wizard(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", type("TTY", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(start, "prepare_dependencies", lambda *args: None)
    monkeypatch.setattr(start, "configure", lambda *args: pytest.fail("terminal wizard"))
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unexpected prompt"))
    calls = []
    monkeypatch.setattr(start.Actions, "commands", lambda self, commands, **kwargs: calls.append(kwargs) or 0)
    assert start.main([], root=tmp_path, environ={}) == 0
    assert calls[-1]["env"].get("VIBE_FAKE_DEMO") is None
    command = calls[-1]["env"].get("VIBE_CONFIG_FILE")
    assert command == str(config_path({}))
    assert calls[-1]["env"]["VIBE_MODEL_ENV_KEYS"] == "[]"


def test_placeholder_shell_key_counts_as_unconfigured_and_opens_ink(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", type("TTY", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(start, "prepare_dependencies", lambda *args: None)
    monkeypatch.setattr(start, "configure", lambda *args: pytest.fail("terminal wizard"))
    calls = []
    monkeypatch.setattr(start.Actions, "commands", lambda self, commands, **kwargs: calls.append(kwargs) or 0)
    assert start.main([], root=tmp_path, environ={"VIBE_API_KEY": "sk-your-key-here"}) == 0
    assert calls[-1]["env"]["VIBE_API_KEY"] == "sk-your-key-here"
    assert calls[-1]["env"]["VIBE_MODEL_ENV_KEYS"] == '["VIBE_API_KEY"]'


@pytest.mark.parametrize("service", ["default", "custom"])
def test_configure_in_real_terminal_hides_key_and_saves_to_requested_root(tmp_path, service):
    import pty
    import select
    import time
    from pathlib import Path

    try:
        master, slave = pty.openpty()
    except OSError:
        pytest.skip("PTY unavailable")
    secret = "sk-pty-hidden-987654"
    code = "from pathlib import Path; from scripts.start import main; import sys; sys.exit(main(['--configure'], root=Path(sys.argv[1]), environ={}))"
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1], stdin=slave, stdout=slave, stderr=slave,
        start_new_session=True,
    )
    os.close(slave)
    transcript = bytearray()

    def receive(marker: str):
        deadline = time.monotonic() + 10
        while marker.encode() not in transcript:
            assert time.monotonic() < deadline, "setup prompt timed out"
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                transcript.extend(chunk)
        assert marker.encode() in transcript

    try:
        receive("选择 [1]")
        os.write(master, b"2\n" if service == "custom" else b"1\n")
        if service == "custom":
            receive("服务地址")
            os.write(master, b"https://example.com/v1\n")
            receive("模型名")
            os.write(master, b"test-model\n")
        receive("API Key")
        os.write(master, (secret + "\n").encode())
        receive("继续？")
        os.write(master, b"y\n")
        receive("模型配置已保存")
        assert process.wait(timeout=10) == 0
        assert secret.encode() not in transcript
        assert load_environment(config_path({}), {})["VIBE_API_KEY"] == secret
        assert secret not in (tmp_path / ".vibe/setup-audit.jsonl").read_text()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        os.close(master)


def test_default_service_only_requests_key_and_save_confirmation(tmp_path, monkeypatch, capsys):
    prompts = []
    def answer(prompt):
        prompts.append(prompt)
        if prompt.startswith("选择"):
            return "1"
        if prompt.startswith("继续"):
            return "y"
        pytest.fail(f"unexpected default-service prompt: {prompt}")
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(start.getpass, "getpass", lambda _: "sk-local-test")
    assert start.configure(tmp_path, {}, start.Actions(tmp_path)) is False
    env = load_environment(config_path({}), {})
    assert env["VIBE_BASE_URL"] == start.DEFAULT_BASE_URL
    assert env["VIBE_MODEL"] == start.DEFAULT_MODEL
    assert len(prompts) == 2
    assert start.DEFAULT_MODEL in capsys.readouterr().out


@pytest.mark.parametrize("custom", [False, True])
def test_template_model_settings_reach_provider_request(tmp_path, monkeypatch, custom):
    import asyncio
    import json
    from pathlib import Path
    import httpx
    from app.config import Config
    from app.__main__ import build_provider
    from core.contracts import Message

    for name in list(os.environ):
        if name.startswith("VIBE_") or name in {"OPENAI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_BASE_URL"}:
            monkeypatch.delenv(name)
    template = (Path(__file__).resolve().parents[1] / ".env.example").read_text()
    path = tmp_path / ".env"
    path.write_text(template.replace("sk-your-key-here", "sk-fixture-only"))
    if custom:
        with path.open("a") as handle:
            handle.write("\nVIBE_BASE_URL=https://provider.example/v1\nVIBE_MODEL=custom-model\nVIBE_TRUST_ENV=1\n")
    for name, value in load_environment(path, {}).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("VIBE_LLM_MAX_OUTPUT_TOKENS", "128")
    config = Config.from_env()
    assert config.completion_guard and config.workspace_audit and config.contract_backcheck
    assert not config.llm_auditor
    provider = build_provider(config, False)
    assert provider.trust_env is custom
    captured = []
    def handle(request):
        captured.append(request)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    provider.transport = httpx.MockTransport(handle)
    async def collect():
        return [event async for event in provider.stream([Message("user", "hello")], [])]
    asyncio.run(collect())
    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == ("https://provider.example/v1" if custom else start.DEFAULT_BASE_URL) + "/chat/completions"
    assert json.loads(request.content)["model"] == ("custom-model" if custom else start.DEFAULT_MODEL)
    assert request.headers["Authorization"] == "Bearer sk-fixture-only"
    assert "max_tokens" not in json.loads(request.content)


@pytest.mark.parametrize("script", ["run_vibe.sh"])
def test_direct_launch_scripts_do_not_load_dotenv(tmp_path, script):
    import shutil
    from pathlib import Path
    project = Path(__file__).resolve().parents[1]
    shutil.copy(project / script, tmp_path / script)
    (tmp_path / ".env").write_text("VIBE_MODEL=file-model\n")
    (tmp_path / "tui/node_modules/ink").mkdir(parents=True)
    (tmp_path / "tui/dist/src").mkdir(parents=True)
    (tmp_path / "tui/dist/src/cli.js").touch()
    (tmp_path / "tui/src").mkdir(parents=True)
    commands = tmp_path / "bin"
    commands.mkdir()
    for name in ("node", "npm", "python3"):
        executable = commands / name
        executable.write_text('#!/bin/sh\nprintf "%s\\n" "${VIBE_MODEL-unset}"\n')
        executable.chmod(0o755)
    env = {**os.environ, "PATH": f"{commands}:{os.environ['PATH']}", "PYTHON": str(commands / "python3")}
    env.pop("VIBE_MODEL", None)
    for model, expected in [(None, "unset"), ("shell-model", "shell-model")]:
        current = {**env, **({"VIBE_MODEL": model} if model else {})}
        result = subprocess.run(["bash", str(tmp_path / script), "--help"], env=current,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected


def test_broken_env_opens_the_ink_problem_page_without_leaking_content(tmp_path, monkeypatch):
    secret = "sk-should-not-leak"
    (tmp_path / ".env").write_text("VIBE_API_KEY='unterminated\n# " + secret + "\n")
    monkeypatch.setattr(sys, "stdin", type("TTY", (), {"isatty": lambda self: True})())
    monkeypatch.setattr(start, "prepare_dependencies", lambda *args: None)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unexpected prompt"))
    calls = []
    monkeypatch.setattr(start.Actions, "commands", lambda self, commands, **kwargs: calls.append(kwargs) or 0)
    assert start.main([], root=tmp_path, environ={}) == 0
    env = calls[-1]["env"]
    assert env["VIBE_BOOTSTRAP"] == "1"
    assert "配置文件无法解析" in env["VIBE_CONFIG_PROBLEM"]
    assert "第 1 行" in env["VIBE_CONFIG_PROBLEM"]
    assert secret not in env["VIBE_CONFIG_PROBLEM"]


def test_broken_env_noninteractive_reports_fix_steps_without_launching(tmp_path, monkeypatch, capsys):
    secret = "sk-should-not-leak"
    (tmp_path / ".env").write_text("VIBE_API_KEY='unterminated\n# " + secret + "\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(start, "prepare_dependencies", lambda *args: None)
    monkeypatch.setattr(start.Actions, "commands", lambda *args, **kwargs: pytest.fail("launch"))
    assert start.main([], root=tmp_path, environ={}) == 2
    error = capsys.readouterr().err
    assert "配置文件无法解析" in error and "--demo" in error
    assert secret not in error


def test_demo_ignores_broken_env_with_a_warning(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text("VIBE_API_KEY='unterminated\n")
    monkeypatch.setattr(start, "prepare_dependencies", lambda *args: None)
    calls = []
    monkeypatch.setattr(start.Actions, "commands", lambda self, commands, **kwargs: calls.append(kwargs) or 0)
    assert start.main(["--demo"], root=tmp_path, environ={}) == 0
    assert calls[-1]["env"]["VIBE_FAKE_DEMO"] == "1"
    assert "VIBE_BOOTSTRAP" not in calls[-1]["env"]
    assert "配置文件无法解析" in capsys.readouterr().err
