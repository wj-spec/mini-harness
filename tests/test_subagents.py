from __future__ import annotations

import asyncio

import pytest

from app.providers.fake import FakeProvider
from app.runtime.subagents import SubagentResult, SubagentError, run_subagent
from core.contracts import Response, ToolCall


def test_readonly_subagent_returns_structured_result(tmp_path):
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("read_file", {"path": "README.md"}, id="r1")]),
        "探索完成：README 存在。",
    ])
    result = asyncio.run(run_subagent(provider, tmp_path, "读 README"))
    assert result.status == "ok"
    assert "README" in result.summary
    assert SubagentResult.from_dict(result.to_dict()).status == "ok"


def test_subagent_cannot_write_or_execute(tmp_path):
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("write_file", {"path": "x.txt", "content": "x"}, id="w1")]),
        Response(tool_calls=[ToolCall("bash", {"command": "touch y.txt"}, id="b1")]),
        "完成",
    ])
    result = asyncio.run(run_subagent(provider, tmp_path, "尝试写"))
    assert not (tmp_path / "x.txt").exists()
    assert not (tmp_path / "y.txt").exists()
    assert result.status == "ok"


def test_subagent_result_schema_rejects_bad_status():
    with pytest.raises(SubagentError):
        SubagentResult.from_dict({"schema_version": 1, "status": "bogus"})
