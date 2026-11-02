import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sync_verified_configs.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_verified_configs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_sync_task_restores_official_lists(tmp_path):
    module = _load_module()
    task = tmp_path / "astropy__astropy-7606"
    (task / "tests").mkdir(parents=True)
    (task / "tests" / "config.json").write_text(
        """
{
  "instance_id": "astropy__astropy-7606",
  "repo": "astropy/astropy",
  "base_commit": "old",
  "FAIL_TO_PASS": "[]",
  "PASS_TO_PASS": "[]",
  "test_patch": "old"
}
""".strip(),
        encoding="utf-8",
    )
    official = {
        "astropy__astropy-7606": {
            "repo": "astropy/astropy",
            "instance_id": "astropy__astropy-7606",
            "base_commit": "new",
            "patch": "gold",
            "test_patch": "test",
            "problem_statement": "problem",
            "hints_text": "",
            "created_at": "2026-01-01",
            "version": "1.0",
            "FAIL_TO_PASS": ["test_a"],
            "PASS_TO_PASS": ["test_b"],
            "environment_setup_commit": "env",
            "difficulty": "easy",
        }
    }

    result = module.sync_task(task, official, write=True)

    assert result["status"] == "updated"
    import json

    cfg = json.loads((task / "tests" / "config.json").read_text(encoding="utf-8"))
    assert json.loads(cfg["FAIL_TO_PASS"]) == ["test_a"]
    assert json.loads(cfg["PASS_TO_PASS"]) == ["test_b"]
    assert cfg["base_commit"] == "new"
    assert cfg["test_patch"] == "test"

    again = module.sync_task(task, official, write=True)
    assert again["status"] == "ok"
