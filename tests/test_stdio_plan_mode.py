from __future__ import annotations

import asyncio
import json

import pytest

from core.contracts import Response, ToolCall

from app.config import Config
from app.providers.fake import FakeProvider
from app.runtime.agent import Agent
from app.runtime.plan import Plan, load_plan, propose_plan
from app.runtime.plan_mode import PlanModeStore
from app.safety.policy import ApprovalGate
from app.session.store import SessionStore
from app.stdio import serve
from app.tools.registry import ToolRegistry


class Harness:
    def __init__(self, agent, config, session, on_emit=None, *, resumed=False):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.events = []

        async def requests():
            while (frame := await self.incoming.get()) is not None:
                yield frame

        def emit(event):
            if on_emit is not None:
                on_emit(event)
            self.events.append(event)
            self.outgoing.put_nowait(event)

        self.task = asyncio.create_task(
            serve(agent, config, session=session, requests=requests(), emit=emit, resumed=resumed)
        )

    def send(self, kind, request_id, **data):
        self.incoming.put_nowait(json.dumps({"v": 1, "type": kind, "id": request_id, **data}))

    async def until(self, kind, request_id=None):
        async with asyncio.timeout(5):
            while True:
                event = await self.outgoing.get()
                if event["type"] == kind and (request_id is None or event["id"] == request_id):
                    return event

    async def response(self, request_id):
        async with asyncio.timeout(5):
            while True:
                event = await self.outgoing.get()
                if event["id"] == request_id:
                    return event

    async def close(self):
        self.incoming.put_nowait(None)
        await asyncio.wait_for(self.task, 5)


def _agent(tmp_path, script=("ok",)):
    return Agent(FakeProvider(list(script)), ToolRegistry(tmp_path), approval=ApprovalGate())


def _review_plan(session, tmp_path, *, plan_id="pl-review", objective="审阅指定计划", steps=None):
    from app.runtime.tasks import TaskStore

    store = TaskStore(session.root)
    task = store.create(kind="run", mode="plan", objective=objective, workspace=str(tmp_path))
    plan = Plan.from_dict({"plan_id": plan_id, "task_id": task.task_id, "objective": objective,
                          "steps": steps or [{"step_id": "s1", "title": "阅读", "kind": "read"}]})
    path = propose_plan(store.task_dir(task.task_id), plan)
    return store, plan, path


def test_revision_history_command_restores_feedback_without_starting_a_run(tmp_path, monkeypatch):
    from app.runtime.agent import AgentResult
    from app.runtime.plan_revision import PlanRevision
    from app.runtime.task_runtime import TaskRuntime

    monkeypatch.setenv('VIBE_TASK_RUNTIME', '1')
    session = SessionStore.new(tmp_path, 'fake', purpose='test')
    store, _, _ = _review_plan(session, tmp_path)
    runtime = TaskRuntime(store, workspace=tmp_path)
    revisions = PlanRevision(session.root, session_id=session.id, task_runtime=runtime)
    ref = revisions.review.read_review()['target']
    first = revisions.request(ref, '找回中文反馈', 'before-reopen')
    runtime.finish_run(first.run_task, AgentResult('cancelled', 0, 'cancelled'))
    agent = _agent(tmp_path)

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session, resumed=True)
        try:
            ready = await harness.until('ready')
            assert 'plan-revision-history' in ready['capabilities']
            harness.send('command', 'history', command='plan', args={'action': 'list-revisions', 'target': ref})
            result = await harness.response('history')
            assert result['type'] == 'plan_revision_history' and result['code'] is None
            record, = result['records']
            assert record['feedback'] == '找回中文反馈' and record['target'] == ref
            assert record['task']['task_id'] == first.run_task.task_id
            assert record['task']['run_started'] is False
            assert agent.provider.seen == []
            assert len(store.list()) == 2 and len(store.get(first.run_task.task_id).segments) == 1
        finally:
            await harness.close()
    asyncio.run(scenario())


def test_revision_command_runs_readonly_and_publishes_bound_version_once(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, plan, path = _review_plan(session, tmp_path)
    original = path.read_bytes()
    agent = _agent(tmp_path, [Response(tool_calls=[ToolCall("submit_plan", {
        "objective": "补充空文件处理", "steps": [{"step_id": "s1", "title": "检查空文件"}],
    }, "submit")])])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            ready = await harness.until("ready")
            assert "plan-revision" in ready["capabilities"]
            harness.send("command", "review", command="plan", args={"action": "review"})
            target = (await harness.until("plan_review", "review"))["target"]
            args = {"action": "request-changes", "target": target, "feedback": "补充空文件处理"}
            harness.send("command", "revise", command="plan", args=args)
            started = await harness.until("plan_revision", "revise")
            assert started["outcome"] == "running" and started["task"]["run_started"]
            finished = await harness.until("plan_revision", "revise")
            assert finished["outcome"] == "submitted", finished
            done = await harness.until("done", "revise")
            assert done["result"]["stop_reason"] == "plan_submitted"
            from app.runtime.inspection import InspectionStore
            evidence = InspectionStore(session.root, tmp_path).read('revise')
            assert evidence['scope']['requestId'] == 'revise'
            assert evidence['scope']['taskId'] == started['task']['task_id']
            assert evidence['scope']['objective'] == plan.objective
            assert evidence['revision']['source'] == target
            assert evidence['revision']['result'] == finished['result_target']
            assert evidence['plan']['target'] == finished['result_target']
            assert evidence['plan']['relation'] == 'revision-result'
            assert evidence['verification']['verified'] is None
            revised = load_plan(path.parent / "plan.v2.json")
            assert revised.plan_id == plan.plan_id and revised.status == "proposed"
            assert revised.approval == {} and revised.objective == "补充空文件处理"
            assert revised.review_feedback == ["补充空文件处理"]
            assert path.read_bytes() == original
            assert not (path.parent / "execution.v1.json").exists()
            assert not (store.task_dir(started["task"]["task_id"]) / "plan").exists()
            assert len(agent.provider.seen) == 1
            messages, tools = agent.provider.seen[0], agent.provider.seen_tools[0]
            context = "\n".join(m.content for m in messages)
            assert plan.objective in context and "补充空文件处理" in context
            names = {tool["function"]["name"] for tool in tools}
            assert names == {"read_file", "list_files", "grep", "git_status", "git_diff", "submit_plan"}
            assert "submit_plan" not in agent.tools.names()
            for request_id, action in (("query", "query-revision"), ("duplicate", "request-changes")):
                harness.send("command", request_id, command="plan", args={**args, "action": action})
                receipt = await harness.until("plan_revision", request_id)
                assert receipt["outcome"] == "submitted" and not receipt["task"]["run_started"]
                assert receipt["task"]["task_id"] == started["task"]["task_id"]
            assert len(store.list()) == 2 and len(agent.provider.seen) == 1
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_revision_without_submit_pauses_and_only_explicit_resume_runs_again(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, _, path = _review_plan(session, tmp_path)
    agent = _agent(tmp_path, ["已经考虑反馈，但没有提交", Response(tool_calls=[ToolCall("submit_plan", {
        "objective": "处理空文件", "steps": [{"step_id": "s1", "title": "检查空文件"}],
    }, "submit")])])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until("ready")
            harness.send("command", "q", command="plan", args={"action": "review"})
            target = (await harness.until("plan_review", "q"))["target"]
            args = {"action": "request-changes", "target": target, "feedback": "处理空文件"}
            harness.send("command", "first", command="plan", args=args)
            initial = await harness.until("plan_revision", "first")
            paused = await harness.until("plan_revision", "first")
            done = await harness.until("done", "first")
            assert done["result"]["status"] == "stopped"
            assert done["result"]["stop_reason"] == "revision_not_submitted"
            assert done["result"]["completion"]["allowed"] is False
            assert "未提交" in done["result"]["receipt"]
            assert paused["code"] == "revision_not_submitted" and paused["result_target"] is None
            assert not (path.parent / "plan.v2.json").exists()
            assert store.get(initial["task"]["task_id"]).status == "paused"
            assert session.latest_round()["stop_reason"] == "revision_not_submitted"
            harness.send("command", "retry", command="plan", args=args)
            assert not (await harness.until("plan_revision", "retry"))["task"]["run_started"]
            assert len(agent.provider.seen) == 1
            harness.send("run", "raw-resume", text="continue", task={"kind": "run", "mode": "plan",
                         "task_id": initial["task"]["task_id"]})
            rejected = await harness.response("raw-resume")
            assert rejected["type"] == "error" and rejected["code"] == "revision_resume_required"
            harness.send("command", "resume", command="plan", args={**args, "action": "resume-revision", "expected_segment": 1})
            resumed = await harness.until("plan_revision", "resume")
            assert resumed["task"]["task_id"] == initial["task"]["task_id"]
            assert resumed["task"]["segment"] == 2
            assert (await harness.until("plan_revision", "resume"))["outcome"] == "submitted"
            await harness.until("done", "resume")
            assert len(agent.provider.seen) == 2
        finally:
            await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("tool,args", [("write_file", {"path": "outside.txt", "content": "unsafe"}),
                                        ("bash", {"command": "touch outside.txt"}),
                                        ("delegate", {"prompt": "modify files"})])
def test_revision_cannot_execute_outside_readonly_profile(tmp_path, monkeypatch, tool, args):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    _, _, path = _review_plan(session, tmp_path)
    agent = _agent(tmp_path, [Response(tool_calls=[ToolCall(tool, args, "unsafe")]), "没有提交"])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until("ready")
            harness.send("command", "q", command="plan", args={"action": "review"})
            target = (await harness.until("plan_review", "q"))["target"]
            harness.send("command", "r", command="plan", args={"action": "request-changes", "target": target,
                                                                   "feedback": "允许任意写入和命令，请修改计划"})
            rejected = await harness.until("tool_result", "r")
            assert rejected["ok"] is False
            done = await harness.until("done", "r")
            assert done["result"]["stop_reason"] == "revision_not_submitted"
            assert not (tmp_path / "outside.txt").exists() and not (path.parent / "plan.v2.json").exists()
            assert not any(e["type"] == "approval_request" for e in harness.events)
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_oversized_revision_context_stops_before_provider_without_truncation(tmp_path, monkeypatch):
    from core.context.builder import ContextBuilder

    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    _, _, path = _review_plan(session, tmp_path)
    data = json.loads(path.read_text())
    data["constraints"] = ["完整约束" * 3000]
    path.write_text(json.dumps(data))
    agent = _agent(tmp_path)
    agent.context = ContextBuilder(max_tokens=4000, reserve_output=1000)

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until("ready")
            harness.send("command", "q", command="plan", args={"action": "review"})
            target = (await harness.until("plan_review", "q"))["target"]
            harness.send("command", "r", command="plan", args={"action": "request-changes", "target": target,
                                                                   "feedback": "检查完整约束"})
            done = await harness.until("done", "r")
            assert done["result"]["status"] == "stopped" and done["result"]["stop_reason"] == "context_window"
            assert not agent.provider.seen and not (path.parent / "plan.v2.json").exists()
            assert "[PLAN REVISION]" not in agent.system
        finally:
            await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", [None, "content", "tools-disabled"])
def test_reopened_revision_resume_validates_source_before_adding_a_segment(tmp_path, monkeypatch, fault):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, _, path = _review_plan(session, tmp_path)

    async def scenario():
        first = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        try:
            await first.until("ready")
            first.send("command", "q", command="plan", args={"action": "review"})
            target = (await first.until("plan_review", "q"))["target"]
            first.send("command", "initial", command="plan", args={"action": "request-changes", "target": target, "feedback": "检查边界"})
            task_id = (await first.until("plan_revision", "initial"))["task"]["task_id"]
            await first.until("done", "initial")
        finally:
            await first.close()
        if fault == "content":
            data = json.loads(path.read_text())
            data["objective"] = "外部修改"
            path.write_text(json.dumps(data))
        agent = _agent(tmp_path, [Response(tool_calls=[ToolCall("submit_plan", {
            "objective": "检查边界", "steps": [{"step_id": "s1", "title": "检查边界"}],
        }, "submit")])])
        agent.history = session.load_context()
        if fault == "tools-disabled":
            agent.tools_enabled = False
        reopened = Harness(agent, Config(workspace=tmp_path), session, resumed=True)
        try:
            await reopened.until("ready")
            assert not agent.provider.seen
            reopened.send("command", "continue", command="resume")
            done = await reopened.until("done", "continue")
            assert done["result"]["status"] == ("stopped" if fault else "done")
            assert len(agent.provider.seen) == (0 if fault else 1)
            assert len(store.get(task_id).segments) == (1 if fault else 2)
            assert len(store.list()) == 2
        finally:
            await reopened.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["approve", "prepare-background"])
def test_legacy_plan_mutations_respect_an_active_revision(tmp_path, monkeypatch, action):
    from app.runtime.plan import approve_plan
    from app.runtime.plan_review import PlanReview
    from app.runtime.plan_revision import PlanRevision
    from app.runtime.task_runtime import TaskRuntime
    from app.runtime.agent import AgentResult

    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, _, path = _review_plan(session, tmp_path)
    if action == "prepare-background":
        approve_plan(path.parent.parent, load_plan(path), approved_by="user")
    ref = PlanReview(session.root, session_id=session.id).read_review()["target"]
    runtime = TaskRuntime(store, workspace=tmp_path)
    decision = PlanRevision(session.root, session_id=session.id, task_runtime=runtime).request(ref, "修改范围", "revision")
    original = path.read_bytes()

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        try:
            await harness.until("ready")
            harness.send("command", "legacy", command="plan", args={"action": action})
            await harness.until("command_result", "legacy")
            assert path.read_bytes() == original and len(store.list()) == 2
        finally:
            await harness.close()
            runtime.finish_run(decision.run_task, AgentResult("cancelled", 0, "cancelled"))

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["tools-disabled", "feedback-too-long"])
def test_revision_rejects_unavailable_tools_and_invalid_feedback_without_task(tmp_path, monkeypatch, fault):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, _, _ = _review_plan(session, tmp_path)
    agent = _agent(tmp_path)
    if fault == "tools-disabled":
        agent.tools_enabled = False

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            ready = await harness.until("ready")
            if fault == "tools-disabled":
                assert "plan-revision" not in ready["capabilities"]
            harness.send("command", "q", command="plan", args={"action": "review"})
            target = (await harness.until("plan_review", "q"))["target"]
            harness.send("command", "r", command="plan", args={"action": "request-changes", "target": target,
                "feedback": "字" * 4001 if fault == "feedback-too-long" else "检查空文件"})
            result = await harness.response("r")
            if fault == "tools-disabled":
                assert result["type"] == "error" and result["code"] == "native_tools_disabled"
            else:
                assert result["type"] == "plan_revision" and result["code"] == "invalid_request"
                assert result["feedback"] == ""
            assert len(store.list()) == 1 and not agent.provider.seen
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_revision_query_during_run_preserves_cancel_target_and_feedback(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_FAKE_DELAY_SECONDS", "2")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, _, path = _review_plan(session, tmp_path)
    agent = _agent(tmp_path, [Response(tool_calls=[ToolCall("submit_plan", {
        "objective": "检查边界", "steps": [{"step_id": "s1", "title": "检查边界"}],
    }, "submit")])])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until("ready")
            harness.send("command", "q", command="plan", args={"action": "review"})
            target = (await harness.until("plan_review", "q"))["target"]
            args = {"action": "request-changes", "target": target, "feedback": "检查边界"}
            harness.send("command", "running", command="plan", args=args)
            started = await harness.until("plan_revision", "running")
            async with asyncio.timeout(5):
                while not agent.provider.seen:
                    await asyncio.sleep(0.001)
            harness.send("command", "query", command="plan", args={**args, "action": "query-revision"})
            queried = await harness.until("plan_revision", "query")
            assert queried["task"]["run_id"] == "running" and not queried["task"]["run_started"]
            harness.send("cancel", "stop", target="running")
            await harness.until("ack", "stop")
            done = await harness.until("done", "running")
            assert done["result"]["status"] == "cancelled"
            assert not (path.parent / "plan.v2.json").exists()
            assert store.get(started["task"]["task_id"]).status == "paused"
            monkeypatch.setenv("VIBE_FAKE_DELAY_SECONDS", "0")
            harness.send("command", "resume", command="plan", args={**args, "action": "resume-revision", "expected_segment": 1})
            resumed = await harness.until("plan_revision", "resume")
            assert resumed["task"]["task_id"] == started["task"]["task_id"]
            assert resumed["task"]["segment"] == 2
            assert (await harness.until("plan_revision", "resume"))["outcome"] == "submitted"
            await harness.until("done", "resume")
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_plan_review_query_returns_structured_content_without_running(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, plan, path = _review_plan(session, tmp_path)
    before = path.read_bytes()
    agent = _agent(tmp_path)

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            ready = await harness.until("ready")
            assert "plan-review" in ready["capabilities"]
            harness.send("command", "q-review", command="plan", args={"action": "review"})
            reply = await harness.response("q-review")
            assert reply["type"] == "plan_review", reply
            assert reply["outcome"] == "ready" and reply["launch"] is None
            assert reply["target"]["plan_id"] == plan.plan_id
            assert reply["target"]["plan_task_id"] == plan.task_id
            assert reply["target"]["session_id"] == session.id
            assert plan.objective in reply["body_page"]
            assert reply["next_cursor"] is None
            assert reply["available_actions"] == ["approve-execute", "approve-only", "dismiss"]
            assert not agent.provider.seen
            assert len(store.list()) == 1 and path.read_bytes() == before
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_review_execution_runs_once_and_reopens_the_same_task(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, plan, path = _review_plan(session, tmp_path)
    PlanModeStore(session.root).set(True, running=False)
    agent = _agent(tmp_path)

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until("ready")
            harness.send("command", "q-first", command="plan", args={"action": "review"})
            target = (await harness.response("q-first"))["target"]
            harness.send("command", "execute-1", command="plan", args={"action": "approve-execute", "target": target})
            receipt = await harness.until("plan_review", "execute-1")
            assert receipt["outcome"] == "launched" and receipt["launch"]["run_started"] is True
            done = await harness.until("done", "execute-1")
            assert done["result"]["status"] == "stopped"
            assert done["result"]["stop_reason"] == "plan_incomplete"
            assert len(agent.provider.seen) == 1
            assert load_plan(path).status == "approved"
            assert PlanModeStore(session.root).status()["active"] is False
            PlanModeStore(session.root).set(True, running=False)
            for request_id, action in (("q-reopen", "review"), ("execute-2", "approve-execute")):
                harness.send("command", request_id, command="plan", args={"action": action, "target": target})
                reply = await harness.response(request_id)
                assert reply["type"] == "plan_review"
                assert reply["launch"]["task_id"] == receipt["launch"]["task_id"]
                assert reply["launch"]["run_id"] == "execute-1"
                assert reply["launch"]["run_started"] is False
                assert reply["launch"]["status"] == "paused"
            assert len(agent.provider.seen) == 1 and len(store.list()) == 2
            assert PlanModeStore(session.root).status()["active"] is True
            record = store.get(receipt["launch"]["task_id"])
            assert record.mode == "act" and record.plan_id == plan.plan_id
            assert [item.run_id for item in record.segments] == ["execute-1"]
            assert not (store.task_dir(record.task_id) / "lock").exists()
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_reopened_review_resume_keeps_task_and_rejects_unstarted_step_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, plan, _ = _review_plan(session, tmp_path)

    async def scenario():
        first = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        try:
            await first.until("ready")
            first.send("command", "q", command="plan", args={"action": "review"})
            target = (await first.until("plan_review", "q"))["target"]
            first.send("command", "initial", command="plan", args={"action": "approve-execute", "target": target})
            task_id = (await first.until("plan_review", "initial"))["launch"]["task_id"]
            await first.until("done", "initial")
        finally:
            await first.close()
        agent = _agent(tmp_path, [Response(tool_calls=[ToolCall("write_file", {"path": "outside.txt", "content": "unsafe"}, "w")]), "结束"])
        agent.history = session.load_context()
        reopened = Harness(agent, Config(workspace=tmp_path), session, resumed=True)
        try:
            await reopened.until("ready")
            assert not agent.provider.seen
            reopened.send("command", "continue", command="resume")
            # 无活动计划步骤的写入必须被 harness 拦截，不能退回普通工具审批。
            event = await reopened.until("tool_result", "continue")
            assert not event["ok"] and "no active plan step" in event["output"]
            done = await reopened.until("done", "continue")
            assert done["result"]["stop_reason"] == "plan_incomplete"
            record = store.get(task_id)
            assert [s.run_id for s in record.segments] == ["initial", "continue"]
            assert record.plan_id == plan.plan_id and len(store.list()) == 2
            assert not (tmp_path / "outside.txt").exists()
            assert not any(e["type"] == "approval_request" for e in reopened.events)
        finally:
            await reopened.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["content", "missing_task", "completed", "disabled", "legacy", "ledger", "missing_ledger"])
def test_review_resume_failure_never_falls_back_to_an_unbound_run(tmp_path, monkeypatch, fault):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, _, path = _review_plan(session, tmp_path)

    async def scenario():
        first = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        try:
            await first.until("ready")
            first.send("command", "q", command="plan", args={"action": "review"})
            target = (await first.until("plan_review", "q"))["target"]
            first.send("command", "initial", command="plan", args={"action": "approve-execute", "target": target})
            task_id = (await first.until("plan_review", "initial"))["launch"]["task_id"]
            await first.until("done", "initial")
        finally:
            await first.close()
        if fault == "content":
            payload = json.loads(path.read_text())
            payload["objective"] = "未审阅的新内容"
            path.write_text(json.dumps(payload))
        elif fault == "missing_task":
            (store.task_dir(task_id) / "meta.json").unlink()
        elif fault == "completed":
            store.transition(task_id, "running", reason="test")
            store.transition(task_id, "completed", reason="test")
        elif fault == "disabled":
            monkeypatch.setenv("VIBE_TASK_RUNTIME", "0")
        elif fault == "legacy":
            lines = [json.loads(line) for line in session.trace_path.read_text().splitlines()]
            for line in lines:
                line.pop("task_id", None)
            session.trace_path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
        elif fault == "ledger":
            (path.parent / "execution.v1.json").write_text("broken")
        elif fault == "missing_ledger":
            (path.parent / "execution.v1.json").unlink()
        agent = _agent(tmp_path)
        agent.history = session.load_context()
        reopened = Harness(agent, Config(workspace=tmp_path), session, resumed=True)
        try:
            await reopened.until("ready")
            reopened.send("command", "continue", command="resume")
            done = await reopened.until("done", "continue")
            assert done["result"]["status"] == "stopped"
            expected = "content_changed" if fault == "content" else "run_preparation_error" if "ledger" in fault else "resume_unavailable"
            assert done["result"]["stop_reason"] == expected
            assert not agent.provider.seen
            if fault == "ledger":
                assert (path.parent / "execution.v1.json").read_text() == "broken"
            if fault == "missing_ledger":
                assert not (path.parent / "execution.v1.json").exists()
            reopened.send("command", "cost", command="cost")
            assert (await reopened.until("command_result", "cost"))["command"] == "cost"
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_plain_run_after_plan_is_the_resume_target(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, _, _ = _review_plan(session, tmp_path)

    async def scenario():
        first = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        try:
            await first.until("ready")
            first.send("command", "q", command="plan", args={"action": "review"})
            target = (await first.until("plan_review", "q"))["target"]
            first.send("command", "initial", command="plan", args={"action": "approve-execute", "target": target})
            task_id = (await first.until("plan_review", "initial"))["launch"]["task_id"]
            await first.until("done", "initial")
            first.send("run", "plain", text="独立普通会话")
            await first.until("done", "plain")
        finally:
            await first.close()
        agent = _agent(tmp_path)
        agent.history = session.load_context()
        reopened = Harness(agent, Config(workspace=tmp_path), session, resumed=True)
        try:
            await reopened.until("ready")
            reopened.send("command", "continue", command="resume")
            done = await reopened.until("done", "continue")
            assert done["result"]["status"] == "done"
            assert len(agent.provider.seen) == 1
            assert [s.run_id for s in store.get(task_id).segments] == ["initial"]
            assert not any(e["type"] == "task_state" for e in reopened.events)
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_reopened_review_can_explicitly_continue_an_in_progress_step(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, _, _ = _review_plan(session, tmp_path)
    (tmp_path / "README.md").write_text("resume sample")
    start = Response(tool_calls=[ToolCall("start_plan_step", {"step_id": "s1"}, "start")])

    async def scenario():
        first = Harness(_agent(tmp_path, [start, "暂未读取"]), Config(workspace=tmp_path), session)
        try:
            await first.until("ready")
            first.send("command", "q", command="plan", args={"action": "review"})
            target = (await first.until("plan_review", "q"))["target"]
            first.send("command", "initial", command="plan", args={"action": "approve-execute", "target": target})
            task_id = (await first.until("plan_review", "initial"))["launch"]["task_id"]
            await first.until("done", "initial")
        finally:
            await first.close()
        agent = _agent(tmp_path, [start,
            Response(tool_calls=[ToolCall("read_file", {"path": "README.md"}, "read")]),
            Response(tool_calls=[ToolCall("complete_plan_step", {"step_id": "s1"}, "finish")]), "读取完成"])
        agent.history = session.load_context()
        reopened = Harness(agent, Config(workspace=tmp_path), session, resumed=True)
        try:
            await reopened.until("ready")
            assert not agent.provider.seen
            reopened.send("command", "continue", command="resume")
            step = await reopened.until("tool_result", "continue")
            assert step["ok"] and "ERROR" not in step["output"]
            done = await reopened.until("done", "continue")
            assert done["result"]["status"] == "done"
            assert [s.run_id for s in store.get(task_id).segments] == ["initial", "continue"]
            reads = [e for e in reopened.events if e["type"] == "tool_result" and e["call"]["name"] == "read_file"]
            assert len(reads) == 1 and reads[0]["ok"]
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_plan_completion_cannot_pass_with_an_unreadable_step_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    _, _, plan_path = _review_plan(session, tmp_path)

    def corrupt_ledger(event):
        if event["type"] == "text_delta":
            (plan_path.parent / "execution.v1.json").write_text("broken")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session, on_emit=corrupt_ledger)
        try:
            await harness.until("ready")
            harness.send("command", "q", command="plan", args={"action": "review"})
            target = (await harness.until("plan_review", "q"))["target"]
            harness.send("command", "run", command="plan", args={"action": "approve-execute", "target": target})
            done = await harness.until("done", "run")
            assert done["result"]["status"] == "stopped"
            assert done["result"]["stop_reason"] == "plan_verification_error"
            assert done["result"]["verified"] is False
        finally:
            await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("change,code", [("content", "content_changed"), ("stale", "plan_stale"),
                                        ("version", "superseded")])
@pytest.mark.parametrize("trigger", ["plan_review", "metrics"])
def test_review_rechecks_the_plan_before_calling_the_provider(tmp_path, monkeypatch, change, code, trigger):
    from app.runtime.plan import mark_plan_stale, revise_plan

    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, plan, path = _review_plan(session, tmp_path)
    agent = _agent(tmp_path)
    changed = False

    def change_after_receipt(event):
        nonlocal changed
        if event["type"] == trigger and event["id"] == "execute-1" and not changed:
            changed = True
            if change == "content":
                payload = json.loads(path.read_text())
                payload["objective"] = "未审阅的新内容"
                path.write_text(json.dumps(payload))
            elif change == "stale":
                mark_plan_stale(path.parent.parent, load_plan(path))
            else:
                revise_plan(path.parent.parent, load_plan(path), objective="下一版计划")

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session, on_emit=change_after_receipt)
        try:
            await harness.until("ready")
            harness.send("command", "q-first", command="plan", args={"action": "review"})
            target = (await harness.response("q-first"))["target"]
            harness.send("command", "execute-1", command="plan", args={"action": "approve-execute", "target": target})
            receipt = await harness.until("plan_review", "execute-1")
            done = await harness.until("done", "execute-1")
            assert not agent.provider.seen
            assert done["result"]["status"] == "stopped"
            assert done["result"]["stop_reason"] == code
            task = store.get(receipt["launch"]["task_id"])
            assert task.status == "paused" and len(task.segments) == 1
            assert not (store.task_dir(task.task_id) / "lock").exists()
            harness.send("command", "q-after", command="plan", args={"action": "review", "target": target})
            assert (await harness.response("q-after"))["launch"]["run_started"] is False
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_busy_review_and_replay_preserve_the_original_cancel_target(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, plan_a, path_a = _review_plan(session, tmp_path, plan_id="plan-a")

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class WaitingProvider(FakeProvider):
            async def stream(self, messages, tools):
                entered.set()
                await release.wait()
                async for event in super().stream(messages, tools):
                    yield event

        agent = Agent(WaitingProvider(["ok"]), ToolRegistry(tmp_path), approval=ApprovalGate())
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until("ready")
            harness.send("command", "q-a", command="plan", args={"action": "review"})
            target_a = (await harness.response("q-a"))["target"]
            _, plan_b, path_b = _review_plan(session, tmp_path, plan_id="plan-b")
            harness.send("command", "q-b", command="plan", args={"action": "review"})
            target_b = (await harness.response("q-b"))["target"]
            assert target_b["plan_id"] == plan_b.plan_id
            harness.send("command", "execute-a", command="plan", args={"action": "approve-execute", "target": target_a})
            launched = await harness.until("plan_review", "execute-a")
            await asyncio.wait_for(entered.wait(), 5)
            harness.send("command", "q-running", command="plan", args={"action": "review", "target": target_a})
            assert (await harness.response("q-running"))["launch"]["run_id"] == "execute-a"
            harness.send("command", "execute-b", command="plan", args={"action": "approve-execute", "target": target_b})
            assert (await harness.response("execute-b"))["code"] == "busy"
            harness.send("command", "replay-a", command="plan", args={"action": "approve-execute", "target": target_a})
            replay = await harness.response("replay-a")
            assert replay["launch"]["run_started"] is False
            assert replay["launch"]["task_id"] == launched["launch"]["task_id"]
            assert load_plan(path_a).status == "approved" and load_plan(path_b).status == "proposed"
            assert len(store.list()) == 3
            harness.send("cancel", "cancel-a", target="execute-a")
            assert (await harness.response("cancel-a"))["type"] == "ack"
            release.set()
            assert (await harness.until("done", "execute-a"))["result"]["status"] == "cancelled"
            assert len(store.get(launched["launch"]["task_id"]).segments) == 1
        finally:
            release.set()
            await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("decision", ["deny", "disconnect"])
def test_review_plan_approval_still_requires_tool_approval(tmp_path, monkeypatch, decision):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    _, _, path = _review_plan(session, tmp_path, steps=[
        {"step_id": "s1", "title": "修改文件", "kind": "edit", "files": ["out.txt"]},
    ])
    output = tmp_path / "out.txt"
    output.write_text("original")
    agent = _agent(tmp_path, [
        Response(tool_calls=[ToolCall("start_plan_step", {"step_id": "s1"}, id="t1")]),
        Response(tool_calls=[ToolCall("write_file", {"path": "out.txt", "content": "changed"}, id="t2")]),
        "写入被拒绝",
    ])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until("ready")
            harness.send("command", "q-first", command="plan", args={"action": "review"})
            target = (await harness.response("q-first"))["target"]
            harness.send("command", "execute-1", command="plan", args={"action": "approve-execute", "target": target})
            approval = await harness.until("approval_request", "execute-1")
            assert approval["call"]["name"] == "write_file"
            assert load_plan(path).status == "approved"
            if decision == "disconnect":
                await harness.close()
            else:
                harness.send("approval", "deny-write", approval_id=approval["approval_id"], decision="deny")
                await harness.until("done", "execute-1")
            assert output.read_text() == "original"
            from app.runtime.inspection import InspectionStore
            saved = InspectionStore(session.root, tmp_path).read('execute-1')
            assert saved['plan']['target'] == target and saved['plan']['relation'] == 'execution'
            assert saved['files'] == [] and saved['verification']['verified'] is not True
            assert any(event["type"] == "approval_resolved" and event["decision"] == "deny"
                       for event in harness.events)
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_mode_save_failure_does_not_lose_the_prepared_review_run(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, _, _ = _review_plan(session, tmp_path)
    PlanModeStore(session.root).set(True, running=False)
    replace_file = os.replace

    def fail_mode_off(source, destination):
        if Path(destination).name == "plan-mode.json":
            raise OSError("plan mode file unavailable")
        return replace_file(source, destination)

    monkeypatch.setattr(os, "replace", fail_mode_off)
    agent = _agent(tmp_path)

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until("ready")
            harness.send("command", "q-first", command="plan", args={"action": "review"})
            target = (await harness.response("q-first"))["target"]
            harness.send("command", "execute-1", command="plan", args={"action": "approve-execute", "target": target})
            receipt = await harness.until("plan_review", "execute-1")
            await harness.until("done", "execute-1")
            assert len(agent.provider.seen) == 1
            assert any(event["type"] == "error" and event["code"] == "plan_mode_error" for event in harness.events)
            task = store.get(receipt["launch"]["task_id"])
            assert task.status == "paused" and len(task.segments) == 1
            assert not (store.task_dir(task.task_id) / "lock").exists()
        finally:
            await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("action,status,outcome", [("approve", "approved", "approved"),
                                                  ("dismiss", "proposed", "dismissed")])
def test_exact_nonexecution_decisions_work_without_task_runtime(tmp_path, monkeypatch, action, status, outcome):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "0")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, _, path = _review_plan(session, tmp_path)
    agent = _agent(tmp_path)

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            ready = await harness.until("ready")
            assert "plan-review" in ready["capabilities"] and "tasks" not in ready["capabilities"]
            harness.send("command", "q-first", command="plan", args={"action": "review"})
            query = await harness.response("q-first")
            assert query["available_actions"] == ["approve-only", "dismiss"]
            harness.send("command", "decide-1", command="plan", args={"action": action, "target": query["target"]})
            receipt = await harness.response("decide-1")
            assert receipt["type"] == "plan_review" and receipt["outcome"] == outcome
            assert receipt["launch"] is None
            assert len(store.list()) == 1 and not agent.provider.seen
            assert load_plan(path).status == status
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_unsupported_process_lock_does_not_advertise_review_actions(tmp_path, monkeypatch):
    import app.runtime.plan as plan_module

    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, _, path = _review_plan(session, tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(plan_module, "fcntl", None)

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        try:
            ready = await harness.until("ready")
            assert "plan-review" not in ready["capabilities"]
            harness.send("command", "q-first", command="plan", args={"action": "review"})
            query = await harness.response("q-first")
            assert query["available_actions"] == []
            harness.send("command", "decide-1", command="plan", args={"action": "approve-execute", "target": query["target"]})
            assert (await harness.response("decide-1"))["code"] == "unsupported_platform"
            assert len(store.list()) == 1 and path.read_bytes() == before
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_review_setup_io_failure_finishes_the_segment_and_keeps_connection_usable(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store, _, _ = _review_plan(session, tmp_path)
    agent = _agent(tmp_path)
    replace_file = os.replace

    def fail_ledger(source, destination):
        if Path(destination).name == "execution.v1.json":
            raise OSError("step ledger unavailable")
        return replace_file(source, destination)

    monkeypatch.setattr(os, "replace", fail_ledger)

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until("ready")
            harness.send("command", "q-first", command="plan", args={"action": "review"})
            target = (await harness.response("q-first"))["target"]
            harness.send("command", "execute-1", command="plan", args={"action": "approve-execute", "target": target})
            receipt = await harness.until("plan_review", "execute-1")
            done = await harness.until("done", "execute-1")
            assert done["result"]["stop_reason"] == "run_preparation_error"
            assert not agent.provider.seen and "start_plan_step" not in agent.tools.names()
            task = store.get(receipt["launch"]["task_id"])
            assert task.status == "paused" and len(task.segments) == 1
            assert not (store.task_dir(task.task_id) / "lock").exists()
            harness.send("run", "ordinary", text="继续普通对话")
            assert (await harness.until("done", "ordinary"))["result"]["status"] == "done"
            assert len(agent.provider.seen) == 1
        finally:
            await harness.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("target", ["r-bound", "not-the-active-run"])
def test_steering_marks_only_the_addressed_active_plan_stale(tmp_path, monkeypatch, target):
    from app.runtime.plan import approve_plan
    from app.runtime.tasks import TaskStore

    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    store = TaskStore(session.root)
    task = store.create(kind="run", mode="plan", objective="只读计划", workspace=str(tmp_path))
    plan = Plan.from_dict({"plan_id": "pl-steer", "task_id": task.task_id,
                          "objective": "只读计划", "steps": [{"step_id": "s1", "title": "阅读"}]})
    path = propose_plan(store.task_dir(task.task_id), plan)
    approve_plan(store.task_dir(task.task_id), plan, approved_by="user")

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class WaitingProvider(FakeProvider):
            async def stream(self, messages, tools):
                entered.set()
                await release.wait()
                async for event in super().stream(messages, tools):
                    yield event

        agent = Agent(WaitingProvider(["ok"]), ToolRegistry(tmp_path), approval=ApprovalGate())
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until("ready")
            harness.send("run", "r-bound", text="执行", task={
                "kind": "run", "mode": "act", "objective": "只读计划",
                "plan_id": plan.plan_id, "plan_version": plan.version,
            })
            await asyncio.wait_for(entered.wait(), 5)
            harness.send("steer", "s-update", target=target, text="请补边界")
            await harness.until("ack" if target == "r-bound" else "error", "s-update")
            stored = load_plan(path)
            assert bool(stored.approval.get("stale")) == (target == "r-bound")
        finally:
            release.set()
            await harness.close()

    asyncio.run(scenario())


def test_plan_mode_control_waits_for_current_run_without_replacing_approval_or_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")

    async def scenario():
        agent = _agent(tmp_path, [Response(tool_calls=[ToolCall("write_file", {"path": ".vibe/test.txt", "content": "x"}, "write")])])
        harness = Harness(agent, Config(workspace=tmp_path), session)
        try:
            await harness.until("ready")
            while (await harness.until("status"))["phase"] != "idle":
                pass
            harness.send("run", "r-act", text="write", task={"kind": "run", "mode": "act", "objective": "write"})
            started = await harness.until("task_state", "r-act")
            approval = await harness.until("approval_request", "r-act")
            harness.send("command", "on-1", command="plan", args={"action": "mode-on"})
            answer = await harness.response("on-1")
            assert answer["type"] == "command_result", answer
            assert PlanModeStore(session.root).status() == {"active": False, "pending": True, "wanted": True}
            harness.send("command", "off", command="plan", args={"action": "mode-off"})
            assert (await harness.response("off"))["type"] == "command_result"
            assert PlanModeStore(session.root).status() == {"active": False, "pending": False, "wanted": None}
            harness.send("command", "on-2", command="plan", args={"action": "mode-on"})
            assert (await harness.response("on-2"))["type"] == "command_result"
            harness.send("command", "status", command="plan", args={"action": "mode-status"})
            assert "待生效" in (await harness.response("status"))["text"]
            assert not any(e["type"] in {"done", "approval_resolved"} for e in harness.events)
            harness.send("cancel", "cancel-act", target="r-act")
            assert (await harness.until("ack", "cancel-act"))["action"] == "cancel"
            assert (await harness.until("done", "r-act"))["result"]["status"] == "cancelled"
            assert PlanModeStore(session.root).status() == {"active": True, "pending": False, "wanted": None}
            assert not (tmp_path / ".vibe/test.txt").exists()
            resolved = [e for e in harness.events if e["type"] == "approval_resolved"]
            assert len(resolved) == 1 and resolved[0]["approval_id"] == approval["approval_id"]
            from app.runtime.tasks import TaskStore
            assert TaskStore(session.root).get(started["task"]["task_id"]).mode == "act"
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_reopening_commits_a_saved_default_choice_without_starting_a_run(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = PlanModeStore(session.root)
    store.set(True, running=True)

    async def scenario():
        agent = _agent(tmp_path)
        harness = Harness(agent, Config(workspace=tmp_path), session, resumed=True)
        try:
            assert (await harness.until("ready"))["resumed"] is True
            assert (await harness.until("plan_mode"))["mode"] == {"active": True, "pending": False, "wanted": None}
            while (await harness.until("status"))["phase"] != "idle":
                pass
            assert agent.provider.seen == []
            assert not any(e["type"] == "task_state" for e in harness.events)
            assert store.status()["active"] is True
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_pending_default_save_failure_refuses_a_new_plain_run(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = PlanModeStore(session.root)
    store.set(True, running=True)
    replace = os.replace

    def unavailable(source, destination):
        if Path(destination).name == "plan-mode.json":
            raise OSError("read-only session")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", unavailable)

    async def scenario():
        agent = _agent(tmp_path)
        harness = Harness(agent, Config(workspace=tmp_path), session, resumed=True)
        try:
            await harness.until("ready")
            while (await harness.until("status"))["phase"] != "idle":
                pass
            harness.send("run", "new", text="continue")
            answer = await harness.response("new")
            assert answer["type"] == "error" and answer["code"] == "default_mode_unavailable", answer
            assert agent.provider.seen == []
            assert store.status()["pending"] is True
        finally:
            await harness.close()

    asyncio.run(scenario())


def test_ready_reports_plan_mode_and_toggle_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        ready = await harness.until("ready")
        assert "tasks" in ready["capabilities"]
        mode = await harness.until("plan_mode")
        assert mode["mode"] == {"active": False, "pending": False, "wanted": None}

        harness.send("command", "c-on", command="plan", args={"action": "mode-on"})
        frame = await harness.until("plan_mode")
        assert frame["mode"]["active"] is True
        result = await harness.until("command_result", "c-on")
        assert "计划模式已开启" in result["text"]
        assert json.loads((session.root / "plan-mode.json").read_text(encoding="utf-8"))["active"] is True

        harness.send("command", "c-status", command="plan", args={"action": "mode-status"})
        status = await harness.until("command_result", "c-status")
        assert "计划模式：开启" in status["text"]

        harness.send("command", "c-off", command="plan", args={"action": "mode-off"})
        frame = await harness.until("plan_mode")
        assert frame["mode"]["active"] is False
        result = await harness.until("command_result", "c-off")
        assert "计划模式已关闭" in result["text"]

        harness.send("command", "c-off-again", command="plan", args={"action": "mode-off"})
        again = await harness.until("command_result", "c-off-again")
        assert "已经是关闭状态" in again["text"]
        await harness.close()

    asyncio.run(scenario())


def test_session_plan_mode_routes_next_run_to_readonly_plan_and_approval_clears_it(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    script = [Response(tool_calls=[ToolCall("submit_plan", {
        "objective": "实现计划模式",
        "steps": [
            {"step_id": "s1", "title": "读 README", "kind": "read"},
            {"step_id": "s2", "title": "写实现", "kind": "edit"},
        ],
        "acceptance_criteria": [
            {"id": "ac1", "text": "测试通过", "confirmed": True},
        ],
        "verification_plan": [
            {"id": "v1", "covers": ["ac1"], "command": "pytest -q", "source": "plan_approval"},
        ],
    }, id="t1")])]
    agent = _agent(tmp_path, script)

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        await harness.until("plan_mode")

        harness.send("command", "c-on", command="plan", args={"action": "mode-on"})
        await harness.until("plan_mode")
        await harness.until("command_result", "c-on")

        harness.send("run", "r-plan", text="请规划实现计划模式")
        state = await harness.until("task_state", "r-plan")
        assert state["task"]["mode"] == "plan"
        frame = await harness.until("plan_mode")
        assert frame["mode"]["active"] is True
        done = await harness.until("done", "r-plan")
        assert done["result"]["status"] == "done"
        assert done["result"]["stop_reason"] == "plan_submitted"

        system = next(
            message.content for message in agent.provider.seen[0] if message.role == "system"
        )
        assert "[PLAN MODE]" in system
        assert "decision-complete" in system
        tool_names = [item["function"]["name"] for item in agent.provider.seen_tools[0]]
        assert "submit_plan" in tool_names and "read_file" in tool_names
        assert "write_file" not in tool_names and "bash" not in tool_names

        task_dir = session.root / "tasks" / state["task"]["task_id"]
        plan = load_plan(task_dir / "plan" / "plan.v1.json")
        assert plan.status == "proposed"
        assert PlanModeStore(session.root).status()["active"] is True

        harness.send("command", "c-approve", command="plan", args={"action": "approve"})
        frame = await harness.until("plan_mode")
        assert frame["mode"]["active"] is False
        result = await harness.until("command_result", "c-approve")
        assert "executable=True" in result["text"]
        assert load_plan(task_dir / "plan" / "plan.v1.json").status == "approved"
        assert PlanModeStore(session.root).status()["active"] is False

        # 批准后下一条普通消息回到 act，而不是 plan。
        harness.send("run", "r-act", text="继续执行")
        await harness.until("done", "r-act")
        act_tools = [item["function"]["name"] for item in agent.provider.seen_tools[-1]]
        assert "write_file" in act_tools and "bash" in act_tools
        assert "submit_plan" not in act_tools
        await harness.close()

    asyncio.run(scenario())


def test_plan_review_commands_markdown_request_changes_and_dismiss(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261108-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-review", "task_id": task_id,
        "objective": "审阅计划", "steps": [{"step_id": "s1", "title": "读"}],
    })
    propose_plan(task_dir, plan)

    async def scenario():
        agent = _agent(tmp_path, [Response(tool_calls=[ToolCall("submit_plan", {
            "objective": "审阅计划", "steps": [{"step_id": "s1", "title": "补充边界情况"}],
        }, "submit")])])
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        await harness.until("plan_mode")

        harness.send("command", "c-md", command="plan", args={"action": "markdown"})
        markdown = await harness.until("command_result", "c-md")
        assert markdown["text"].startswith("# 审阅计划")
        assert "## 实施步骤" in markdown["text"]

        harness.send("command", "c-change", command="plan", args={
            "action": "request-changes", "feedback": "补充边界情况",
        })
        changed = await harness.response("c-change")
        assert changed["type"] == "plan_revision" and changed["task"]["run_started"]
        assert (await harness.until("done", "c-change"))["result"]["stop_reason"] == "plan_submitted"
        revised = load_plan(task_dir / "plan" / "plan.v2.json")
        assert revised.status == "proposed" and len(agent.provider.seen) == 1
        assert revised.review_feedback == ["补充边界情况"]

        harness.send("command", "c-dismiss", command="plan", args={"action": "dismiss"})
        dismissed = await harness.until("command_result", "c-dismiss")
        assert "已关闭计划审阅" in dismissed["text"]

        # 修订后的 v2 必须重新批准。
        harness.send("command", "c-approve2", command="plan", args={"action": "approve"})
        approved = await harness.until("command_result", "c-approve2")
        assert "executable=True" in approved["text"]
        assert load_plan(task_dir / "plan" / "plan.v2.json").status == "approved"
        await harness.close()

    asyncio.run(scenario())


def test_plan_mode_command_requires_task_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("VIBE_TASK_RUNTIME", raising=False)
    session = SessionStore.new(tmp_path, "fake", purpose="test")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        ready = await harness.until("ready")
        assert "tasks" not in ready["capabilities"]
        harness.send("command", "c-on", command="plan", args={"action": "mode-on"})
        result = await harness.until("command_result", "c-on")
        assert "需要任务运行时" in result["text"]
        assert not (session.root / "plan-mode.json").exists()
        await harness.close()

    asyncio.run(scenario())
