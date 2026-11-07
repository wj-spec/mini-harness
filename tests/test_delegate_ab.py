"""delegate A/B 脚本的纯函数测试。"""

from __future__ import annotations

import shutil

import pytest

from scripts import delegate_ab


def test_check_answer_requires_all_markers():
    text = "说明...\nRESULT=Widget management service|ZEBRA|FALCON"
    ok, value = delegate_ab.check_answer(text)
    assert ok is True and "Widget" in value
    assert delegate_ab.check_answer("RESULT=widget|zebra")[0] is False
    assert delegate_ab.check_answer("没有结果行")[0] is False


def test_build_workspace_files(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git unavailable")
    workspace = tmp_path / "ws"
    delegate_ab.build_workspace(workspace)
    assert "ZEBRA" in (workspace / "docs" / "design.md").read_text(encoding="utf-8")
    assert "FALCON" in (workspace / "notes.txt").read_text(encoding="utf-8")
    assert (workspace / ".git").is_dir()


def test_digest_workspace_and_check(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git unavailable")
    workspace = tmp_path / "digest"
    delegate_ab.build_workspace(workspace, "digest")
    modules = sorted((workspace / "mods").glob("module_*.md"))
    assert len(modules) == 12
    text = modules[0].read_text(encoding="utf-8")
    assert "OWNER: alice" in text and "PRIORITY: 3" in text
    assert delegate_ab.check_digest("SUMMARY=a\nRESULT=4|9")[0] is True
    assert delegate_ab.check_digest("RESULT=3|9")[0] is False
    assert delegate_ab.check_digest("没有结果")[0] is False
