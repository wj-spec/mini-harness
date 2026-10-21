import asyncio
import json

from app.agent import Agent
from app.config import Config
from app.context_snapshot_display import snapshot_for_display
from app.providers.fake import FakeProvider
from app.session import SessionStore
from app.stdio import MAX_FRAME_BYTES
from app.tools import ToolRegistry
from core.budget import BudgetPolicy
from core.context import ContextBuilder
from core.context_snapshot import build_context_snapshot
from core.models import Message
from test_stdio import Harness


def test_many_blocks_are_bounded_without_losing_partition_totals_or_mutating_trace():
    snapshot = build_context_snapshot([Message("user", "x") for _ in range(5000)], [])
    display = snapshot_for_display(snapshot)

    assert len(snapshot["blocks"]) == 5001
    assert len(display["blocks"]) <= 256
    assert display["sections"] == snapshot["sections"]
    for section, count in snapshot["sections"].items():
        assert sum(block["tokens"] for block in display["blocks"] if block["section"] == section) == count
    aggregates = [block for block in display["blocks"] if block["kind"] == "aggregate"]
    assert aggregates and "trace.jsonl" in aggregates[0]["label"]
    assert snapshot_for_display(snapshot) == display


def test_large_contributors_remain_visible():
    snapshot = build_context_snapshot(
        [*[Message("user", "x") for _ in range(300)], Message("tool", "很大的文件" * 1000, name="read_file")], [],
    )
    display = snapshot_for_display(snapshot)
    assert any(block["kind"] == "tool_result" and block["tokens"] == 5000 for block in display["blocks"])
    assert snapshot_for_display(display) == display


def test_large_snapshot_does_not_cancel_a_small_model_request(tmp_path):
    async def scenario():
        session = SessionStore.new(tmp_path, "fake")
        agent = Agent(FakeProvider(["ok"]), ToolRegistry(tmp_path),
                      history=[Message("user", "x") for _ in range(5000)],
                      context=ContextBuilder(policy=BudgetPolicy()))
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            harness.send("run", "many-blocks", text="继续")
            done = await harness.until("done", "many-blocks")
            assert done["result"]["status"] == "done"
            event = next(event for event in harness.events if event["type"] == "context_snapshot")
            assert len(event["snapshot"]["blocks"]) <= 256
            assert any(block["kind"] == "aggregate" for block in event["snapshot"]["blocks"])
            assert all(len(json.dumps(frame, ensure_ascii=False).encode()) <= MAX_FRAME_BYTES
                       for frame in harness.events)
            trace = [json.loads(line) for line in session.trace_path.read_text().splitlines()]
            original = next(event for event in trace if event["type"] == "context_snapshot")
            assert len(original["blocks"]) > 5000
            assert event["snapshot"]["sections"] == original["sections"]
        finally:
            await harness.close()

    asyncio.run(scenario())
