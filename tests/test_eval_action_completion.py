import json
from pathlib import Path

import httpx

from app.config import Config
from app.eval import EvalTask, run_eval
from app.providers.fake import FakeProvider
from app.providers.openai_compat import OpenAICompatProvider
from core.models import Response, ToolCall


def test_eval_continues_after_announcing_action_before_any_tool_was_called(tmp_path):
    task = EvalTask("create_file", "Create hello.txt containing exactly: hello world",
                    checks=[{"type": "file_equals", "path": "hello.txt", "text": "hello world"}])
    provider = FakeProvider([
        Response("I'll create the file.", usage={"total_tokens": 1111}),
        Response(tool_calls=[ToolCall("write_file", {"path": "hello.txt", "content": "hello world"}, "write")],
                 usage={"total_tokens": 200}),
        Response("Created hello.txt.", usage={"total_tokens": 100}),
    ])

    report = run_eval([task], lambda _: provider, tmp_path, config=Config())

    assert report.passed == 1
    result = report.results[0]
    assert Path(result.workspace, "hello.txt").read_text() == "hello world"
    assert result.usage["total_tokens"] == 1411
    audits = [json.loads(line) for line in Path(result.workspace, ".vibe/audit.jsonl").read_text().splitlines()]
    assert any(event["tool"] == "write_file" and event["decision"] == "once" for event in audits)


def test_captured_http_stop_response_gets_one_chance_to_execute_action(tmp_path):
    seen = []

    def handler(request):
        payload = json.loads(request.content)
        seen.append(payload)
        if len(seen) == 1:
            delta = {"content": "I'll create the file.", "reasoning_content": ""}
            finish = "stop"
            usage = {"prompt_tokens": 1104, "completion_tokens": 7, "total_tokens": 1111}
        elif len(seen) == 2:
            delta = {"tool_calls": [{"index": 0, "id": "write", "function": {
                "name": "write_file", "arguments": json.dumps({"path": "hello.txt", "content": "hello world"}),
            }}]}
            finish = "tool_calls"
            usage = {"total_tokens": 200}
        else:
            delta, finish, usage = {"content": "Created hello.txt."}, "stop", {"total_tokens": 100}
        chunk = {"choices": [{"delta": delta, "finish_reason": finish}], "usage": usage}
        return httpx.Response(200, text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n")

    provider = OpenAICompatProvider("deepseek-flash", "fixture", transport=httpx.MockTransport(handler))
    task = EvalTask("create_file", "Create hello.txt containing exactly: hello world",
                    checks=[{"type": "file_equals", "path": "hello.txt", "text": "hello world"}])
    report = run_eval([task], lambda _: provider, tmp_path, config=Config())

    assert report.passed == 1
    assert len(seen) == 3
    assert report.results[0].steps == 3
    assert any(message["content"] == "I'll create the file." for message in seen[1]["messages"])
    assert report.metadata["agent"]["require_tool_use"] is True


def test_grading_failure_does_not_feed_checker_answers_back_into_agent(tmp_path):
    task = EvalTask("create_file", "Create hello.txt containing exactly: hello world",
                    checks=[{"type": "file_equals", "path": "hello.txt", "text": "hello world"}])
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("write_file", {"path": "hello.txt", "content": "wrong"}, "write")]),
        "done",
        Response(tool_calls=[ToolCall("write_file", {"path": "hello.txt", "content": "hello world"}, "repair")]),
    ])

    report = run_eval([task], lambda _: provider, tmp_path, config=Config())

    assert report.passed == 0
    assert len(provider.seen) == 2
    assert report.results[0].status == "done"
    assert Path(report.results[0].workspace, "hello.txt").read_text() == "wrong"
