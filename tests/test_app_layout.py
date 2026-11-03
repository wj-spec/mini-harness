"""app/ 的物理布局契约（2026-11-03 分域重构后）。"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
DOMAINS = ("runtime", "tools", "safety", "session", "profiles", "browser", "eval")
ALLOWED_TOP = {
    ".DS_Store", "__pycache__", "README.md", "__init__.py", "__main__.py",
    "config.py", "events.py", "stdio.py", "providers", *DOMAINS,
}
ENTRY_MODULES = {"app.__main__", "app.stdio"}
LEGACY_FILES = (
    "agent", "tools", "patch", "diff", "web_search", "mcp", "skills", "skill_history",
    "policy", "permissions", "permission_policy", "sandbox", "sandbox_proxy",
    "session", "trace", "audit", "auditor", "evidence", "context_snapshot_display",
    "instructions", "project_profile", "user_profile",
    "browser_server", "browser_policy",
    "eval", "benchmark", "eval_container", "swebench_data", "budget_checks",
    "bench_profile", "oneshot",
)


def _imported_modules(py: Path):
    tree = ast.parse(py.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name


def test_app_top_level_is_boundary_plus_domains():
    entries = {p.name for p in APP.iterdir()}
    assert entries - ALLOWED_TOP == set(), f"unexpected app/ entries: {entries - ALLOWED_TOP}"
    assert {p.name for p in APP.glob("*.py")} == {
        "__init__.py", "__main__.py", "config.py", "events.py", "stdio.py",
    }
    for domain in DOMAINS:
        assert (APP / domain / "__init__.py").is_file(), domain


def test_legacy_flat_modules_are_gone():
    remaining = [name for name in LEGACY_FILES if (APP / f"{name}.py").exists()]
    assert not remaining, f"legacy flat modules still present: {remaining}"


def test_domains_do_not_import_entry_layer():
    problems = []
    for domain in DOMAINS:
        for py in (APP / domain).rglob("*.py"):
            for module in _imported_modules(py):
                if module in ENTRY_MODULES:
                    problems.append(f"{py.relative_to(APP)} -> {module}")
    assert not problems, problems
