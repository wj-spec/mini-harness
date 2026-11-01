import importlib.util
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("script", ["run_vibe.sh", "run_tests.sh"])
def test_launch_script_uses_project_python_and_allows_override(tmp_path, script):
    project = Path(__file__).resolve().parents[1]
    shutil.copy(project / script, tmp_path / script)
    local_python = tmp_path / ".venv" / "bin" / "python3"
    local_python.parent.mkdir(parents=True)
    local_python.write_text('#!/bin/sh\nprintf "project-python\\n%s\\n" "$PYTHONPATH"\n')
    local_python.chmod(0o755)
    override = tmp_path / "override-python"
    override.write_text('#!/bin/sh\nprintf "override-python\\n"\n')
    override.chmod(0o755)
    env = dict(os.environ)
    env.pop("PYTHON", None)
    result = subprocess.run(
        ["bash", str(tmp_path / script), "--help"], env=env,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["project-python", "."]
    result = subprocess.run(
        ["bash", str(tmp_path / script), "--help"], env={**env, "PYTHON": str(override)},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "override-python"


def test_ink_launcher_builds_from_project_root_and_preserves_arguments(tmp_path):
    project = Path(__file__).resolve().parents[1]
    shutil.copy(project / "run_ink.sh", tmp_path / "run_ink.sh")
    (tmp_path / "tui" / "node_modules" / "ink").mkdir(parents=True)
    executables = tmp_path / "bin"
    executables.mkdir()
    for name in ("npm", "node"):
        fake = executables / name
        fake.write_text(f'#!/bin/sh\nprintf "{name}\\n"\npwd\nprintf "<%s>\\n" "$@"\n')
        fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{executables}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(tmp_path / "run_ink.sh"), "--fake", "--workspace", "path with spaces"],
        env=env, cwd=tmp_path.parent, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines == [
        "npm", str(tmp_path.resolve()), "<--prefix>", "<tui>", "<run>", "<build>", "<--silent>",
        "node", str(tmp_path.resolve()), "<tui/dist/src/cli.js>", "<--fake>",
        "<--workspace>", "<path with spaces>",
    ]


def test_ink_launcher_explains_missing_dependencies(tmp_path):
    project = Path(__file__).resolve().parents[1]
    shutil.copy(project / "run_ink.sh", tmp_path / "run_ink.sh")
    result = subprocess.run(
        ["bash", str(tmp_path / "run_ink.sh"), "--fake"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2
    assert "npm --prefix tui ci" in result.stderr


ROOT = Path(__file__).resolve().parents[1]
INK_ENV_KEYS = ("VIBE_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")


def _doctor_module():
    name = "launch_doctor"
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "doctor.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module  # dataclasses 需要按 __module__ 反查
    spec.loader.exec_module(module)
    return module


def _fake_commands():
    """不依赖本机 node/npm/uv/git：按命令名返回固定版本号。"""

    def which(name):
        return f"/fake/bin/{name}"

    def run(command, **kwargs):
        if Path(command[0]).name == "git" and "rev-parse" in command:
            return subprocess.CompletedProcess(command, 0, stdout="true\n", stderr="")
        versions = {
            "node": "v24.1.0",
            "npm": "12.1.0",
            "uv": "uv 0.11.16",
            "git": "git version 2.54.0",
        }
        return subprocess.CompletedProcess(command, 0, stdout=versions[Path(command[0]).name] + "\n", stderr="")

    return which, run


def _green_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tui" / "node_modules" / "ink").mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return repo, workspace


def _run_doctor(repo, workspace, *, env, missing=()):
    doctor = _doctor_module()
    which, run = _fake_commands()
    out = io.StringIO()
    code = doctor.main(
        ["--doctor", "--workspace", str(workspace)],
        repo_root=repo,
        env=env,
        stdin_tty=True,
        stdout_tty=True,
        which=which,
        run=run,
        python_path=Path("/fake/bin/python3"),
        python_source="项目 .venv",
        python_version=lambda path: (3, 12, 0),
        missing_modules=lambda path, modules: list(missing),
        out=out,
    )
    return code, out.getvalue()


def test_doctor_reports_ready_environment(tmp_path):
    repo, workspace = _green_repo(tmp_path)
    code, output = _run_doctor(repo, workspace, env={"VIBE_API_KEY": "sk-test"})
    assert code == 0, output
    assert "mini-harness 环境自检" in output
    assert "✓ Node.js v24.1.0（>=22）" in output
    assert "✓ Python 3.12.0（项目 .venv）" in output
    assert "✓ 模型 API Key 已配置（VIBE_API_KEY）" in output
    assert "✓ 工作区存在且可写" in output
    assert "VIBE_BASE_URL=https://api.deepseek.com，VIBE_MODEL=deepseek-chat" in output
    assert "结论：可以启动 ./run_ink.sh" in output
    assert "✗" not in output


def test_doctor_blocks_without_api_key(tmp_path):
    repo, workspace = _green_repo(tmp_path)
    code, output = _run_doctor(repo, workspace, env={})
    assert code == 2
    assert "✗ 未配置模型 API Key" in output
    assert ".env.example" in output
    assert "VIBE_MODEL=deepseek-chat" in output
    assert "结论：缺少 1 项，先按上面的命令补齐" in output


def test_doctor_rejects_placeholder_api_key(tmp_path):
    repo, workspace = _green_repo(tmp_path)
    code, output = _run_doctor(repo, workspace, env={"VIBE_API_KEY": "sk-your-key-here"})
    assert code == 2
    assert "✗ 模型 API Key 仍是占位值（VIBE_API_KEY）" in output
    assert "真实 key" in output
    assert "结论：缺少 1 项" in output


def test_doctor_accepts_real_key_after_placeholder(tmp_path):
    repo, workspace = _green_repo(tmp_path)
    code, output = _run_doctor(
        repo, workspace,
        env={"VIBE_API_KEY": "sk-your-key-here", "OPENAI_API_KEY": "sk-test-real"},
    )
    assert code == 0, output
    assert "✓ 模型 API Key 已配置（OPENAI_API_KEY）" in output


def test_doctor_redacts_base_url_credentials(tmp_path):
    repo, workspace = _green_repo(tmp_path)
    output_env = {
        "VIBE_API_KEY": "sk-test",
        "VIBE_BASE_URL": "https://user:secret@example.com:8443/v1?key=query-secret",
    }
    code, output = _run_doctor(repo, workspace, env=output_env)
    assert code == 0, output
    assert "VIBE_BASE_URL=https://example.com:8443" in output
    assert "secret" not in output
    assert "user:" not in output
    assert "query-secret" not in output


def test_placeholder_markers_and_url_sanitizer_are_pure_functions(tmp_path):
    doctor = _doctor_module()
    for value in ("sk-your-key-here", "YOUR-KEY", "your_api_key", "changeme", "xxx",
                  "sk-example", "dummy-key", "placeholder"):
        assert doctor.is_placeholder_key(value), value
    assert not doctor.is_placeholder_key("sk-1234567890abcdef")
    assert doctor.sanitize_base_url("https://user:secret@example.com/v1?x=1") == "https://example.com"
    assert doctor.sanitize_base_url("http://[::1]:8000/v1") == "http://[::1]:8000"
    assert doctor.sanitize_base_url("") == "https://api.deepseek.com"
    assert doctor.sanitize_base_url("not a url") == "（已隐藏）"


def test_env_example_default_key_is_detected_as_placeholder():
    doctor = _doctor_module()
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    values = [
        line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("VIBE_API_KEY=")
    ]
    assert values
    assert doctor.is_placeholder_key(values[0])


def test_doctor_blocks_without_frontend_dependencies(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    code, output = _run_doctor(repo, workspace, env={"VIBE_API_KEY": "sk-test"})
    assert code == 2
    assert "✗ 缺少前端依赖 tui/node_modules/ink" in output
    assert "npm --prefix tui ci" in output
    assert "结论：缺少 1 项" in output


def test_ink_launcher_runs_doctor_before_node_and_frontend_checks(tmp_path):
    project = Path(__file__).resolve().parents[1]
    shutil.copy(project / "run_ink.sh", tmp_path / "run_ink.sh")
    (tmp_path / "scripts").mkdir()
    shutil.copy(project / "scripts" / "doctor.py", tmp_path / "scripts" / "doctor.py")
    # 没有 .venv 时 run_ink.sh 回退到 PATH 上的 python3；这里固定到测试解释器。
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "python3").symlink_to(Path(sys.executable))
    env = {
        key: value for key, value in os.environ.items()
        if key not in INK_ENV_KEYS and key != "PYTHON"
    }
    env["PATH"] = f"{fake_bin}:{os.environ['PATH']}"
    result = subprocess.run(
        ["bash", str(tmp_path / "run_ink.sh"), "--doctor", "--workspace", str(tmp_path)],
        env=env, cwd=tmp_path.parent, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 2  # 缺前端依赖 / API key 是阻塞项，但自检本身跑起来了
    assert "mini-harness 环境自检" in result.stdout, result.stderr
    assert "npm --prefix tui ci" in result.stdout, result.stderr
    assert "Ink 入口需要 Node.js 22+ 和 npm。" not in result.stderr
