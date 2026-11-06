"""stdio + TaskRuntime 接入测试（spec: docs/specs/task-runtime-integration.md）。"""

from __future__ import annotations

import asyncio
import json
import os
import socket

from app.config import Config
from app.providers.fake import FakeProvider
from app.runtime.agent import Agent
from app.runtime.approvals import ApprovalInbox
from app.runtime.task_runtime import TaskRuntime
from app.runtime.tasks import TaskStore
from app.safety.policy import ApprovalGate
from app.session.store import SessionStore
from app.stdio import serve
from app.tools.registry import ToolRegistry


class Harness:
    def __init__(self, agent, config, session):
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.events = []

        async def requests():
            while (frame := await self.incoming.get()) is not None:
                yield frame

        def emit(event):
            self.events.append(event)
            self.outgoing.put_nowait(event)

        self.task = asyncio.create_task(
            serve(agent, config, session=session, requests=requests(), emit=emit)
        )

    def send(self, kind, request_id, **data):
        self.incoming.put_nowait(json.dumps({"v": 1, "type": kind, "id": request_id, **data}))

    async def until(self, kind, request_id=None):
        async with asyncio.timeout(5):
            while True:
                event = await self.outgoing.get()
                if event["type"] == kind and (request_id is None or event["id"] == request_id):
                    return event

    async def close(self):
        self.incoming.put_nowait(None)
        await asyncio.wait_for(self.task, 5)


def _agent(tmp_path, script=("ok",)):
    return Agent(FakeProvider(list(script)), ToolRegistry(tmp_path), approval=ApprovalGate())


def _task_dir(session, task_id):
    return session.root / "tasks" / task_id


def test_goal_run_creates_task(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        ready = await harness.until("ready")
        assert "tasks" in ready["capabilities"]

        harness.send("run", "r1", text="目标", task={"kind": "goal", "objective": "目标"})
        state = await harness.until("task_state", "r1")
        task_id = state["task"]["task_id"]
        assert state["task"]["status"] == "running"
        assert state["task"]["segment"] == 1
        done = await harness.until("done", "r1")
        assert done["result"]["status"] == "done"

        meta = json.loads((_task_dir(session, task_id) / "meta.json").read_text(encoding="utf-8"))
        assert meta["kind"] == "goal" and meta["status"] == "completed"
        assert meta["segments"][0]["run_id"] == "r1"
        goal = json.loads((session.root / "goal-record.json").read_text(encoding="utf-8"))
        assert goal["task_id"] == task_id
        assert goal["state"] == "completed"
        assert goal["segments"][0]["run_id"] == "r1"
        budget = [json.loads(line) for line in (session.root / "budget.jsonl").read_text(
            encoding="utf-8").splitlines() if line.strip()]
        assert any(entry["entry_kind"] == "task_close" and entry["terminal_status"] == "completed"
                   for entry in budget)
        assert any(entry["entry_kind"] == "llm" for entry in budget)
        state_index = next(index for index, event in enumerate(harness.events)
                           if event["type"] == "task_state" and event["id"] == "r1")
        done_index = next(index for index, event in enumerate(harness.events)
                          if event["type"] == "done" and event["id"] == "r1")
        assert state_index < done_index
        await harness.close()

    asyncio.run(scenario())


def test_plain_run_does_not_create_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        harness.send("run", "r1", text="普通任务")
        await harness.until("done", "r1")
        assert not (session.root / "tasks").exists()
        await harness.close()

    asyncio.run(scenario())


def test_runtime_disabled_rejects_task_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "0")
    session = SessionStore.new(tmp_path, "fake", purpose="user")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        ready = await harness.until("ready")
        assert "tasks" not in ready["capabilities"]
        harness.send("run", "r1", text="目标", task={"kind": "goal", "objective": "目标"})
        error = await harness.until("error", "r1")
        assert error["code"] == "task_runtime_disabled"
        assert not (session.root / "tasks").exists()
        await harness.close()

    asyncio.run(scenario())


def test_test_purpose_defaults_runtime_off(tmp_path, monkeypatch):
    monkeypatch.delenv("VIBE_TASK_RUNTIME", raising=False)
    session = SessionStore.new(tmp_path, "fake", purpose="test")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        ready = await harness.until("ready")
        assert "tasks" not in ready["capabilities"]
        harness.send("run", "r1", text="目标", task={"kind": "goal", "objective": "目标"})
        error = await harness.until("error", "r1")
        assert error["code"] == "task_runtime_disabled"
        await harness.close()

    asyncio.run(scenario())


def test_stale_task_is_recovered_and_announced(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    runtime = TaskRuntime(TaskStore(session.root), workspace=tmp_path)
    run = runtime.prepare_run("old-run", {"kind": "goal", "objective": "中断目标"})
    lock = _task_dir(session, run.task_id) / "lock"
    lock.write_text(json.dumps({
        "task_id": run.task_id, "pid": 999_999_999,
        "host": socket.gethostname(), "created_at": "2026-11-03T00:00:00+00:00",
    }), encoding="utf-8")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        notice = await harness.until("notice")
        assert "中断任务" in notice["message"]
        meta = json.loads((_task_dir(session, run.task_id) / "meta.json").read_text(encoding="utf-8"))
        assert meta["status"] == "interrupted"
        await harness.close()

    asyncio.run(scenario())


def test_tasks_command_and_ready_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="已有任务", workspace=str(tmp_path), mode="goal")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        ready = await harness.until("ready")
        assert any(item["task_id"] == task.task_id for item in ready.get("tasks", []))
        harness.send("command", "c1", command="tasks")
        result = await harness.until("command_result", "c1")
        assert task.task_id in result["text"]
        assert "worker=" in result["text"]
        ApprovalInbox(session.root).submit(task.task_id, tool="bash", tier="exec")
        harness.send("command", "c2", command="approvals")
        approvals = await harness.until("command_result", "c2")
        assert "待审批" in approvals["text"] and "bash" in approvals["text"]
        await harness.close()

    asyncio.run(scenario())


def test_chat_mode_task_sends_no_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    agent = _agent(tmp_path)

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        ready = await harness.until("ready")
        assert "modes" in ready["capabilities"]
        harness.send("run", "r-chat", text="只聊天", task={
            "kind": "run", "mode": "chat", "objective": "只聊天",
        })
        state = await harness.until("task_state", "r-chat")
        task_id = state["task"]["task_id"]
        assert state["task"]["mode"] == "chat"
        await harness.until("done", "r-chat")
        assert agent.provider.seen_tools[-1] == []
        meta = json.loads((_task_dir(session, task_id) / "meta.json").read_text(encoding="utf-8"))
        assert meta["kind"] == "run" and meta["mode"] == "chat"
        assert meta["status"] == "completed"
        assert agent.tools_enabled is True
        await harness.close()

    asyncio.run(scenario())


def test_plan_mode_restricts_tools_to_readonly_subset(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    agent = _agent(tmp_path)

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-plan", text="先规划", task={
            "kind": "run", "mode": "plan", "objective": "先规划",
        })
        await harness.until("done", "r-plan")
        names = {item["function"]["name"] for item in agent.provider.seen_tools[0]}
        assert "read_file" in names and "write_file" not in names and "bash" not in names
        assert agent.tools.names() == ["web_search", "read_file", "grep", "list_files", "git_status",
                                      "git_diff", "write_file", "edit_file", "apply_patch", "bash",
                                      "list_skills", "load_skill", "unload_skill"]
        await harness.close()

    asyncio.run(scenario())


def test_plan_command_approves_latest_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261106-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    from app.runtime.plan import Plan, load_plan, propose_plan
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-1", "task_id": task_id,
        "objective": "计划", "steps": [{"step_id": "s1", "title": "读"}],
    })
    propose_plan(task_dir, plan)

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-plan", command="plan", args={"action": "approve"})
        result = await harness.until("command_result", "c-plan")
        assert "executable=True" in result["text"] or "可执行" in result["text"]
        approved = load_plan(task_dir / "plan" / "plan.v1.json")
        assert approved.status == "approved"
        from app.runtime.plan_execution import StepLedger, save_ledger
        ledger = StepLedger.from_plan(approved)
        ledger.start_step("s1")
        save_ledger(task_dir, ledger)
        harness.send("command", "c-show", command="plan", args={"action": "show"})
        shown = await harness.until("command_result", "c-show")
        assert "in_progress" in shown["text"]
        await harness.close()

    asyncio.run(scenario())


def test_bound_plan_step_tools_record_evidence_and_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_AUTONOMY", "full")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, approve_plan, propose_plan
    from app.runtime.plan_execution import load_ledger
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-1", "task_id": plan_task,
        "objective": "执行计划",
        "steps": [{"step_id": "s1", "title": "读 README", "kind": "read"}],
    })
    propose_plan(plan_dir, plan)
    approve_plan(plan_dir, plan, approved_by="user")
    from core.contracts import Response, ToolCall
    agent = Agent(FakeProvider([
        Response(tool_calls=[ToolCall("start_plan_step", {"step_id": "s1"}, id="t1")]),
        Response(tool_calls=[ToolCall("read_file", {"path": "README.md"}, id="t2")]),
        Response(tool_calls=[ToolCall("complete_plan_step", {"step_id": "s1"}, id="t3")]),
        "计划步骤完成",
    ]), ToolRegistry(tmp_path), approval=ApprovalGate())
    agent.approval.handler = None

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-plan-step", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-1", "plan_version": 1,
        })
        done = await harness.until("done", "r-plan-step")
        assert done["result"]["status"] == "done"
        ledger = load_ledger(plan_dir, 1)
        assert ledger.steps["s1"].status == "done"
        assert any(item["kind"] == "read" for item in ledger.steps["s1"].evidence)
        assert agent.tools.names() == ["web_search", "read_file", "grep", "list_files", "git_status",
                                      "git_diff", "write_file", "edit_file", "apply_patch", "bash",
                                      "list_skills", "load_skill", "unload_skill"]
        await harness.close()

    asyncio.run(scenario())


def test_bound_plan_done_is_rejected_until_steps_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, approve_plan, propose_plan
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-1", "task_id": plan_task,
        "objective": "执行计划", "steps": [{"step_id": "s1", "title": "读", "kind": "read"}],
    })
    propose_plan(plan_dir, plan)
    approve_plan(plan_dir, plan, approved_by="user")
    agent = _agent(tmp_path, script=("完成",))

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-incomplete", text="直接完成", task={
            "kind": "run", "mode": "act", "objective": "直接完成",
            "plan_id": "pl-1", "plan_version": 1,
        })
        done = await harness.until("done", "r-incomplete")
        assert done["result"]["status"] == "stopped"
        assert done["result"]["stop_reason"] == "plan_incomplete"
        tasks = TaskStore(session.root).list(kind="run")
        assert tasks and tasks[-1].status == "paused"
        assert tasks[-1].segments[-1].stop_reason == "plan_incomplete"
        await harness.close()

    asyncio.run(scenario())


def test_bound_plan_acceptance_runs_trusted_verification(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_AUTONOMY", "full")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, approve_plan, propose_plan
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-1", "task_id": plan_task,
        "objective": "验收计划",
        "acceptance_criteria": [{"id": "ac1", "text": "命令成功", "confirmed": True}],
        "verification_plan": [{"id": "v1", "covers": ["ac1"], "command": "true",
                               "source": "plan_approval"}],
        "steps": [{"step_id": "s1", "title": "读 README", "kind": "read"}],
    })
    propose_plan(plan_dir, plan)
    approve_plan(plan_dir, plan, approved_by="user")
    from app.safety.policy import ApprovalDecision
    from core.contracts import Response, ToolCall
    agent = Agent(FakeProvider([
        Response(tool_calls=[ToolCall("start_plan_step", {"step_id": "s1"}, id="t1")]),
        Response(tool_calls=[ToolCall("read_file", {"path": "README.md"}, id="t2")]),
        Response(tool_calls=[ToolCall("complete_plan_step", {"step_id": "s1"}, id="t3")]),
        "完成",
    ]), ToolRegistry(tmp_path), approval=ApprovalGate())

    async def allow(call, preview):
        return ApprovalDecision(True, "once")
    agent.approval.handler = allow

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-accept", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-1", "plan_version": 1,
        })
        done = await harness.until("done", "r-accept")
        assert done["result"]["status"] == "done"
        await harness.close()

    asyncio.run(scenario())


def test_bound_plan_acceptance_failure_reports_command(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_AUTONOMY", "full")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, approve_plan, propose_plan
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-fail", "task_id": plan_task,
        "objective": "验收失败",
        "acceptance_criteria": [{"id": "ac1", "text": "命令成功", "confirmed": True}],
        "verification_plan": [{"id": "v1", "covers": ["ac1"], "command": "false",
                               "source": "plan_approval"}],
        "steps": [{"step_id": "s1", "title": "读 README", "kind": "read"}],
    })
    propose_plan(plan_dir, plan)
    approve_plan(plan_dir, plan, approved_by="user")
    from core.contracts import Response, ToolCall
    agent = Agent(FakeProvider([
        Response(tool_calls=[ToolCall("start_plan_step", {"step_id": "s1"}, id="t1")]),
        Response(tool_calls=[ToolCall("read_file", {"path": "README.md"}, id="t2")]),
        Response(tool_calls=[ToolCall("complete_plan_step", {"step_id": "s1"}, id="t3")]),
        "完成",
    ]), ToolRegistry(tmp_path), approval=ApprovalGate())

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-fail", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-fail", "plan_version": 1,
        })
        done = await harness.until("done", "r-fail")
        assert done["result"]["status"] == "stopped"
        assert done["result"]["stop_reason"].startswith("acceptance_")
        assert "false" in done["result"]["text"]
        await harness.close()

    asyncio.run(scenario())


def test_explore_command_runs_readonly_subagent(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")

    async def scenario():
        harness = Harness(_agent(tmp_path, script=("子 Agent 摘要",)), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-explore", command="explore", args={"prompt": "读 README"})
        result = await harness.until("command_result", "c-explore")
        assert "子 Agent" in result["text"] and "子 Agent 摘要" in result["text"]
        assert "任务：" in result["text"]
        subagents = TaskStore(session.root).list(kind="subagent")
        assert subagents and subagents[-1].status == "completed"
        await harness.close()

    asyncio.run(scenario())


def test_notifications_command_lists_recent(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    from app.runtime.notifications import notify
    notify(tmp_path, "worker_started", task_id="t-1")
    notify(tmp_path, "worker_exited", task_id="t-1", status="exited")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-notify", command="notifications")
        result = await harness.until("command_result", "c-notify")
        assert "worker_started" in result["text"] and "worker_exited" in result["text"]
        await harness.close()

    asyncio.run(scenario())


def test_plan_command_revises_latest_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261106-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    from app.runtime.plan import Plan, load_plan, propose_plan
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-rev", "task_id": task_id,
        "objective": "旧目标", "steps": [{"step_id": "s1", "title": "读"}],
    })
    propose_plan(task_dir, plan)

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-rev", command="plan",
                     args={"action": "revise", "objective": "新目标"})
        result = await harness.until("command_result", "c-rev")
        assert "新目标" in result["text"]
        revised = load_plan(task_dir / "plan" / "plan.v2.json")
        assert revised.version == 2 and revised.status == "draft"
        assert revised.objective == "新目标"
        await harness.close()

    asyncio.run(scenario())
