import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_stdio import Harness, agent_for  # noqa: E402

from app.config import Config  # noqa: E402
from app.session import SessionStore  # noqa: E402
from core.models import ToolCall  # noqa: E402


def test_bench_cancelled_run_counts_every_returned_tool(tmp_path, monkeypatch):
    async def fake_run(text, **callbacks):
        callbacks["on_result"](ToolCall("read_file", {"path": "a.txt"}, id="c1"), True, "ok")
        callbacks["on_result"](ToolCall("read_file", {"path": "b.txt"}, id="c2"), True, "ok")
        raise asyncio.CancelledError

    agent = agent_for(tmp_path, [])
    monkeypatch.setattr(agent, "run", fake_run)
    session = SessionStore.new(tmp_path, "fake", purpose="test")

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session=session)
        harness.send("run", "r1", text="go")
        done = await harness.until("done", "r1")
        assert done["result"]["status"] == "cancelled"
        await harness.close()

    asyncio.run(scenario())
    events = [json.loads(line) for line in session.trace_path.read_text().splitlines()]
    ending = next(event for event in events if event["type"] == "run_end")
    assert ending["tool_calls"] == 2
