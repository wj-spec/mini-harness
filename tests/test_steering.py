import asyncio

from app.agent import Agent
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry
from core.models import Response, ToolCall


def run(coro):
    return asyncio.run(coro)


def _user_texts(messages):
    return [m.content for m in messages if m.role == "user"]


def test_steer_without_active_run_is_rejected(tmp_path):
    agent = Agent(FakeProvider(["done"]), ToolRegistry(tmp_path))
    assert agent.steer("保留公开接口") is False
    result = run(agent.run("q"))
    assert result.status == "done"
    assert agent.steer("保留公开接口") is False
    assert agent.steer("   ") is False


def test_steer_is_applied_before_the_next_model_request(tmp_path):
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("list_files", {}, id="c1")]),
        Response(content="已按约束继续"),
    ])
    agent = Agent(provider, ToolRegistry(tmp_path))
    steered = []
    delivered = []
    events = []

    def on_tool(call):
        steered.append(agent.steer("保留公开接口，只改内部实现"))

    result = run(agent.run(
        "重构一下", on_tool=on_tool, on_message=delivered.append, trace=events.append,
    ))

    assert steered == [True]
    assert (result.status, result.steps, result.text) == ("done", 2, "已按约束继续")
    # 第二次请求才带上纠偏，第一次请求不受影响。
    assert _user_texts(provider.seen[0]) == ["重构一下"]
    assert _user_texts(provider.seen[1]) == ["重构一下", "保留公开接口，只改内部实现"]
    steering = [m for m in delivered if m.role == "user" and m.name == "steering"]
    assert [m.content for m in steering] == ["保留公开接口，只改内部实现"]
    assert any(event.get("type") == "steering_applied" and event.get("step") == 2 for event in events)


def test_multiple_steering_messages_keep_arrival_order(tmp_path):
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("list_files", {}, id="c1")]),
        Response(content="收到"),
    ])
    agent = Agent(provider, ToolRegistry(tmp_path))

    def on_tool(call):
        agent.steer("第一条：不要改测试")
        agent.steer("第二条：先跑回归")

    result = run(agent.run("q", on_tool=on_tool))

    assert result.status == "done"
    assert _user_texts(provider.seen[1]) == ["q", "第一条：不要改测试", "第二条：先跑回归"]


def test_steer_arriving_before_done_takes_another_step(tmp_path):
    provider = FakeProvider(["我先收尾。", "已按新约束继续。"])
    agent = Agent(provider, ToolRegistry(tmp_path))
    text_deltas = []
    steered = []

    def on_text(delta):
        text_deltas.append(delta)
        if not steered:
            steered.append(agent.steer("保留公开接口"))

    result = run(agent.run("q", on_text=on_text))

    assert steered == [True]
    # 收尾文本已经产生，pending 纠偏仍会再走一步；不静默丢弃。
    assert (result.status, result.steps, result.text) == ("done", 2, "已按新约束继续。")
    assert _user_texts(provider.seen[1]) == ["q", "保留公开接口"]
    assert "我先收尾。" in "".join(text_deltas)


def test_steering_message_enters_history_for_following_turns(tmp_path):
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("list_files", {}, id="c1")]),
        "done",
        "second turn",
    ])
    agent = Agent(provider, ToolRegistry(tmp_path))

    run(agent.run("q", on_tool=lambda call: agent.steer("只改内部实现")))
    result = run(agent.run("继续"))

    assert result.status == "done"
    steering = [m for m in agent.history if m.role == "user" and m.name == "steering"]
    assert [m.content for m in steering] == ["只改内部实现"]
    assert _user_texts(provider.seen[2]) == ["q", "只改内部实现", "继续"]


def test_corrections_enter_constraint_ledger_with_source_and_order(tmp_path):
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("list_files", {}, id="c1")]),
        Response(tool_calls=[ToolCall("list_files", {}, id="c2")]),
        Response(content="收到"),
    ])
    agent = Agent(provider, ToolRegistry(tmp_path))
    queued = iter(["保留公开接口", "同时保持兼容"])

    def on_tool(call):
        agent.steer(next(queued))

    result = run(agent.run("重构一下", on_tool=on_tool))
    assert result.status == "done"
    rendered = agent.state.render()
    assert "Constraints (by time; later entries override earlier ones on conflict):" in rendered
    assert "1. (steering) 保留公开接口" in rendered
    assert "2. (steering) 同时保持兼容" in rendered
    # 约束随 task_state 系统消息重注入，历史压缩不会丢失。
    assert any(m.role == "system" and "保留公开接口" in m.content and "同时保持兼容" in m.content
               for m in provider.seen[-1])

