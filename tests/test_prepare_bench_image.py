import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "prepare_bench_image.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("prepare_bench_image", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_prepare_task_image_is_idempotent(tmp_path):
    module = _load_module()
    task = tmp_path / "astropy__astropy-7606"
    (task / "environment").mkdir(parents=True)
    (task / "environment" / "Dockerfile").write_text(
        "FROM swebench/example:latest\nWORKDIR /testbed\nRUN mkdir -p /logs\n",
        encoding="utf-8",
    )
    (task / "task.toml").write_text(
        "[verifier]\n# timeout\ntimeout_sec = 3000\n\n[agent]\ntimeout_sec = 3000\n",
        encoding="utf-8",
    )

    first = module.prepare_task_image(task, pypi_index="https://mirror.example/simple")
    assert first["dockerfile_changed"] is True
    assert first["task_toml_changed"] is True
    dockerfile = (task / "environment" / "Dockerfile").read_text(encoding="utf-8")
    assert module.MARKER in dockerfile
    assert "/opt/vibe-venv" in dockerfile
    assert "swebench==4.0.3" in dockerfile
    assert "timeout 600" in dockerfile
    assert "timeout 900" in dockerfile
    toml = (task / "task.toml").read_text(encoding="utf-8")
    assert "UV_HTTP_TIMEOUT" in toml
    assert "https://mirror.example/simple" in toml

    second = module.prepare_task_image(task, pypi_index="https://mirror.example/simple")
    assert second["dockerfile_changed"] is False
    assert second["task_toml_changed"] is False
