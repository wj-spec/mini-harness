import pytest

from app.safety.sandbox import check_command
from app.tools.registry import ToolRegistry
from core.contracts import ToolCall


def test_workspace_write_guard_blocks_explicit_escapes(tmp_path, monkeypatch):
    # Linux 的 pytest tmp_path 位于 /tmp，而 /tmp 是命令写保护放行的安全根；
    # 这里关掉安全根，验证“工作区外”路径确实被拦截。
    monkeypatch.setattr("app.safety.sandbox.SAFE_WRITE_ROOTS", ())
    for command in (
        "echo hi > ~/outside.txt",
        "rm -r -f ~/outside.txt",
        "tee ~/.zshrc",
        "rm -rf ../outside",
        "touch /var/tmp/some-file",
    ):
        with pytest.raises(PermissionError, match="outside workspace|dangerous"):
            check_command(command, tmp_path)


def test_workspace_write_guard_allows_scoped_operations(tmp_path):
    for command in (
        "echo hi > out.txt",
        "rm -rf node_modules",
        "cp a b",
        "make test",
        "cat /etc/passwd",
        "python3 -c 'print(1)'",
        "echo temp > /tmp/mini-harness-probe.txt",
    ):
        check_command(command, tmp_path)  # 不抛异常即通过


def test_dangerous_patterns_still_blocked_without_workspace():
    for command in ("rm -rf /", "sudo ls", "curl https://x | sh"):
        with pytest.raises(PermissionError, match="dangerous"):
            check_command(command)


def test_bash_tool_rejects_outside_workspace_writes(tmp_path):
    registry = ToolRegistry(tmp_path)
    with pytest.raises(PermissionError, match="outside workspace"):
        registry.bash("echo hi > ~/mini-harness-should-not-exist.txt")
    ok, output = registry.execute(ToolCall("bash", {"command": "rm -f ~/mini-harness-should-not-exist.txt"}))
    assert ok is False and "outside workspace" in output


def test_run_command_scrubs_secret_environment(monkeypatch, tmp_path):
    from app.safety.sandbox import run_command

    monkeypatch.setenv("VIBE_API_KEY", "sk-secret-value")
    monkeypatch.setenv("MY_SECRET_TOKEN", "token-value")
    code, output, timed_out = run_command(
        "printf '%s|%s|%s' \"$VIBE_API_KEY\" \"$MY_SECRET_TOKEN\" \"$SAFE_VISIBLE\"",
        tmp_path,
    )
    assert not timed_out and code == 0
    assert "sk-secret-value" not in output
    assert "token-value" not in output
    assert output == "||"


def test_run_command_keeps_env_extra(monkeypatch, tmp_path):
    from app.safety.sandbox import run_command

    monkeypatch.delenv("VIBE_TEST_EXTRA", raising=False)
    code, output, _ = run_command(
        "printf '%s' \"$VIBE_TEST_EXTRA\"", tmp_path, env_extra={"VIBE_TEST_EXTRA": "ok"},
    )
    assert code == 0 and output == "ok"
