import asyncio

from app.agent import Agent
from app.tools import ToolRegistry
from app.trace import Pricing, TraceRecorder
from app.providers.fake import FakeProvider
from core.models import Response


def run(coro):
    return asyncio.run(coro)


def test_pricing_cost():
    pricing = Pricing(input_per_1m=1.0, output_per_1m=2.0)
    assert pricing.cost(1_000_000, 0) == 1.0
    assert pricing.cost(0, 1_000_000) == 2.0
    assert pricing.cost(500_000, 500_000) == 1.5


def test_trace_recorder_aggregates_and_emits():
    events = []
    recorder = TraceRecorder(emit=events.append, pricing=Pricing(1.0, 2.0), model="m")
    recorder.start("task")
    recorder.llm_start(1)
    recorder.llm_end(1, {"prompt_tokens": 10, "completion_tokens": 5}, "stop")
    recorder.end("done", 1)

    assert recorder.usage() == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert recorder.cost_usd == 10 / 1_000_000 + 10 / 1_000_000
    assert [e["type"] for e in events] == ["run_start", "llm_start", "llm_end", "run_end"]


def test_agent_records_usage_and_cost(tmp_path):
    provider = FakeProvider(
        [
            Response(
                content="ok",
                usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            )
        ]
    )
    agent = Agent(provider, ToolRegistry(tmp_path), pricing=Pricing(1.0, 2.0))
    events = []

    result = run(agent.run("q", trace=events.append))

    assert result.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    assert result.cost_usd == 20 / 1_000_000
    assert result.run_id
    assert any(e["type"] == "llm_end" and e["total_tokens"] == 15 for e in events)
    assert any(e["type"] == "run_end" and e["status"] == "done" for e in events)
