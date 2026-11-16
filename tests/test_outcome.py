import asyncio

from app.runtime.agent import Agent
from app.providers.fake import FakeProvider
from app.safety.policy import ApprovalDecision, ApprovalGate
from app.tools.registry import ToolRegistry
from core.contracts import Response, ToolCall
from core.task import TaskState
from core.task.outcome import build_outcome, merge_outcomes


def test_outcome_tracks_repeat_edits_and_verification_coverage():
    state = TaskState(goal="可以帮我修改吗？", files_changed=["a.py"], mutation_seq=2,
                      file_changes=[{"path": "a.py"}, {"path": "a.py"}])
    state.verifications = [{"command": "check", "ok": True, "mutation_seq": 1}]
    result = build_outcome(state, start_file_changes=1, start_verifications=0)
    assert result["files"] == ["a.py"]
    assert result["verification"] == "stale"


def test_outcome_does_not_erase_failed_check_with_unrelated_success():
    state = TaskState(goal="check", mutation_seq=1, file_changes=[{"path": "a"}],
                      verifications=[{"command": "test-a", "ok": False, "mutation_seq": 1},
                                     {"command": "test-b", "ok": True, "mutation_seq": 1}])
    first = build_outcome(state)
    assert first["verification"] == "failed"
    state.verifications.append({"command": "test-a", "ok": True, "mutation_seq": 1})
    fixed = build_outcome(state, start_file_changes=1, start_verifications=2)
    merged = merge_outcomes(first, fixed)
    assert merged["files"] == ["a"]
    assert merged["verification"] == "passed"


def test_task_merge_preserves_pending_changes_and_unknown_history():
    first = build_outcome(TaskState(goal="write", mutation_seq=1, file_changes=[{"path": "a"}]))
    final = build_outcome(TaskState(goal="reply", mutation_seq=1))
    assert merge_outcomes(first, final)["verification"] == "not_run"
    assert merge_outcomes(first, final)["files"] == ["a"]
    assert merge_outcomes(None, final, prior_unknown=True)["prior_unknown"] is True


async def allow(call, preview):
    return ApprovalDecision(True, "once")


def test_agent_keeps_raw_receipt_but_adds_quiet_facts(tmp_path):
    agent = Agent(FakeProvider(["answer"]), ToolRegistry(tmp_path), system="test",
                  workspace_audit=False)
    result = asyncio.run(agent.run("解释一下"))
    assert result.outcome["files"] == []
    assert result.outcome["operations"] == 0
    assert result.outcome["tool_errors"] == 0
    assert result.receipt.startswith("[运行结束]")


def test_agent_records_no_diff_command_and_denial(tmp_path):
    async def scenario(approved):
        async def decision(call, preview):
            return ApprovalDecision(approved, "once")
        agent = Agent(FakeProvider([
            Response(tool_calls=[ToolCall("bash", {"command": "printf ok"}, id="c")]),
            "answer",
        ]), ToolRegistry(tmp_path), system="test", approval=ApprovalGate(decision),
            workspace_audit=False)
        return await agent.run("运行命令后说明")
    permitted = asyncio.run(scenario(True))
    assert permitted.outcome["operations"] == 1
    assert permitted.outcome["files"] == []
    denied = asyncio.run(scenario(False))
    assert denied.outcome["denied"] is True
    assert denied.outcome["tool_errors"] >= 1
    assert denied.outcome["operations"] == 0

def test_stdio_task_outcome_keeps_prior_segment_effects_and_persists(tmp_path, monkeypatch):
    from app.config import Config
    from app.runtime.inspection import InspectionStore
    from app.session.store import SessionStore
    from tests.test_stdio import Harness
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    agent = Agent(FakeProvider([
        Response(tool_calls=[ToolCall("write_file", {"path": "a.txt", "content": "one"}, id="w")]),
        "final",
        "another task",
    ]), ToolRegistry(tmp_path), system="test", approval=ApprovalGate(allow),
        workspace_audit=False, max_steps=1)
    async def scenario():
        h = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await h.until("ready")
            h.send("run", "segment-1", text="write", task={"kind": "goal", "objective": "write a file"})
            approval = await h.until("approval_request", "segment-1")
            h.send("approval", "allow", approval_id=approval["approval_id"], decision="once")
            first = (await h.until("done", "segment-1"))["result"]
            task_id = next(e["task"]["task_id"] for e in h.events if e["type"] == "task_state")
            h.send("run", "segment-2", text="finish", task={"kind": "goal", "task_id": task_id})
            final = (await h.until("done", "segment-2"))["result"]
            assert first["outcome"]["files"] == ["a.txt"]
            assert final["outcome"]["files"] == ["a.txt"]
            assert final["outcome"]["verification"] == "not_run"
            snapshot = InspectionStore(session.root, tmp_path).read("segment-2")
            assert snapshot["verification"]["outcome"] == final["outcome"]
            assert snapshot["verification"]["receipt"] == final["receipt"]
            h.send("run", "new-task", text="hello")
            other = (await h.until("done", "new-task"))["result"]
            assert other["outcome"]["files"] == []
        finally:
            await h.close()
    asyncio.run(scenario())


def test_inspection_persists_structured_and_raw_evidence(tmp_path):
    from app.runtime.inspection import InspectionStore
    store = InspectionStore(tmp_path / "session", tmp_path)
    facts = build_outcome(TaskState(goal="QA"))
    store.start("r", "QA").finish({"status": "done", "outcome": facts, "receipt": "raw"})
    assert store.read("r")["verification"]["outcome"] == facts
    assert store.read("r")["verification"]["receipt"] == "raw"

def test_plan_final_acceptance_updates_outcome_and_round_record(tmp_path):
    from types import SimpleNamespace
    from app.config import Config
    from app.session.store import SessionStore
    from app.stdio import _Connection
    from app.runtime.agent import AgentResult
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    agent = Agent(FakeProvider([]), ToolRegistry(tmp_path), system="test")
    agent.state = TaskState(goal="plan", file_changes=[{"path": "a"}])
    connection = _Connection(agent, Config(workspace=tmp_path), session, lambda event: None)
    context = {"verification": {"status": False, "results": {}},
               "ledger": SimpleNamespace(steps={}),
               "plan": SimpleNamespace(acceptance=lambda: SimpleNamespace(verification_plan=[]))}
    initial = AgentResult("done", 1, "done", verified=True, outcome=build_outcome(agent.state))
    final = connection._plan_result(context, initial, 0)
    assert final.verified is False
    assert final.outcome["plan_verification"] == "failed"
    assert initial.outcome.get("plan_verification") is None


def test_invalid_or_unknown_outcome_is_not_accepted():
    from core.task.outcome import valid_outcome
    facts = build_outcome(TaskState(goal=""))
    assert valid_outcome(facts)
    for patch in ({"schema_version": True}, {"schema_version": 2},
                  {"files": "a"}, {"operations": -1}, {"denied": "false"},
                  {"verification": ["passed"]}, {"plan_verification": {"status": "passed"}}):
        assert not valid_outcome({**facts, **patch})
