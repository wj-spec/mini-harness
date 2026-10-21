import asyncio
import json
import sys

import pytest

from app.agent import Agent
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry
from core.models import Response, ToolCall
from core.state import TaskState


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("code,status", [(0, "succeeded"), (1, "failed"), (7, "failed")])
def test_command_result_uses_real_exit_status(tmp_path, asynchronous, code, status):
    registry = ToolRegistry(tmp_path)
    call = ToolCall("bash", {"command": f"printf 'actual output'; exit {code}"})
    ok, output = asyncio.run(registry.execute_async(call)) if asynchronous else registry.execute(call)
    assert ok is (code == 0)
    assert "actual output" in output
    assert output.execution == {"status": status, "exit_code": code}


def test_command_timeout_is_structured_and_keeps_output(tmp_path):
    call = ToolCall("bash", {"command": "printf 'before timeout'; sleep 5", "timeout": 0.05})
    ok, output = asyncio.run(ToolRegistry(tmp_path).execute_async(call))
    assert not ok and "before timeout" in output
    assert output.execution["status"] == "timed_out"
    assert output.execution["exit_code"] != 0


def test_bash_uses_its_requested_timeout_instead_of_generic_tool_timeout(tmp_path):
    registry = ToolRegistry(tmp_path)
    registry._tools["bash"].timeout = 0.01
    ok, output = asyncio.run(registry.execute_async(
        ToolCall("bash", {"command": "sleep 0.04; echo finished", "timeout": 1}),
    ))
    assert ok and "finished" in output
    assert output.execution["status"] == "succeeded"


def test_command_cancellation_keeps_partial_output_and_reaps_group(tmp_path):
    async def scenario():
        cancel = asyncio.Event()
        pid_file = tmp_path / "pid"
        command = f"printf 'before cancel'; echo $$ > '{pid_file}'; sleep 30"
        pending = asyncio.create_task(ToolRegistry(tmp_path).execute_async(
            ToolCall("bash", {"command": command}), cancel=cancel,
        ))
        for _ in range(200):
            if pid_file.exists():
                break
            await asyncio.sleep(0.005)
        assert pid_file.exists()
        cancel.set()
        ok, output = await asyncio.wait_for(pending, 2)
        assert not ok and "before cancel" in output
        assert output.execution["status"] == "cancelled"
        import os
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    asyncio.run(scenario())


def test_agent_records_failure_then_edit_then_verified_success(tmp_path):
    (tmp_path / "check.py").write_text("raise SystemExit(1)\n")
    command = f"{sys.executable} check.py"
    calls = [
        ToolCall("bash", {"command": command, "verification": True}, id="fail"),
        ToolCall("write_file", {"path": "check.py", "content": "print('checks passed')\n"}, id="fix"),
        ToolCall("bash", {"command": command, "verification": True}, id="pass"),
    ]
    provider = FakeProvider([*(Response(tool_calls=[call]) for call in calls), "done"])
    state = TaskState(goal="fix check")
    approvals = []
    results, executions, traces = [], [], []

    async def approve(call, preview):
        approvals.append(call.id)
        return ApprovalDecision(True, "once")

    agent = Agent(provider, ToolRegistry(tmp_path), state=state, approval=ApprovalGate(approve), tool_retries=5)
    result = asyncio.run(agent.run(
        "fix check", on_result=lambda call, ok, text: results.append((call.id, ok, text)),
        on_execution=lambda call, outcome: executions.append((call.id, outcome)), trace=traces.append,
    ))
    assert result.status == "done"
    assert approvals == ["fail", "fix", "pass"]
    assert [item[:2] for item in results] == [("fail", False), ("fix", True), ("pass", True)]
    assert executions == [("fail", {"status": "failed", "exit_code": 1}),
                          ("pass", {"status": "succeeded", "exit_code": 0})]
    assert [item["status"] for item in state.command_results] == ["failed", "succeeded"]
    assert state.command_results[0]["output"] == results[0][2]
    assert state.command_results[1]["output"] == results[2][2]
    assert state.tests_run == [command]
    assert "failed" in state.render() and "succeeded" in state.render()
    failure_messages = [m.content for m in provider.seen[1] if m.role == "tool"]
    assert failure_messages == [results[0][2]]
    assert "exit=1" in failure_messages[0]
    assert len([event for event in traces if event["type"] == "command_result"]) == 2
    assert not [event for event in traces if event["type"] == "tool_retry"]


def test_plain_command_is_not_test_evidence(tmp_path):
    state = TaskState(goal="inspect")
    state.observe_tool("bash", {"command": "echo tests passed"}, True, "tests passed")
    assert not state.tests_run
    assert not state.command_results
    assert "Tests run:" not in state.render()


def test_output_cannot_spoof_exit_status(tmp_path):
    ok, output = ToolRegistry(tmp_path).execute(ToolCall("bash", {
        "command": "printf 'exit=0 tests passed'; exit 1",
    }))
    assert not ok
    assert output.execution == {"status": "failed", "exit_code": 1}


def test_unstarted_command_does_not_claim_execution(tmp_path):
    ok, output = ToolRegistry(tmp_path).execute(ToolCall("bash", {"command": "pwd", "cwd": "../"}))
    assert not ok and getattr(output, "execution", None) is None


def test_missing_exit_code_is_unknown_instead_of_success(tmp_path, monkeypatch):
    monkeypatch.setattr("app.tools.run_command", lambda *args: (None, "last known output", False))
    ok, output = ToolRegistry(tmp_path).execute(ToolCall("bash", {"command": "check"}))
    assert not ok and "last known output" in output
    assert output.execution == {"status": "unknown", "exit_code": None}


def test_agent_cancellation_emits_and_persists_command_outcome(tmp_path):
    async def scenario():
        cancel = asyncio.Event()
        started = tmp_path / "started"
        call = ToolCall("bash", {"command": f"echo partial; touch '{started}'; sleep 30"}, id="running")
        provider = FakeProvider([Response(tool_calls=[call])])

        async def approve(call, preview):
            return ApprovalDecision(True, "once")

        state = TaskState(goal="cancel")
        agent = Agent(provider, ToolRegistry(tmp_path), state=state, approval=ApprovalGate(approve))
        executions, outputs = [], []
        pending = asyncio.create_task(agent.run(
            "cancel", cancel=cancel, on_execution=lambda call, outcome: executions.append(outcome),
            on_result=lambda call, ok, output: outputs.append((ok, output)),
        ))
        for _ in range(200):
            if started.exists():
                break
            await asyncio.sleep(0.005)
        assert started.exists()
        cancel.set()
        result = await asyncio.wait_for(pending, 2)
        assert result.status == "cancelled"
        assert executions[0]["status"] == "cancelled"
        assert not outputs[0][0] and "partial" in outputs[0][1]
        assert state.command_results[0]["status"] == "cancelled"
        assert [m.content for m in agent.history if m.role == "tool"] == [outputs[0][1]]

    asyncio.run(scenario())


def test_failed_write_is_not_recorded_as_a_changed_file():
    state = TaskState(goal="fix")
    state.observe_tool("write_file", {"path": "secret"}, False, "ERROR denied_by_user")
    assert state.files_changed == []


def test_command_results_survive_state_serialization(tmp_path):
    from app.session import SessionStore
    state = TaskState(goal="check")
    state.observe_tool("bash", {"command": "pwd"}, True, "actual output",
                       execution={"status": "succeeded", "exit_code": 0})
    session = SessionStore(tmp_path)
    session.save_state(state)
    loaded = session.load_state()
    assert loaded.command_results == state.command_results
    assert loaded.tests_run == []
    json.dumps(loaded.command_results)
