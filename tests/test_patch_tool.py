import pytest

from core.models import ToolCall
from app.patch import apply_hunks, parse_patch, render_summary
from app.tools import ToolRegistry

MODIFY_PATCH = """diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1,3 +1,3 @@
 a
-b
+B
 c
@@ -4,3 +4,3 @@
 d
-e
+E
 f
"""


def test_apply_hunks_modifies_multiple_hunks():
    parsed = parse_patch(MODIFY_PATCH)
    assert len(parsed) == 1
    updated, added, deleted = apply_hunks("a\nb\nc\nd\ne\nf\n", parsed[0])
    assert updated == "a\nB\nc\nd\nE\nf\n"
    assert (added, deleted) == (2, 2)


def test_patch_tool_modifies_existing_file(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("a\nb\nc\nd\ne\nf\n")
    registry = ToolRegistry(tmp_path)
    output = registry.apply_patch(MODIFY_PATCH)
    assert "applied patch: 1 file(s)" in output
    assert "modified file.txt +2 -2" in output
    assert target.read_text() == "a\nB\nc\nd\nE\nf\n"


def test_patch_tool_creates_and_deletes_files(tmp_path):
    created = tmp_path / "new.txt"
    registry = ToolRegistry(tmp_path)
    create = """diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+hello
+world
"""
    registry.apply_patch(create)
    assert created.read_text() == "hello\nworld\n"

    delete = """diff --git a/new.txt b/new.txt
deleted file mode 100644
--- a/new.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-hello
-world
"""
    output = registry.apply_patch(delete)
    assert "deleted new.txt" in output
    assert not created.exists()


def test_patch_tool_applies_multiple_files(tmp_path):
    (tmp_path / "a.txt").write_text("one\n")
    (tmp_path / "b.txt").write_text("two\n")
    patch = """diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-one
+ONE
diff --git a/b.txt b/b.txt
--- a/b.txt
+++ b/b.txt
@@ -1 +1 @@
-two
+TWO
"""
    output = ToolRegistry(tmp_path).apply_patch(patch)
    assert (tmp_path / "a.txt").read_text() == "ONE\n"
    assert (tmp_path / "b.txt").read_text() == "TWO\n"
    assert "2 file(s)" in output


def test_patch_tool_tolerates_line_numbers_and_rejects_bad_context(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("intro\na\nb\nc\n")
    patch = """diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,2 @@
 a
-b
+B
"""
    ToolRegistry(tmp_path).apply_patch(patch)
    assert target.read_text() == "intro\na\nB\nc\n"

    bad = """diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-missing
+new
"""
    with pytest.raises(ValueError, match="context mismatch"):
        ToolRegistry(tmp_path).apply_patch(bad)
    assert target.read_text() == "intro\na\nB\nc\n"


def test_patch_tool_rejects_unsafe_paths_and_renames(tmp_path):
    outside = """diff --git a/../x b/../x
--- a/../x
+++ b/../x
@@ -1 +1 @@
-a
+b
"""
    with pytest.raises(ValueError, match="escape"):
        parse_patch(outside)

    git_path = """diff --git a/.git/config b/.git/config
--- a/.git/config
+++ b/.git/config
@@ -1 +1 @@
-a
+b
"""
    with pytest.raises(ValueError, match="\\.git"):
        parse_patch(git_path)

    rename = """diff --git a/old.txt b/new.txt
similarity index 100%
rename from old.txt
rename to new.txt
"""
    with pytest.raises(ValueError, match="rename"):
        parse_patch(rename)


def test_patch_tool_rejects_binary_and_malformed_hunks():
    binary = """diff --git a/logo.png b/logo.png
Binary files a/logo.png and b/logo.png differ
"""
    with pytest.raises(ValueError, match="binary"):
        parse_patch(binary)

    malformed = """diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1,5 +1,5 @@
-a
+b
"""
    with pytest.raises(ValueError, match="line count mismatch"):
        parse_patch(malformed)


def test_patch_preview_lists_files_and_marks_write_risk(tmp_path):
    (tmp_path / "file.txt").write_text("a\nb\nc\nd\ne\nf\n")
    registry = ToolRegistry(tmp_path)
    preview = registry.preview(ToolCall("apply_patch", {"patch": MODIFY_PATCH}))
    assert "apply_patch · 1 file(s), +2 -2" in preview
    assert "[risk] write" in preview
    with pytest.raises(ValueError, match="unexpected patch line|context mismatch|line count"):
        registry.preview(ToolCall("apply_patch", {"patch": "not a patch"}))


def test_patch_tool_requires_fresh_read_and_handles_content_starting_with_plus(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("a\n")
    registry = ToolRegistry(tmp_path)
    patch = """diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1,2 @@
 a
+++ added
"""
    registry.read_file("file.txt")
    registry.apply_patch(patch)
    assert target.read_text() == "a\n++ added\n"

    target.write_text("changed externally\n")
    with pytest.raises(ValueError, match="file changed since last read"):
        registry.apply_patch("""diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-changed externally
+changed again
""")
    assert target.read_text() == "changed externally\n"


def test_patch_tool_verifies_delete_hunks_and_rejects_duplicate_targets(tmp_path):
    target = tmp_path / "old.txt"
    target.write_text("one\ntwo\n")
    bad_delete = """diff --git a/old.txt b/old.txt
deleted file mode 100644
--- a/old.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-other
-two
"""
    with pytest.raises(ValueError, match="context mismatch"):
        ToolRegistry(tmp_path).apply_patch(bad_delete)
    assert target.read_text() == "one\ntwo\n"

    duplicate = """diff --git a/old.txt b/old.txt
--- a/old.txt
+++ b/old.txt
@@ -1 +1 @@
-one
+ONE
diff --git a/old.txt b/old.txt
--- a/old.txt
+++ b/old.txt
@@ -2 +2 @@
-two
+TWO
"""
    with pytest.raises(ValueError, match="twice"):
        ToolRegistry(tmp_path).apply_patch(duplicate)
    assert target.read_text() == "one\ntwo\n"
