import os
import shutil
import subprocess
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
