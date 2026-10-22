import asyncio

from app.agent import Agent
from app.events import StreamDone, StreamError, TextDelta, ToolCallsReady
from app.tools import ToolRegistry
from core.models import ToolCall


def test_budget_stop_preserves_simultaneous_model_error_as_structured_outcome(tmp_path):
    class Provider:
        async def stream(self, messages, tools):
            yield StreamError("HTTP 400: protocol failed")
            yield StreamDone(usage={"total_tokens": 10000})

    result = asyncio.run(Agent(Provider(), ToolRegistry(tmp_path), max_run_tokens=10000).run("inspect"))

    assert result.status == "stopped"
    assert result.stop_reason == "token_budget"
    assert result.error == "HTTP 400: protocol failed"


def test_successful_retry_clears_model_error_from_final_result(tmp_path):
    class Provider:
        calls = 0

        async def stream(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                yield StreamError("HTTP 503", retryable=True)
            else:
                yield TextDelta("done")
            yield StreamDone(usage={"total_tokens": 100})

    result = asyncio.run(Agent(Provider(), ToolRegistry(tmp_path), llm_retry_backoff=0).run("inspect"))

    assert result.status == "done"
    assert result.error is None


def test_checkpoint_model_error_remains_visible_alongside_budget_pause(tmp_path):
    class Provider:
        calls = 0

        async def stream(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                yield ToolCallsReady([ToolCall("list_files", {}, "list")])
                yield StreamDone(usage={"total_tokens": 9500})
            else:
                yield StreamError("HTTP 400: checkpoint failed")

    result = asyncio.run(Agent(Provider(), ToolRegistry(tmp_path), max_run_tokens=10000).run("inspect"))

    assert result.status == "stopped"
    assert result.stop_reason == "token_budget"
    assert result.error == "HTTP 400: checkpoint failed"
