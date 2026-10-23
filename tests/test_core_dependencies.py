import subprocess
import sys
from pathlib import Path


def test_core_works_without_app_dependencies(tmp_path):
    project = Path(__file__).resolve().parents[1]
    script = f"""
import importlib
import pkgutil
import sys

sys.path.insert(0, {str(project)!r})
import core

for module in pkgutil.iter_modules(core.__path__):
    importlib.import_module(f"core.{{module.name}}")

from core.models import Message
from core.summarize import summarize_messages

assert "fix bug" in summarize_messages([Message("user", "fix bug")])
assert not {{"app", "httpx", "rich"}}.intersection(sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
