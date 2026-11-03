"""core/ 的物理布局与依赖方向契约（2026-11-03 重构后）。"""

import ast
import importlib
import importlib.util
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"
PACKAGES = ("contracts", "budget", "context", "task")
ALLOWED_TOP = {"__init__.py", "README.md", *PACKAGES}
# 子包允许依赖的 core 子包（含自身，用于同包内相对导入）
ALLOWED_DEPS = {
    "contracts": {"contracts"},
    "budget": {"budget", "contracts"},
    "context": {"context", "contracts", "budget"},
    "task": {"task", "contracts", "budget"},
}
LEGACY_MODULES = (
    "core.models",
    "core.state",
    "core.progress",
    "core.rounds",
    "core.context_snapshot",
    "core.summarize",
)


def _core_targets(py: Path, package: str):
    """列出文件里引用的 core 子包名；同包相对导入记作 package 自身。"""
    tree = ast.parse(py.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1:
                yield package
            elif node.level == 2:
                yield (node.module or "").split(".")[0]
            elif node.module and node.module.startswith("core."):
                parts = node.module.split(".")
                yield parts[1] if len(parts) > 1 else ""
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("core."):
                    parts = alias.name.split(".")
                    yield parts[1] if len(parts) > 1 else ""


def test_core_top_level_is_domain_layout():
    entries = {
        p.name
        for p in CORE.iterdir()
        if not p.name.startswith(".") and p.name != "__pycache__"
    }
    assert entries - ALLOWED_TOP == set(), f"unexpected core/ entries: {entries - ALLOWED_TOP}"
    assert {p.name for p in CORE.glob("*.py")} == {"__init__.py"}
    for package in PACKAGES:
        assert (CORE / package / "__init__.py").is_file()


def test_subpackage_dependencies_are_one_directional():
    problems = []
    for package in PACKAGES:
        for py in (CORE / package).rglob("*.py"):
            for target in _core_targets(py, package):
                if target not in ALLOWED_DEPS[package]:
                    problems.append(f"{py.relative_to(CORE)} -> {target or 'core'}")
    assert not problems, problems


def test_legacy_module_paths_are_gone():
    for name in LEGACY_MODULES:
        assert importlib.util.find_spec(name) is None, f"{name} should be migrated"


def test_public_api_imports():
    from core.contracts import ExecutionOutcome, Message, Response, ToolCall
    from core.budget import BudgetPolicy
    from core.context import (
        ContextBuilder,
        ContextResult,
        build_context_snapshot,
        estimate_tokens,
        summarize_messages,
        total_tokens,
    )
    from core.task import (
        CompletionDecision,
        ProgressMonitor,
        TaskState,
        build_round_record,
        completion_decision,
        delivery_receipt,
    )

    import core

    assert core.Message is Message
    assert core.ToolCall is ToolCall
    assert core.Response is Response
    assert ExecutionOutcome is not None
    assert BudgetPolicy.context_window == 1_000_000
    assert callable(ContextBuilder)
    assert callable(ContextResult)
    assert callable(build_context_snapshot)
    assert callable(estimate_tokens)
    assert callable(total_tokens)
    assert callable(summarize_messages)
    assert callable(completion_decision)
    assert callable(delivery_receipt)
    assert callable(build_round_record)
    assert ProgressMonitor is not None
    assert CompletionDecision is not None
    assert TaskState is not None
    for package in PACKAGES:
        importlib.import_module(f"core.{package}")
