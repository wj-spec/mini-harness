import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "browser_baseline_check", ROOT / "scripts" / "browser_baseline_check.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _root(tmp_path: Path, *, pinned: str = "@playwright/mcp@0.0.83", latest: bool = False) -> Path:
    for name in ("examples", "scripts", "app", ".vibe/ms-playwright/chromium-1247"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    for relative in ("examples/browser-mcp.json", "examples/browser-actions-mcp.json"):
        suffix = " @latest" if latest else ""
        (tmp_path / relative).write_text(
            '{"mcpServers": {"browser": {"args": ["%s%s"]}}}' % (pinned, suffix),
            encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('dependencies = ["fastmcp==4.1.1"]\n', encoding="utf-8")
    for relative in ("scripts/browser_acceptance.py", "scripts/browser_actions_smoke.py",
                     "scripts/browser_smoke.py", "app/browser_server.py"):
        (tmp_path / relative).write_text("", encoding="utf-8")
    return tmp_path


def test_baseline_check_passes_with_pinned_versions(tmp_path):
    module = _module()
    report = module.check(_root(tmp_path))
    assert report["passed"] is True, report["problems"]
    assert report["baseline"]["playwright_mcp"] == "@playwright/mcp@0.0.83"


def test_baseline_check_flags_unpinned_and_latest(tmp_path):
    module = _module()
    report = module.check(_root(tmp_path, pinned="@playwright/mcp@0.0.99"))
    assert report["passed"] is False
    assert any("未固定" in problem for problem in report["problems"])
    report_latest = module.check(_root(tmp_path / "latest", latest=True))
    assert any("@latest" in problem for problem in report_latest["problems"])


def test_baseline_check_flags_missing_chromium_and_entries(tmp_path):
    module = _module()
    root = _root(tmp_path)
    (root / ".vibe/ms-playwright/chromium-1247").rmdir()
    report = module.check(root)
    assert any("chromium-1247" in problem for problem in report["problems"])
    assert module.main(["--allow-missing-chromium"]) in (0, 1)  # 真实仓库入口检查不崩溃
    filtered = [item for item in report["problems"] if "chromium" not in item.lower()]
    assert filtered == [] or report["passed"] is False


def test_baseline_check_reports_missing_entries(tmp_path):
    module = _module()
    root = _root(tmp_path)
    (root / "scripts/browser_acceptance.py").unlink()
    report = module.check(root)
    assert any("browser_acceptance.py" in problem for problem in report["problems"])
