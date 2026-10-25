import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.models import ToolCall  # noqa: E402

from app.tools import ToolRegistry  # noqa: E402


def test_bench_restore_blocks_write_until_reread(tmp_path):
    (tmp_path / "a.txt").write_text("old\n")
    registry = ToolRegistry(tmp_path)
    registry.mark_restored()
    ok, output = registry.execute(ToolCall("write_file", {"path": "a.txt", "content": "new\n"}))
    assert ok is False
    assert "re-read before editing" in output
    registry.read_file("a.txt")
    ok, _ = registry.execute(ToolCall("write_file", {"path": "a.txt", "content": "new\n"}))
    assert ok is True
    assert (tmp_path / "a.txt").read_text() == "new\n"


def test_bench_restore_external_change_still_blocked_after_reread(tmp_path):
    (tmp_path / "a.txt").write_text("old\n")
    registry = ToolRegistry(tmp_path)
    registry.mark_restored()
    registry.read_file("a.txt")
    (tmp_path / "a.txt").write_text("external\n")
    ok, output = registry.execute(ToolCall("edit_file", {"path": "a.txt", "old": "external", "new": "x"}))
    assert ok is False
    assert "file changed since last read" in output


def test_bench_restore_new_files_allowed_without_read(tmp_path):
    registry = ToolRegistry(tmp_path)
    registry.mark_restored()
    ok, _ = registry.execute(ToolCall("write_file", {"path": "fresh.txt", "content": "one\n"}))
    assert ok is True
    ok, _ = registry.execute(ToolCall("edit_file", {"path": "fresh.txt", "old": "one", "new": "two"}))
    assert ok is True
