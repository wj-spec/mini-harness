from __future__ import annotations

import asyncio
import json

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


def test_subagent_creates_child_task_record(tmp_path):
    from app.runtime.tasks import TaskStore
    from app.session.store import SessionStore

    session = SessionStore.new(tmp_path, "fake", purpose="test")
    parent = TaskStore(session.root).create(
        kind="goal", objective="parent", workspace=str(tmp_path), mode="goal",
    )
    provider = FakeProvider(["子任务完成"])
    result = asyncio.run(run_subagent(
        provider, tmp_path, "只读探索", session_root=session.root,
        parent_task_id=parent.task_id,
    ))
    child = TaskStore(session.root).get(result.task_id)
    assert child.kind == "subagent" and child.parent_id == parent.task_id
    assert child.status == "completed"
    task_dir = TaskStore(session.root).task_dir(child.task_id)
    saved = SubagentResult.from_dict(json.loads((task_dir / "subagent-result.json").read_text(encoding="utf-8")))
    assert saved.status == "ok" and saved.task_id == child.task_id
    assert not (task_dir / "lock").exists()


def test_subagent_usage_is_recorded_in_parent_ledger(tmp_path):
    from app.runtime.tasks import TaskStore
    from app.session.store import SessionStore

    session = SessionStore.new(tmp_path, "fake", purpose="test")
    parent = TaskStore(session.root).create(
        kind="goal", objective="parent", workspace=str(tmp_path), mode="goal",
    )
    provider = FakeProvider([
        Response(content="完成", usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
    ])
    result = asyncio.run(run_subagent(
        provider, tmp_path, "短任务", session_root=session.root,
        parent_task_id=parent.task_id,
    ))
    assert result.usage.get("total_tokens") == 15
    ledger = (session.root / "budget.jsonl").read_text(encoding="utf-8")
    assert '"node_kind": "subagent"' in ledger
    assert f'"parent_id": "{parent.task_id}"' in ledger


def test_subagent_depth_limit_and_parent_cancel(tmp_path, monkeypatch):
    import asyncio
    from app.runtime.tasks import TaskStore
    from app.session.store import SessionStore

    monkeypatch.setenv("VIBE_FAKE_DELAY_SECONDS", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    parent = TaskStore(session.root).create(
        kind="goal", objective="parent", workspace=str(tmp_path), mode="goal",
    )
    with pytest.raises(SubagentError):
        asyncio.run(run_subagent(FakeProvider(["x"]), tmp_path, "x", depth=2, max_depth=1))

    async def scenario():
        parent_cancel = asyncio.Event()

        async def set_cancel():
            await asyncio.sleep(0.1)
            parent_cancel.set()

        setter = asyncio.create_task(set_cancel())
        result = await run_subagent(
            FakeProvider(["慢任务"]), tmp_path, "慢任务",
            parent_cancel=parent_cancel, session_root=session.root,
            parent_task_id=parent.task_id,
        )
        await setter
        assert result.status == "cancelled"
        child = TaskStore(session.root).get(result.task_id)
        assert child.status == "cancelled"

    asyncio.run(scenario())


def test_subagent_batch_limits_and_results(tmp_path):
    from app.runtime.subagents import SubagentRequest, run_subagent_batch

    requests = [
        SubagentRequest(FakeProvider([f"r{i}"]), tmp_path, f"任务 {i}")
        for i in range(2)
    ]
    results = asyncio.run(run_subagent_batch(requests, max_concurrency=2, max_children=4))
    assert len(results) == 2 and all(item.status == "ok" for item in results)

    too_many = [
        SubagentRequest(FakeProvider(["x"]), tmp_path, f"任务 {i}")
        for i in range(5)
    ]
    with pytest.raises(SubagentError):
        asyncio.run(run_subagent_batch(too_many, max_children=4))


def test_subagent_limits_from_env(tmp_path, monkeypatch):
    from app.runtime.subagents import SubagentRequest, run_subagent_batch

    monkeypatch.setenv("VIBE_SUBAGENT_MAX_DEPTH", "0")
    with pytest.raises(SubagentError, match="exceeds max_depth 0"):
        asyncio.run(run_subagent(FakeProvider(["x"]), tmp_path, "x", depth=1))
    monkeypatch.setenv("VIBE_SUBAGENT_MAX_CHILDREN", "1")
    requests = [SubagentRequest(FakeProvider(["x"]), tmp_path, "x")]
    assert len(asyncio.run(run_subagent_batch(requests))) == 1
    with pytest.raises(SubagentError, match="exceeds max_children 1"):
        asyncio.run(run_subagent_batch(
            [SubagentRequest(FakeProvider(["x"]), tmp_path, "x"),
             SubagentRequest(FakeProvider(["x"]), tmp_path, "y")]
        ))
