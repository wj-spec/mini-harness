import asyncio

from core.models import Message, Response, ToolCall
from app.agent import Agent
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry, ToolSpec
from core.state import TaskState
from app.policy import ApprovalDecision, ApprovalGate
from app.events import StreamDone, StreamError, TextDelta, ToolCallsReady


def run(coro):
    return asyncio.run(coro)


def test_text_only(tmp_path):
    provider = FakeProvider(["hi"])
    agent = Agent(provider, ToolRegistry(tmp_path))
    result = run(agent.run("q"))
    assert (result.text, result.steps, result.status) == ("hi", 1, "done")
    user_messages = [m for m in provider.seen[0] if m.role == "user"]
    assert [m.content for m in user_messages] == ["q"]


def test_tool_call_then_answer(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    provider = FakeProvider(
        [
            Response(tool_calls=[ToolCall("read_file", {"path": "a.txt"}, id="c1")]),
            Response(content="read it"),
        ]
    )
    agent = Agent(provider, ToolRegistry(tmp_path))

    result = run(agent.run("q"))

    assert (result.text, result.steps, result.status) == ("read it", 2, "done")
    history = [m for m in provider.seen[1] if m.role != "system"]
    assert [m.role for m in history] == ["user", "assistant", "tool"]
    assert history[1].tool_calls[0].id == "c1"
    tool_messages = [m for m in provider.seen[1] if m.role == "tool"]
    assert tool_messages
    assert tool_messages[0].tool_call_id == "c1"
    assert "hello" in tool_messages[0].content


def test_unknown_tool_recovers(tmp_path):
    provider = FakeProvider(
        [
            Response(tool_calls=[ToolCall("nope", {}, id="c1")]),
            Response(content="recovered"),
        ]
    )
    agent = Agent(provider, ToolRegistry(tmp_path))
    result = run(agent.run("q"))
    assert (result.text, result.status) == ("recovered", "done")


def test_tool_error_recovers(tmp_path):
    def boom():
        raise ValueError("bad input")

    registry = ToolRegistry(tmp_path)
    registry.register(
        ToolSpec("boom", "failing tool", {"type": "object", "properties": {}}, boom)
    )
    provider = FakeProvider(
        [
            Response(tool_calls=[ToolCall("boom", {}, id="c1")]),
            Response(content="recovered"),
        ]
    )
    agent = Agent(provider, registry)
    results = []

    result = run(
        agent.run("q", on_result=lambda call, ok, output: results.append((ok, output)))
    )

    assert (result.text, result.steps, result.status) == ("recovered", 2, "done")
    history = [m for m in provider.seen[1] if m.role != "system"]
    assert [m.role for m in history] == ["user", "assistant", "tool"]
    assert history[1].tool_calls[0].id == history[2].tool_call_id == "c1"
    assert "ERROR ValueError: bad input" in history[2].content
    assert results == [(False, history[2].content)]


def test_max_steps(tmp_path):
    script = [
        Response(tool_calls=[ToolCall("read_file", {"path": "a.txt"}, id=f"c{i}")])
        for i in range(5)
    ]
    (tmp_path / "a.txt").write_text("x")
    agent = Agent(FakeProvider(script), ToolRegistry(tmp_path), max_steps=2)
    result = run(agent.run("q"))
    assert (result.status, result.steps) == ("stopped", 2)


def test_cancel_before_start(tmp_path):
    cancel = asyncio.Event()
    cancel.set()
    agent = Agent(FakeProvider(["never"]), ToolRegistry(tmp_path))
    result = run(agent.run("q", cancel=cancel))
    assert result.status == "cancelled"
    assert result.steps == 0


def test_context_is_applied_before_model_call(tmp_path):
    from core.budget import BudgetPolicy
    from core.context import ContextBuilder

    (tmp_path / "a.txt").write_text("x" * 400)
    provider = FakeProvider(
        [
            Response(tool_calls=[ToolCall("read_file", {"path": "a.txt"}, id="c1")]),
            Response(content="done"),
        ]
    )
    context = ContextBuilder(
        policy=BudgetPolicy(
            context_window=1200,
            output_cap=100,
            system_cap=100,
            memory_cap=100,
            tools_cap=1000,
            history_cap=60,
            scratch_cap=10,
        ),
        keep_recent_groups=0,
        offload_dir=tmp_path / "offload",
    )
    agent = Agent(provider, ToolRegistry(tmp_path), context=context)
    seen_context = []

    result = run(agent.run("q", on_context=seen_context.append))

    assert result.text == "done"
    assert seen_context
    second_call = provider.seen[1]
    assert any("q" == m.content for m in second_call if m.role == "user")
    assert any("[offloaded:" in m.content for m in second_call)


def test_chat_only_sends_no_tools(tmp_path):
    provider = FakeProvider(["hi"])
    agent = Agent(provider, ToolRegistry(tmp_path), tools_enabled=False)
    result = run(agent.run("q"))
    assert result.text == "hi"
    assert provider.seen_tools[0] == []


def test_text_tool_call_is_parsed_and_executed(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    provider = FakeProvider(
        [
            Response(
                content='<tool_call>{"name": "list_files", "arguments": {"path": "."}}</tool_call>'
            ),
            Response(content="done"),
        ]
    )
    agent = Agent(provider, ToolRegistry(tmp_path))
    result = run(agent.run("q"))
    assert (result.text, result.status) == ("done", "done")
    assert any(m.role == "tool" for m in provider.seen[1])


def test_chat_only_rejects_text_tool_call(tmp_path):
    provider = FakeProvider(
        [
            Response(
                content='<tool_call>{"name": "list_files", "arguments": {"path": "."}}</tool_call>'
            )
        ]
    )
    agent = Agent(provider, ToolRegistry(tmp_path), tools_enabled=False)
    result = run(agent.run("q"))
    assert result.status == "error"
    assert "chat-only" in result.text


def test_prompt_reflects_tools(tmp_path):
    with_tools = Agent(FakeProvider([]), ToolRegistry(tmp_path))
    assert "list_files" in with_tools.system

    chat_only = Agent(FakeProvider([]), ToolRegistry(tmp_path), tools_enabled=False)
    assert "list_files" not in chat_only.system
    assert "No tools" in chat_only.system


def test_repeated_result_warns_and_allows_correction(tmp_path):
    provider = FakeProvider(
        [Response(tool_calls=[ToolCall("list_files", {}, id=f"c{i}")]) for i in range(3)]
        + [Response("需要调整检查方向。")]
    )
    agent = Agent(provider, ToolRegistry(tmp_path))
    result = run(agent.run("q"))
    assert result.status == "done"
    assert result.steps == 4
    assert any(m.name == "progress_warning" for m in provider.seen[-1])
    assert [m.content for m in agent.history if m.role == "user"] == ["q"]


def test_empty_response_retries_then_errors(tmp_path):
    provider = FakeProvider([Response(content=""), Response(content="")])
    agent = Agent(provider, ToolRegistry(tmp_path))
    result = run(agent.run("q"))
    assert result.status == "error"
    assert result.steps == 2


def test_empty_then_ok(tmp_path):
    provider = FakeProvider([Response(content=""), Response(content="ok")])
    agent = Agent(provider, ToolRegistry(tmp_path))
    result = run(agent.run("q"))
    assert (result.text, result.status, result.steps) == ("ok", "done", 2)


def test_finish_reason_length_marks_truncated(tmp_path):
    class LengthProvider:
        async def stream(self, messages, tools):
            yield TextDelta("partial")
            yield StreamDone(finish_reason="length")

    agent = Agent(LengthProvider(), ToolRegistry(tmp_path))
    result = run(agent.run("q"))
    assert (result.text, result.status) == ("partial", "truncated")


def test_text_tool_call_ids_are_step_scoped(tmp_path):
    text_call = '<tool_call>{"name": "list_files", "arguments": {}}</tool_call>'
    provider = FakeProvider(
        [Response(content=text_call), Response(content=text_call), Response(content="done")]
    )
    agent = Agent(provider, ToolRegistry(tmp_path))
    result = run(agent.run("q"))
    assert result.status == "done"

    calls = [
        m.tool_calls[0].id
        for m in provider.seen[-1]
        if m.role == "assistant" and m.tool_calls
    ]
    assert calls == ["step1_0", "step2_0"]


def test_tool_timeout_recovers(tmp_path):
    import time

    def slow():
        time.sleep(0.3)
        return "late"

    registry = ToolRegistry(tmp_path)
    registry.register(
        ToolSpec("slow", "slow tool", {"type": "object", "properties": {}}, slow, timeout=0.05)
    )
    provider = FakeProvider(
        [
            Response(tool_calls=[ToolCall("slow", {}, id="c1")]),
            Response(content="recovered"),
        ]
    )
    agent = Agent(provider, registry)
    result = run(agent.run("q"))
    assert (result.text, result.status) == ("recovered", "done")
    tool_messages = [m for m in provider.seen[1] if m.role == "tool"]
    assert "timeout" in tool_messages[0].content


def test_cancel_after_tool(tmp_path):
    cancel = asyncio.Event()

    def stopper():
        cancel.set()
        return "stopped"

    registry = ToolRegistry(tmp_path)
    registry.register(
        ToolSpec("stop", "stop tool", {"type": "object", "properties": {}}, stopper)
    )
    provider = FakeProvider([Response(tool_calls=[ToolCall("stop", {}, id="c1")])])
    agent = Agent(provider, registry)
    result = run(agent.run("q", cancel=cancel))
    assert result.status == "cancelled"


def test_truncated_tool_call_is_not_executed(tmp_path):
    called = []
    registry = ToolRegistry(tmp_path)
    registry.register(
        ToolSpec(
            "probe",
            "probe tool",
            {"type": "object", "properties": {}},
            lambda: called.append(1) or "x",
        )
    )

    class TruncatedProvider:
        async def stream(self, messages, tools):
            yield ToolCallsReady([ToolCall("probe", {}, id="c1")])
            yield StreamDone(finish_reason="length")

    agent = Agent(TruncatedProvider(), registry)
    result = run(agent.run("q"))
    assert result.status == "truncated"
    assert called == []


def test_agent_injects_task_state(tmp_path):
    provider = FakeProvider(["ok"])
    state = TaskState(goal="")
    agent = Agent(provider, ToolRegistry(tmp_path), state=state)

    result = run(agent.run("fix bug"))

    assert result.text == "ok"
    assert state.goal == "fix bug"
    assert any(
        m.name == "task_state" and "fix bug" in m.content for m in provider.seen[0]
    )


def test_agent_updates_state_after_write(tmp_path):
    state = TaskState(goal="g")
    registry = ToolRegistry(tmp_path, state=state)
    registry.register(
        ToolSpec(
            "write_file",
            "write a file",
            {"type": "object", "properties": {}},
            lambda path, content: "ok",
        )
    )
    provider = FakeProvider(
        [
            Response(
                tool_calls=[
                    ToolCall("write_file", {"path": "a.py", "content": "x"}, id="c1")
                ]
            ),
            Response(content="done"),
        ]
    )
    agent = Agent(provider, registry, state=state)

    result = run(agent.run("q"))

    assert result.text == "done"
    assert state.files_changed == ["a.py"]
    assert any(
        m.name == "task_state" and "a.py" in m.content for m in provider.seen[1]
    )


def test_agent_update_task_tool(tmp_path):
    state = TaskState(goal="g")
    registry = ToolRegistry(tmp_path, state=state)
    provider = FakeProvider(
        [
            Response(
                tool_calls=[
                    ToolCall(
                        "update_task",
                        {"plan": ["a"], "done": ["b"], "open": ["c"]},
                        id="c1",
                    )
                ]
            ),
            Response(content="done"),
        ]
    )
    agent = Agent(provider, registry, state=state)

    result = run(agent.run("q"))

    assert result.text == "done"
    assert state.plan == ["a"]
    assert any(
        m.name == "task_state" and "Plan:" in m.content for m in provider.seen[1]
    )


def test_agent_denies_write_without_approval(tmp_path):
    provider = FakeProvider(
        [
            Response(
                tool_calls=[
                    ToolCall("write_file", {"path": "a.txt", "content": "x"}, id="c1")
                ]
            ),
            Response(content="done"),
        ]
    )
    agent = Agent(provider, ToolRegistry(tmp_path))

    result = run(agent.run("q"))

    assert result.text == "done"
    assert not (tmp_path / "a.txt").exists()
    tool_message = [m for m in provider.seen[1] if m.role == "tool"][0]
    assert "approval_required" in tool_message.content


def test_agent_denies_write_when_user_says_no(tmp_path):
    async def deny(call, preview):
        return ApprovalDecision(False, "deny")

    provider = FakeProvider(
        [
            Response(
                tool_calls=[
                    ToolCall("write_file", {"path": "a.txt", "content": "x"}, id="c1")
                ]
            ),
            Response(content="done"),
        ]
    )
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(handler=deny))

    result = run(agent.run("q"))

    assert result.text == "done"
    assert not (tmp_path / "a.txt").exists()
    tool_message = [m for m in provider.seen[1] if m.role == "tool"][0]
    assert "denied_by_user" in tool_message.content


def test_agent_allows_write_once(tmp_path):
    async def allow(call, preview):
        return ApprovalDecision(True, "once")

    provider = FakeProvider(
        [
            Response(
                tool_calls=[
                    ToolCall("write_file", {"path": "a.txt", "content": "x"}, id="c1")
                ]
            ),
            Response(content="done"),
        ]
    )
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(handler=allow))

    result = run(agent.run("q"))

    assert result.text == "done"
    assert (tmp_path / "a.txt").read_text() == "x"


def test_agent_approval_failure_is_closed(tmp_path):
    async def boom(call, preview):
        raise RuntimeError("modal crashed")

    provider = FakeProvider(
        [
            Response(
                tool_calls=[
                    ToolCall("write_file", {"path": "a.txt", "content": "x"}, id="c1")
                ]
            ),
            Response(content="done"),
        ]
    )
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate(handler=boom))

    result = run(agent.run("q"))

    assert result.text == "done"
    assert not (tmp_path / "a.txt").exists()
    tool_message = [m for m in provider.seen[1] if m.role == "tool"][0]
    assert "approval_failed" in tool_message.content


def test_default_steps_allow_productive_long_task(tmp_path):
    registry = ToolRegistry(tmp_path)
    registry.register(ToolSpec("inspect", "Inspect", {"type": "object"}, lambda index: str(index)))
    provider = FakeProvider([
        *[Response(tool_calls=[ToolCall("inspect", {"index": i}, id=f"c{i}")])
          for i in range(12)],
        "done",
    ])
    agent = Agent(provider, registry)
    result = run(agent.run("q"))
    assert result.status == "done"
    assert result.steps == 13


def test_max_run_seconds_stops(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    provider = FakeProvider(
        [Response(tool_calls=[ToolCall("read_file", {"path": "a.txt"}, id="c1")])]
    )
    agent = Agent(provider, ToolRegistry(tmp_path), max_run_seconds=-1)
    result = run(agent.run("q"))
    assert result.status == "stopped"
    assert "max_run_seconds" in result.text


def test_agent_history_across_runs(tmp_path):
    provider = FakeProvider(["first", "second"])
    agent = Agent(provider, ToolRegistry(tmp_path))

    run(agent.run("one"))
    run(agent.run("two"))

    second_call = provider.seen[1]
    assert any(m.role == "user" and m.content == "one" for m in second_call)
    assert any(m.role == "assistant" and m.content == "first" for m in second_call)
    assert any(m.role == "user" and m.content == "two" for m in second_call)
    assert [m.role for m in agent.history] == [
        "user", "assistant", "user", "assistant",
    ]


def test_agent_on_message_callback(tmp_path):
    provider = FakeProvider(["ok"])
    agent = Agent(provider, ToolRegistry(tmp_path))
    seen = []

    run(agent.run("q", on_message=seen.append))

    assert [m.role for m in seen] == ["user", "assistant"]


def test_agent_resume_without_new_user(tmp_path):
    provider = FakeProvider(["resumed"])
    history = [Message("user", "old"), Message("assistant", "old answer")]
    agent = Agent(provider, ToolRegistry(tmp_path), history=history)

    result = run(agent.run(None))

    assert result.text == "resumed"
    first_call = provider.seen[0]
    assert any(m.role == "user" and m.content == "old" for m in first_call)
    assert agent.history[-1].content == "resumed"


def test_agent_state_persists_across_runs(tmp_path):
    provider = FakeProvider(["a", "b"])
    agent = Agent(provider, ToolRegistry(tmp_path))
    run(agent.run("first"))
    run(agent.run("second"))
    assert agent.state is not None
    assert agent.state.goal == "first"


def test_agent_injects_project_instructions(tmp_path):
    provider = FakeProvider(["ok"])
    agent = Agent(
        provider,
        ToolRegistry(tmp_path),
        project_instructions="Use tabs for indentation.",
    )
    run(agent.run("q"))
    system_text = "\n".join(m.content for m in provider.seen[0] if m.role == "system")
    assert "[PROJECT INSTRUCTIONS]" in system_text
    assert "Use tabs for indentation." in system_text


def test_auto_continue_on_length(tmp_path):
    provider = FakeProvider(
        [Response("part", finish_reason="length"), Response("done")]
    )
    agent = Agent(provider, ToolRegistry(tmp_path), max_continuations=1)

    result = run(agent.run("q"))

    assert result.text == "done"
    second_call = provider.seen[1]
    assert any(
        m.role == "user" and m.name == "continuation" for m in second_call
    )
    assert any(m.role == "assistant" and m.content == "part" for m in second_call)


def test_transient_stream_error_retries(tmp_path):
    class Flaky:
        calls = 0

        async def stream(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                yield StreamError("HTTP 503", retryable=True)
                return
            yield TextDelta("ok")
            yield StreamDone()

    provider = Flaky()
    agent = Agent(
        provider,
        ToolRegistry(tmp_path),
        llm_retries=1,
        llm_retry_backoff=0,
    )

    result = run(agent.run("q"))

    assert result.text == "ok"
    assert provider.calls == 2


def test_stream_timeout_retries(tmp_path):
    class SlowThenFast:
        calls = 0

        async def stream(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(0.2)
            yield TextDelta("ok")
            yield StreamDone()

    provider = SlowThenFast()
    agent = Agent(
        provider,
        ToolRegistry(tmp_path),
        llm_timeout=0.05,
        llm_retries=1,
        llm_retry_backoff=0,
    )

    result = run(agent.run("q"))

    assert result.text == "ok"
    assert provider.calls == 2


def test_token_budget_stops(tmp_path):
    provider = FakeProvider(
        [
            Response(
                "ok",
                usage={"prompt_tokens": 10000, "completion_tokens": 5000, "total_tokens": 15000},
            )
        ]
    )
    agent = Agent(provider, ToolRegistry(tmp_path), max_run_tokens=10000)

    result = run(agent.run("q"))

    assert result.status == "stopped"
    assert "token_budget" in result.text
    assert result.usage["total_tokens"] == 15000


def test_no_progress_stops(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    provider = FakeProvider(
        [
            *[Response(tool_calls=[ToolCall("read_file", {"path": "a.txt"}, id=f"c{i}")])
              for i in range(5)],
            Response("文件内容未变，任务尚未完成。"),
        ]
    )
    agent = Agent(
        provider,
        ToolRegistry(tmp_path),
        max_no_progress=2,
        max_steps=10,
    )

    result = run(agent.run("q"))

    assert result.status == "stopped"
    assert result.stop_reason == "no_progress"
    assert "no_progress" in result.text
    assert len(provider.seen) == 6
    assert provider.seen_tools[-1] == []


def test_no_progress_checkpoint_respects_remaining_task_budget(tmp_path):
    registry = ToolRegistry(tmp_path)
    registry.register(ToolSpec("inspect", "Inspect", {"type": "object"}, lambda: "same"))
    script = [Response(tool_calls=[ToolCall("inspect", {}, id=f"c{i}")],
                       usage={"total_tokens": 10 if i < 4 else 9900}) for i in range(5)]
    provider = FakeProvider(script + ["must not request"])
    agent = Agent(provider, registry, max_run_tokens=10000)

    result = run(agent.run("fix"))

    assert result.status == "stopped" and result.stop_reason == "no_progress"
    assert len(provider.seen) == 5
    assert result.usage["total_tokens"] == 9940
    assert "预算" in result.text and "待完成" in result.text


def test_no_progress_checkpoint_respects_active_time_limit(tmp_path):
    calls = []
    registry = ToolRegistry(tmp_path)

    def inspect():
        calls.append(1)
        if len(calls) == 5:
            agent.max_run_seconds = -1
        return "same"

    registry.register(ToolSpec("inspect", "Inspect", {"type": "object"}, inspect))
    provider = FakeProvider([
        *[Response(tool_calls=[ToolCall("inspect", {}, id=f"c{i}")]) for i in range(5)],
        "must not request",
    ])
    agent = Agent(provider, registry)

    result = run(agent.run("fix"))

    assert result.status == "stopped" and result.stop_reason == "no_progress"
    assert len(provider.seen) == 5 and "运行时间已到" in result.text


def test_cancel_takes_priority_over_progress_degradation(tmp_path):
    calls = []
    cancel = asyncio.Event()
    registry = ToolRegistry(tmp_path)

    def inspect():
        calls.append(1)
        if len(calls) == 5:
            cancel.set()
        return "same"

    registry.register(ToolSpec("inspect", "Inspect", {"type": "object"}, inspect))
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("inspect", {}, id=f"c{i}")]) for i in range(5)
    ])
    agent = Agent(provider, registry)

    result = run(agent.run("fix", cancel=cancel))

    assert result.status == "cancelled" and len(provider.seen) == 5
    assert not any(m.name == "progress_stop" for m in agent.history)


def test_read_only_tool_retries_on_timeout(tmp_path):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("transient")
        return "ok"

    registry = ToolRegistry(tmp_path)
    registry.register(
        ToolSpec(
            "flaky",
            "flaky test tool",
            {"type": "object", "properties": {}},
            flaky,
            retryable=True,
        )
    )
    provider = FakeProvider(
        [
            Response(tool_calls=[ToolCall("flaky", {}, id="c1")]),
            Response("done"),
        ]
    )
    agent = Agent(provider, registry, tool_retries=1)

    result = run(agent.run("q"))

    assert result.text == "done"
    assert calls["n"] == 2


def test_vibe_coding_prompt_rules(tmp_path):
    agent = Agent(FakeProvider([]), ToolRegistry(tmp_path))
    assert "Read a file before editing it" in agent.system
    assert "never claim a test passed" in agent.system
