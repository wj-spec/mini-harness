import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))

from test_provider import _collect, _handler, _tool_call_chunk, sse  # noqa: E402

from app.events import ToolCallsReady  # noqa: E402
from app.providers.openai_compat import OpenAICompatProvider  # noqa: E402


def _provider(body):
    return OpenAICompatProvider("m", "k", "http://example/v1", transport=httpx.MockTransport(_handler(body)))


def test_bench_mixed_batch_keeps_only_complete_calls():
    body = sse([
        _tool_call_chunk(0, "c1", "read_file", '{"path": "a.txt"}'),
        _tool_call_chunk(1, "c2", "write_file", '{"path": "b.txt"'),
        _tool_call_chunk(2, "c3", "list_files", "{}"),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ])
    ready = [e for e in _collect(_provider(body)) if isinstance(e, ToolCallsReady)][0]
    assert [c.id for c in ready.calls] == ["c1", "c3"]
    assert [c.id for c in ready.incomplete] == ["c2"]


def test_bench_empty_name_is_incomplete():
    body = sse([
        _tool_call_chunk(0, "c1", "", '{"path": "a.txt"}'),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ])
    ready = [e for e in _collect(_provider(body)) if isinstance(e, ToolCallsReady)][0]
    assert ready.calls == []
    assert [c.id for c in ready.incomplete] == ["c1"]


def test_bench_stream_without_terminator_never_executes_calls():
    body = sse([
        _tool_call_chunk(0, "c1", "read_file", '{"path": "a.txt"}'),
    ], done=False)
    events = _collect(_provider(body))
    ready = [e for e in events if isinstance(e, ToolCallsReady)][0]
    assert ready.calls == []
    assert [c.id for c in ready.incomplete] == ["c1"]
    done = [e for e in events if type(e).__name__ == "StreamDone"][-1]
    assert done.finish_reason == "truncated"


def test_bench_missing_done_but_explicit_finish_reason_is_complete():
    body = sse([
        _tool_call_chunk(0, "c1", "read_file", '{"path": "a.txt"}'),
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ], done=False)
    ready = [e for e in _collect(_provider(body)) if isinstance(e, ToolCallsReady)][0]
    assert [c.id for c in ready.calls] == ["c1"]
    assert ready.incomplete == []
