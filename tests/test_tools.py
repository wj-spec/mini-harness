from core.context import ContextBuilder
from core.models import Message, ToolCall
from core.state import TaskState
from app.tools import ToolRegistry


def test_read_file(tmp_path):
    (tmp_path / "a.txt").write_text("hello\nworld\n")
    registry = ToolRegistry(tmp_path)

    output = registry.read_file("a.txt")
    assert "hello" in output
    assert "world" in output
    assert "1" in output

    ok, result = registry.execute(ToolCall("read_file", {"path": "a.txt"}))
    assert ok is True
    assert "world" in result


def test_path_escape_is_rejected(tmp_path):
    registry = ToolRegistry(tmp_path)
    ok, output = registry.execute(ToolCall("read_file", {"path": "../etc/passwd"}))
    assert ok is False
    assert "outside workspace" in output


def test_list_files(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y")

    output = ToolRegistry(tmp_path).list_files()
    assert "a.txt" in output
    assert "sub/" in output
    assert "sub/b.py" in output


def test_unknown_tool(tmp_path):
    ok, output = ToolRegistry(tmp_path).execute(ToolCall("nope", {}))
    assert ok is False
    assert "unknown_tool" in output


def test_grep_finds_match(tmp_path):
    (tmp_path / "a.py").write_text("def hello():\n    return 1\n")
    ok, output = ToolRegistry(tmp_path).execute(ToolCall("grep", {"pattern": "hello"}))
    assert ok is True
    assert "a.py" in output
    assert "hello" in output


def test_grep_no_match(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    ok, output = ToolRegistry(tmp_path).execute(ToolCall("grep", {"pattern": "nope"}))
    assert ok is True
    assert output == "(no matches)"


def test_grep_path_escape(tmp_path):
    ok, output = ToolRegistry(tmp_path).execute(
        ToolCall("grep", {"pattern": "root", "path": "../"})
    )
    assert ok is False
    assert "outside workspace" in output


def test_recall_tool(tmp_path):
    builder = ContextBuilder(
        max_tokens=40, reserve_output=5, keep_recent_groups=1,
        offload_dir=tmp_path / "offload",
    )
    messages = [
        Message("system", "sys"),
        Message("user", "q"),
        Message("assistant", "", [ToolCall("read_file", {"path": "a.txt"})]),
        Message("tool", "x" * 200, name="read_file"),
        Message("assistant", "done"),
    ]
    result = builder.build(messages)
    assert result.offloaded

    registry = ToolRegistry(tmp_path, context=builder)
    ok, output = registry.execute(ToolCall("recall", {"ref": result.offloaded[0]}))
    assert ok is True
    assert "x" * 20 in output


def test_recall_unknown_ref(tmp_path):
    builder = ContextBuilder()
    registry = ToolRegistry(tmp_path, context=builder)
    ok, output = registry.execute(ToolCall("recall", {"ref": "off-nope"}))
    assert ok is False
    assert "unknown ref" in output


def test_update_task_tool(tmp_path):
    state = TaskState(goal="g")
    registry = ToolRegistry(tmp_path, state=state)
    ok, output = registry.execute(
        ToolCall(
            "update_task",
            {"plan": ["a"], "done": ["b"], "open": ["c"], "note": "n"},
        )
    )
    assert ok is True
    assert state.plan == ["a"]
    assert state.done == ["b"]
    assert state.open == ["c"]
    assert "Plan:" in output


def test_recall_only_registered_with_context(tmp_path):
    assert "recall" not in ToolRegistry(tmp_path).names()
    assert "recall" in ToolRegistry(tmp_path, context=ContextBuilder()).names()


def test_write_file_and_backup(tmp_path):
    registry = ToolRegistry(tmp_path)
    ok, output = registry.execute(
        ToolCall("write_file", {"path": "a.txt", "content": "hello"})
    )
    assert ok is True
    assert (tmp_path / "a.txt").read_text() == "hello"

    ok, output = registry.execute(
        ToolCall("write_file", {"path": "a.txt", "content": "bye"})
    )
    assert ok is True
    assert (tmp_path / "a.txt").read_text() == "bye"
    assert list((tmp_path / ".vibe" / "backups").iterdir())


def test_edit_file_requires_unique_match(tmp_path):
    (tmp_path / "a.txt").write_text("foo bar foo\n")
    registry = ToolRegistry(tmp_path)

    ok, output = registry.execute(
        ToolCall("edit_file", {"path": "a.txt", "old": "bar", "new": "baz"})
    )
    assert ok is True
    assert (tmp_path / "a.txt").read_text() == "foo baz foo\n"

    ok, output = registry.execute(
        ToolCall("edit_file", {"path": "a.txt", "old": "foo", "new": "x"})
    )
    assert ok is False
    assert "exactly once" in output


def test_write_path_escape_is_rejected(tmp_path):
    registry = ToolRegistry(tmp_path)
    ok, output = registry.execute(
        ToolCall("write_file", {"path": "../x", "content": "y"})
    )
    assert ok is False
    assert "outside workspace" in output


def test_preview_shows_unified_diff(tmp_path):
    (tmp_path / "a.txt").write_text("old\n")
    registry = ToolRegistry(tmp_path)
    preview = registry.preview(
        ToolCall("edit_file", {"path": "a.txt", "old": "old", "new": "new"})
    )
    assert "-old" in preview
    assert "+new" in preview


def test_write_tools_have_write_risk(tmp_path):
    registry = ToolRegistry(tmp_path)
    assert registry.risk("read_file") == "read"
    assert registry.risk("write_file") == "write"
    assert registry.risk("edit_file") == "write"


def test_bash_echo(tmp_path):
    ok, output = ToolRegistry(tmp_path).execute(
        ToolCall("bash", {"command": "echo hi"})
    )
    assert ok is True
    assert "hi" in output
    assert "exit=0" in output


def test_bash_timeout(tmp_path):
    ok, output = ToolRegistry(tmp_path).execute(
        ToolCall("bash", {"command": "sleep 1", "timeout": 0.1})
    )
    assert ok is True
    assert "timeout" in output.lower()


def test_bash_dangerous_command_is_blocked(tmp_path):
    ok, output = ToolRegistry(tmp_path).execute(
        ToolCall("bash", {"command": "rm -rf /"})
    )
    assert ok is False
    assert "blocked dangerous" in output


def test_bash_cwd_escape_is_rejected(tmp_path):
    ok, output = ToolRegistry(tmp_path).execute(
        ToolCall("bash", {"command": "pwd", "cwd": "../"})
    )
    assert ok is False
    assert "outside workspace" in output


def test_bash_output_is_truncated(tmp_path):
    ok, output = ToolRegistry(tmp_path).execute(
        ToolCall("bash", {"command": "python3 -c \"print('x'*30000)\""})
    )
    assert ok is True
    assert "[truncated]" in output
    assert len(output) < 12000


def test_edit_file_rejects_stale_read(tmp_path):
    (tmp_path / "a.txt").write_text("old")
    registry = ToolRegistry(tmp_path)
    registry.read_file("a.txt")
    (tmp_path / "a.txt").write_text("external")

    ok, output = registry.execute(
        ToolCall("edit_file", {"path": "a.txt", "old": "external", "new": "x"})
    )
    assert ok is False
    assert "changed since last read" in output


def test_edit_file_after_fresh_read(tmp_path):
    (tmp_path / "a.txt").write_text("old")
    registry = ToolRegistry(tmp_path)
    registry.read_file("a.txt")

    ok, output = registry.execute(
        ToolCall("edit_file", {"path": "a.txt", "old": "old", "new": "new"})
    )
    assert ok is True
    assert (tmp_path / "a.txt").read_text() == "new"


def test_write_file_rejects_stale_read(tmp_path):
    (tmp_path / "a.txt").write_text("old")
    registry = ToolRegistry(tmp_path)
    registry.read_file("a.txt")
    (tmp_path / "a.txt").write_text("external")

    ok, output = registry.execute(
        ToolCall("write_file", {"path": "a.txt", "content": "overwrite"})
    )
    assert ok is False
    assert "changed since last read" in output
