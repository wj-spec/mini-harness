from core.models import Message, ToolCall
from core.summarize import summarize_messages


def test_summary_keeps_tool_names_and_errors():
    summary = summarize_messages(
        [
            Message("user", "fix bug"),
            Message("assistant", "", [ToolCall("read_file", {"path": "a.py"})]),
            Message("tool", "ERROR ValueError: bad", name="read_file"),
        ]
    )
    assert "read_file" in summary
    assert "ERROR" in summary
    assert "fix bug" in summary


def test_summary_truncates():
    summary = summarize_messages([Message("user", "x" * 10000)], max_chars=100)
    assert len(summary) <= 160
    assert "[summary truncated]" in summary


