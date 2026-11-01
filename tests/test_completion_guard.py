import asyncio

from app.agent import Agent
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.session import SessionStore
from app.tools import ToolRegistry
from core.models import Response, ToolCall
from core.rounds import build_round_record
from core.state import (
    CompletionDecision, TaskState, completion_decision, delivery_receipt,
)


def run(coro):
    return asyncio.run(coro)


def _change(path="a.txt", after="sha-new", kind="modify"):
    return {
        "path": path,
        "kind": kind,
        "before_sha256": "sha-old",
        "after_sha256": after,
        "before_status": "present",
        "after_status": "present",
        "size": 3,
        "step": 1,
        "tool": "edit_file",
        "tool_call_id": "c1",
        "approval_scope": "once",
        "at": "2026-01-01T00:00:00+00:00",
    }


def test_completion_decision_ignores_qa_and_tasks_without_changes():
    qa = TaskState(goal="如何运行测试？")
    assert completion_decision(qa).allowed is True

    action = TaskState(goal="修改 a.txt")
    assert completion_decision(action).allowed is True


def test_completion_decision_blocks_change_without_verification():
    state = TaskState(goal="修改 a.txt", file_changes=[_change()], mutation_seq=1)
    decision = completion_decision(state)
    assert decision.allowed is False
    assert decision.codes == ("no_verification",)
    assert "验证命令" in decision.reason_text


def test_completion_decision_blocks_change_after_verification():
    state = TaskState(
        goal="修改 a.txt", file_changes=[_change()], mutation_seq=2,
        last_verification_seq=1, last_verification_ok=True,
    )
    decision = completion_decision(state)
    assert decision.allowed is False
    assert decision.codes == ("mutation_after_verification",)


def test_completion_decision_blocks_failed_last_verification():
    state = TaskState(
        goal="修改 a.txt", file_changes=[_change()], mutation_seq=1,
        last_verification_seq=1, last_verification_ok=False,
    )
    decision = completion_decision(state)
    assert decision.allowed is False
    assert decision.codes == ("last_verification_failed",)


def test_completion_decision_allows_covering_verification():
    state = TaskState(
        goal="修改 a.txt", file_changes=[_change()], mutation_seq=1,
        last_verification_seq=1, last_verification_ok=True,
    )
    decision = completion_decision(state)
    assert decision.allowed is True
    assert decision.to_dict() == {"allowed": True, "codes": [], "messages": []}


def test_receipt_distinguishes_verified_and_unverified_completion():
    state = TaskState(goal="修改 a.txt", files_changed=["a.txt"], file_changes=[_change()])
    unverified = delivery_receipt(
        state, status="done", task_kind="action", verified=False,
        completion=CompletionDecision(False, ("no_verification",), ("有文件改动，但没有记录到验证命令",)),
    )
    assert "[任务验收] 未验证" in unverified
    assert "完成判定：未通过" in unverified

    verified = delivery_receipt(
        state, status="done", task_kind="action", verified=True,
        completion=CompletionDecision(True),
    )
    assert "[任务验收] 已验证" in verified
    assert "完成判定：通过" in verified


async def _allow(call, preview):
    return ApprovalDecision(True, "workspace")


def test_agent_rejects_unverified_completion_once_then_marks_unverified(tmp_path):
    rounds = []
    provider = FakeProvider([
        Response(tool_calls=[ToolCall(
            "write_file", {"path": "a.txt", "content": "new"}, id="w1",
        )]),
        "done without verification",
        "still done",
    ])
    agent = Agent(
        provider, ToolRegistry(tmp_path), approval=ApprovalGate(_allow),
        completion_guard=True, workspace_audit=False, on_round=rounds.append,
    )

    result = run(agent.run("修改 a.txt"))

    assert result.status == "done"
    assert result.steps == 3
    assert result.verified is False
    assert result.completion and result.completion["codes"] == ["no_verification"]
    assert "未验证" in result.receipt
    assert agent.state is not None
    assert agent.state.mutation_seq == 1
    assert agent.state.verifications == []
    assert len(rounds) == 1
    assert rounds[0]["verified"] is False
    assert rounds[0]["schema_version"] == 1


def test_agent_marks_verified_after_covering_verification(tmp_path):
    rounds = []
    provider = FakeProvider([
        Response(tool_calls=[ToolCall(
            "write_file", {"path": "a.txt", "content": "new"}, id="w1",
        )]),
        Response(tool_calls=[ToolCall(
            "bash", {"command": "true", "verification": True}, id="b1",
        )]),
        "done",
    ])
    agent = Agent(
        provider, ToolRegistry(tmp_path), approval=ApprovalGate(_allow),
        completion_guard=True, workspace_audit=False, on_round=rounds.append,
    )

    result = run(agent.run("修改 a.txt"))

    assert result.status == "done"
    assert result.verified is True
    assert "已验证" in result.receipt
    assert agent.state is not None
    assert agent.state.last_verification_seq == agent.state.mutation_seq == 1
    assert agent.state.last_verification_ok is True
    assert len(agent.state.file_changes) == 1
    assert agent.state.file_changes[0]["tool_call_id"] == "w1"
    assert agent.state.verifications[0]["ok"] is True
    assert len(rounds) == 1
    assert rounds[0]["verified"] is True


def test_agent_writes_round_record_to_session(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    provider = FakeProvider([
        Response(tool_calls=[ToolCall(
            "write_file", {"path": "a.txt", "content": "new"}, id="w1",
        )]),
        Response(tool_calls=[ToolCall(
            "bash", {"command": "true", "verification": True}, id="b1",
        )]),
        "done",
    ])
    agent = Agent(
        provider, ToolRegistry(tmp_path), approval=ApprovalGate(_allow),
        completion_guard=True, workspace_audit=False, on_round=session.save_round,
    )

    result = run(agent.run("修改 a.txt"))

    assert result.verified is True
    rounds = session.load_rounds()
    assert len(rounds) == 1
    round_record = rounds[0]
    assert round_record["run_id"] == result.run_id
    assert round_record["verified"] is True
    assert round_record["task_state"]["file_changes"][0]["path"] == "a.txt"
    assert round_record["task_state"]["verifications"][0]["ok"] is True


def test_completion_decision_is_scoped_to_current_run_start():
    state = TaskState(goal="修改 a.txt", file_changes=[_change()], mutation_seq=1)
    # 上一轮的改动 + 本轮无改动：不拦截
    assert completion_decision(state, start_mutation_seq=1, start_file_changes=1).allowed is True

    state.file_changes.append(_change(path="b.txt", after="sha-b"))
    state.mutation_seq = 2
    decision = completion_decision(state, start_mutation_seq=1, start_file_changes=1)
    assert decision.codes == ("no_verification",)
    assert "本轮" in decision.reason_text

    # 上一轮的验证不能覆盖本轮改动
    state.last_verification_seq = 1
    state.last_verification_ok = True
    assert completion_decision(
        state, start_mutation_seq=1, start_file_changes=1,
    ).codes == ("no_verification",)

    # 本轮验证覆盖本轮最后一次改动
    state.last_verification_seq = 2
    assert completion_decision(
        state, start_mutation_seq=1, start_file_changes=1,
    ).allowed is True


def test_receipt_and_round_record_scope_to_run_start():
    state = TaskState(
        goal="修改 a.txt", files_changed=["old.txt"], file_changes=[_change(path="old.txt")],
        command_results=[{"command": "true", "verification": True, "status": "succeeded", "exit_code": 0}],
        mutation_seq=2, last_verification_seq=2, last_verification_ok=True,
    )
    receipt = delivery_receipt(
        state, status="done", task_kind="action", verified=True,
        start_files_changed=1, start_command_results=1,
    )
    assert "old.txt" not in receipt
    assert "未运行验证命令" in receipt
    record = build_round_record(
        run_id="run-2", goal="修改 a.txt", task_kind="action", status="done",
        stop_reason="", steps=1, usage={}, cost_usd=0.0, started_at="2026-01-01T00:00:00+00:00",
        state=state, verified=True, start_mutation_seq=2, start_file_changes=1,
        start_verifications=0, start_files_changed=1, start_command_results=1,
    )
    assert record["task_state"]["file_changes"] == []
    assert record["task_state"]["files_changed"] == []
    assert record["task_state"]["mutation_seq"] == 0
    assert record["task_state"]["command_results_count"] == 0


def test_second_run_does_not_inherit_verified_or_changes(tmp_path):
    rounds = []
    provider = FakeProvider([
        # run 1：写文件 + 验证 + 完成（verified=true）
        Response(tool_calls=[ToolCall("write_file", {"path": "a.txt", "content": "new"}, id="w1")]),
        Response(tool_calls=[ToolCall("bash", {"command": "true", "verification": True}, id="b1")]),
        "done",
        # run 2：纯问答
        "解释一下刚才做了什么",
    ])
    agent = Agent(
        provider, ToolRegistry(tmp_path), approval=ApprovalGate(_allow),
        completion_guard=True, workspace_audit=False, on_round=rounds.append,
    )
    first = run(agent.run("修改 a.txt"))
    second = run(agent.run("解释一下刚才做了什么"))

    assert first.verified is True
    assert second.status == "done" and second.steps == 1
    assert second.verified is None, "第二轮无改动时不应继承上一轮的 verified=True"
    assert len(rounds) == 2
    assert rounds[1]["verified"] is None
    assert rounds[1]["task_state"]["file_changes"] == []
    assert "无（未记录文件修改）" in second.receipt


def test_second_run_is_not_blocked_by_previous_unverified_changes(tmp_path):
    rounds = []
    provider = FakeProvider([
        # run 1：写文件但未验证；守卫拒绝一次后以 verified=false 收尾
        Response(tool_calls=[ToolCall("write_file", {"path": "a.txt", "content": "new"}, id="w1")]),
        "done without verification",
        "still done",
        # run 2：纯问答，不应被上一轮未验证改动触发守卫
        "回答",
    ])
    agent = Agent(
        provider, ToolRegistry(tmp_path), approval=ApprovalGate(_allow),
        completion_guard=True, workspace_audit=False, on_round=rounds.append,
    )
    first = run(agent.run("修改 a.txt"))
    second = run(agent.run("回答我"))

    assert first.verified is False
    assert second.status == "done" and second.steps == 1, "第二轮纯问答不应触发完成守卫"
    assert second.verified is None
    assert rounds[1]["task_state"]["file_changes"] == []


def test_second_run_with_audit_only_reports_current_run_changes(tmp_path):
    rounds = []
    provider = FakeProvider([
        Response(tool_calls=[ToolCall("write_file", {"path": "a.txt", "content": "new"}, id="w1")]),
        Response(tool_calls=[ToolCall("bash", {"command": "true", "verification": True}, id="b1")]),
        "done",
        "解释一下刚才做了什么",
    ])
    agent = Agent(
        provider, ToolRegistry(tmp_path), approval=ApprovalGate(_allow),
        completion_guard=True, workspace_audit=True, on_round=rounds.append,
    )
    first = run(agent.run("修改 a.txt"))
    second = run(agent.run("解释一下刚才做了什么"))

    assert first.verified is True
    assert second.verified is None
    assert rounds[1]["task_state"]["file_changes"] == []
    assert rounds[1]["task_state"]["files_changed"] == []
    assert rounds[1]["audit"] is not None
    assert rounds[1]["audit"]["changed_files"] == 0
    assert "a.txt" not in second.receipt
