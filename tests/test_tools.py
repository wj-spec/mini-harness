import pytest

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


def test_grep_option_style_pattern_is_treated_as_pattern(tmp_path):
    (tmp_path / "a.txt").write_text("--files\n-n\n")
    ok, output = ToolRegistry(tmp_path).execute(ToolCall("grep", {"pattern": "--files"}))
    assert ok is True
    assert "a.txt:1:--files" in output


def test_grep_option_style_pattern_is_treated_as_pattern_without_rg(tmp_path, monkeypatch):
    monkeypatch.setattr("app.tools.shutil.which", lambda _: None)
    (tmp_path / "a.txt").write_text("--files\n-n\n")
    ok, output = ToolRegistry(tmp_path).execute(ToolCall("grep", {"pattern": "-n"}))
    assert ok is True
    assert "a.txt:2:-n" in output


def test_grep_path_starting_with_dash_is_searched(tmp_path):
    (tmp_path / "-evil").mkdir()
    (tmp_path / "-evil" / "a.txt").write_text("needle\n")
    ok, output = ToolRegistry(tmp_path).execute(ToolCall("grep", {"pattern": "needle", "path": "-evil"}))
    assert ok is True
    assert "-evil/a.txt" in output


def test_grep_skips_out_of_workspace_symlink(tmp_path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("root:x:0:0\n")
    (tmp_path / "link.txt").symlink_to(outside)
    for rg_available in (True, False):
        if not rg_available:
            import app.tools as tools_module
            original = tools_module.shutil.which
            tools_module.shutil.which = lambda _: None
        try:
            ok, output = ToolRegistry(tmp_path).execute(ToolCall("grep", {"pattern": "root:x"}))
        finally:
            if not rg_available:
                tools_module.shutil.which = original
        assert ok is True
        assert "root:x" not in output, f"rg_available={rg_available} leaked symlink content"


def test_grep_invalid_regex_is_error_not_no_match(tmp_path):
    (tmp_path / "a.txt").write_text("x\n")
    for rg_available in (True, False):
        import app.tools as tools_module
        original = tools_module.shutil.which
        if not rg_available:
            tools_module.shutil.which = lambda _: None
        try:
            ok, output = ToolRegistry(tmp_path).execute(ToolCall("grep", {"pattern": "["}))
        finally:
            tools_module.shutil.which = original
        assert ok is False, f"rg_available={rg_available}"
        assert output != "(no matches)"
        assert "invalid regex" in output


def test_atomic_write_ignores_preplanted_temp_symlink(tmp_path):
    outside = tmp_path.parent / "outside-keep.txt"
    outside.write_text("keep\n")
    (tmp_path / "f.txt.tmp").symlink_to(outside)
    registry = ToolRegistry(tmp_path)
    ok, output = registry.execute(ToolCall("write_file", {"path": "f.txt", "content": "new\n"}))
    assert ok is True
    assert (tmp_path / "f.txt").read_text() == "new\n"
    assert outside.read_text() == "keep\n"
    assert (tmp_path / "f.txt.tmp").is_symlink()


def test_atomic_write_leaves_no_temp_files(tmp_path):
    registry = ToolRegistry(tmp_path)
    ok, _ = registry.execute(ToolCall("write_file", {"path": "f.txt", "content": "one\n"}))
    assert ok is True
    ok, _ = registry.execute(ToolCall("write_file", {"path": "f.txt", "content": "two\n"}))
    assert ok is True
    assert sorted(p.name for p in tmp_path.iterdir()) == [".vibe", "f.txt"]
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_write_via_out_of_workspace_symlink_is_rejected(tmp_path):
    outside = tmp_path.parent / "outside-target.txt"
    outside.write_text("keep\n")
    (tmp_path / "link.txt").symlink_to(outside)
    ok, output = ToolRegistry(tmp_path).execute(ToolCall("write_file", {"path": "link.txt", "content": "evil\n"}))
    assert ok is False
    assert "outside workspace" in output
    assert outside.read_text() == "keep\n"


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
    assert ok is False
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


def test_async_tool_runs_on_callers_event_loop(tmp_path):
    import asyncio
    from app.tools import ToolSpec

    async def run():
        loop = asyncio.get_running_loop()

        async def echo(text):
            assert asyncio.get_running_loop() is loop
            await asyncio.sleep(0)
            return text

        registry = ToolRegistry(tmp_path)
        registry.register(ToolSpec("remote", "", {}, echo))
        assert await registry.execute_async(ToolCall("remote", {"text": "你好"})) == (True, "你好")
        ok, output = registry.execute(ToolCall("remote", {"text": "你好"}))
        assert not ok and "execute_async" in output

    asyncio.run(run())


def test_async_tool_timeout_cancels_operation(tmp_path):
    import asyncio
    from app.tools import ToolSpec

    async def run():
        cancelled = asyncio.Event()

        async def hang():
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        registry = ToolRegistry(tmp_path)
        registry.register(ToolSpec("remote", "", {}, hang, timeout=0.02))
        ok, output = await registry.execute_async(ToolCall("remote", {}))
        assert not ok and "timeout" in output
        assert cancelled.is_set()

    asyncio.run(run())


def test_async_tool_cancel_event_stops_pending_operation(tmp_path):
    import asyncio
    from app.tools import ToolSpec

    async def run():
        entered, stopped, cancel = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def hang():
            entered.set()
            try:
                await asyncio.sleep(10)
            finally:
                stopped.set()

        registry = ToolRegistry(tmp_path)
        registry.register(ToolSpec("remote", "", {}, hang))
        pending = asyncio.create_task(registry.execute_async(ToolCall("remote", {}), cancel=cancel))
        await asyncio.wait_for(entered.wait(), 0.2)
        cancel.set()
        ok, output = await asyncio.wait_for(pending, 0.2)
        assert not ok and "cancelled" in output and "unknown" in output
        assert stopped.is_set()

    asyncio.run(run())


def _recall_registry(tmp_path):
    workspace = tmp_path / "work"
    offload = workspace / ".vibe" / "offload"
    offload.mkdir(parents=True)
    registry = ToolRegistry(workspace, context=ContextBuilder(offload_dir=offload))
    return workspace, offload, registry


def test_recall_rejects_path_traversal_outside_workspace(tmp_path):
    workspace, offload, registry = _recall_registry(tmp_path)
    (tmp_path / "secret.json").write_text('[{"role": "user", "content": "private"}]')

    with pytest.raises(ValueError, match="invalid offload reference"):
        registry.recall("../../../secret")

    ok, output = registry.execute(ToolCall("recall", {"ref": "../../../secret"}))
    assert not ok and "invalid" in output and "private" not in output


def test_recall_rejects_invalid_reference_format(tmp_path):
    _, _, registry = _recall_registry(tmp_path)
    with pytest.raises(ValueError, match="invalid offload reference"):
        registry.recall("off-ZZZZZZZZ")
    with pytest.raises(ValueError, match="invalid offload reference"):
        registry.recall("../off-12345678")


def test_recall_still_resolves_saved_offload_file(tmp_path):
    workspace, offload, registry = _recall_registry(tmp_path)
    (offload / "off-12345678.json").write_text(
        '[{"role": "user", "content": "original request"}]', encoding="utf-8",
    )

    output = registry.recall("off-12345678")

    assert "original request" in output
