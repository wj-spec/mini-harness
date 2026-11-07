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
        assert "worker=" in result["text"] and "mode=goal" in result["text"]
        assert ready["tasks"][0].get("mode") == "goal"
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
        outputs = [event.get("output", "") for event in harness.events if event["type"] == "tool_result"]
        assert any("all plan steps complete" in output for output in outputs)
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
        import json as _json
        saved = _json.loads((plan_dir / "acceptance.v1.json").read_text(encoding="utf-8"))
        assert saved["status"] is True
        round_record = session.latest_round()
        assert round_record and round_record["acceptance"]["status"] is True
        assert round_record["acceptance"]["plan_id"] == "pl-1"
        from app.runtime.notifications import read_notifications
        acceptance_events = [item for item in read_notifications(tmp_path) if item["kind"] == "plan_acceptance"]
        assert acceptance_events and acceptance_events[-1]["status"] is True
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
        round_record = session.latest_round()
        assert round_record and round_record["acceptance"]["status"] is False
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
        notices = [event["message"] for event in harness.events if event["type"] == "notice"]
        assert any("子 Agent 开始" in message for message in notices)
        assert any("子 Agent ok" in message for message in notices)
        assert "任务：" in result["text"]
        subagents = TaskStore(session.root).list(kind="subagent")
        assert subagents and subagents[-1].status == "completed"
        summary = session.cost_summary()
        assert summary["subagent_runs"] == 1
        assert summary["subagent_tokens"] > 0
        assert summary["runs"] == 0
        assert "用量：" in result["text"]
        trace = [json.loads(line) for line in session.trace_path.read_text(encoding="utf-8").splitlines()]
        assert any(item.get("kind") == "subagent" for item in trace)
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


def test_plan_command_lists_versions(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261106-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    from app.runtime.plan import Plan, propose_plan, revise_plan
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-list", "task_id": task_id,
                           "objective": "旧目标", "steps": [{"step_id": "s1", "title": "读"}]})
    propose_plan(task_dir, plan)
    revise_plan(task_dir, plan, objective="新目标")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-list", command="plan", args={"action": "list"})
        result = await harness.until("command_result", "c-list")
        assert "v1" in result["text"] and "v2" in result["text"]
        assert "旧目标" in result["text"] and "新目标" in result["text"]
        await harness.close()

    asyncio.run(scenario())


def test_plan_command_shows_step_detail(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261106-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    from app.runtime.plan import Plan, propose_plan
    from app.runtime.plan_execution import StepLedger, save_ledger
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-step", "task_id": task_id,
        "objective": "步骤", "steps": [{"step_id": "s1", "title": "读代码", "kind": "read"}],
    })
    propose_plan(task_dir, plan)
    ledger = StepLedger.from_plan(plan)
    ledger.start_step("s1")
    save_ledger(task_dir, ledger)

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-step", command="plan", args={"action": "step", "step_id": "s1"})
        result = await harness.until("command_result", "c-step")
        assert "读代码" in result["text"] and "status=in_progress" in result["text"]
        await harness.close()

    asyncio.run(scenario())


def test_bound_unapproved_plan_blocks_write_at_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, propose_plan
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-draft", "task_id": plan_task,
        "objective": "未批准", "steps": [{"step_id": "s1", "title": "写", "kind": "edit"}],
    })
    propose_plan(plan_dir, plan)  # proposed, not approved
    from app.safety.policy import ApprovalDecision
    from core.contracts import Response, ToolCall
    agent = Agent(FakeProvider([
        Response(tool_calls=[ToolCall("write_file", {"path": "blocked.txt", "content": "x"}, id="w1")]),
        "完成",
    ]), ToolRegistry(tmp_path), approval=ApprovalGate())

    async def allow(call, preview):
        return ApprovalDecision(True, "once")
    agent.approval.handler = allow

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-gate", text="写文件", task={
            "kind": "run", "mode": "act", "objective": "写文件",
            "plan_id": "pl-draft", "plan_version": 1,
        })
        done = await harness.until("done", "r-gate")
        assert not (tmp_path / "blocked.txt").exists()
        outputs = [event.get("output", "") for event in harness.events if event["type"] == "tool_result"]
        assert any("plan_not_approved" in output for output in outputs)
        assert done["result"]["status"] in {"stopped", "done", "error"}
        await harness.close()

    asyncio.run(scenario())


def test_explore_emits_subagent_event(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")

    async def scenario():
        harness = Harness(_agent(tmp_path, script=("摘要",)), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-sub", command="explore", args={"prompt": "读"})
        await harness.until("command_result", "c-sub")
        events = [event for event in harness.events if event["type"] == "subagent_event"]
        assert events and events[-1]["status"] == "ok"
        assert events[-1]["task_id"].startswith("t-")
        from app.runtime.notifications import read_notifications
        notices = [item for item in read_notifications(tmp_path) if item["kind"] == "subagent_finished"]
        assert notices and notices[-1]["task_id"] == events[-1]["task_id"]
        await harness.close()

    asyncio.run(scenario())


def test_plan_command_diffs_versions(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261106-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    from app.runtime.plan import Plan, propose_plan, revise_plan
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-diff", "task_id": task_id,
                           "objective": "旧目标", "steps": [{"step_id": "s1", "title": "读"}]})
    propose_plan(task_dir, plan)
    revise_plan(task_dir, plan, objective="新目标",
                steps=[{"step_id": "s1", "title": "读"}, {"step_id": "s2", "title": "写"}])

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-diff", command="plan",
                     args={"action": "diff", "version_a": 1, "version_b": 2})
        result = await harness.until("command_result", "c-diff")
        assert "旧目标" in result["text"] and "新目标" in result["text"]
        assert "+ step s2" in result["text"]
        await harness.close()

    asyncio.run(scenario())


def test_plan_command_lists_acceptance(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261106-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    from app.runtime.plan import Plan, propose_plan
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-acc", "task_id": task_id, "objective": "验收",
        "acceptance_criteria": [{"id": "ac1", "text": "测试通过", "confirmed": True}],
        "verification_plan": [{"id": "v1", "covers": ["ac1"], "command": "true",
                               "source": "plan_approval"}],
        "steps": [{"step_id": "s1", "title": "读"}],
    })
    propose_plan(task_dir, plan)

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-acc", command="plan", args={"action": "acceptance"})
        result = await harness.until("command_result", "c-acc")
        assert "测试通过" in result["text"] and "[已确认]" in result["text"]
        assert "true" in result["text"] and "plan_approval" in result["text"]
        await harness.close()

    asyncio.run(scenario())


def test_plan_acceptance_shows_saved_result(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261106-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    from app.runtime.acceptance import save_acceptance_result
    from app.runtime.plan import Plan, propose_plan
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-acc2", "task_id": task_id,
                           "objective": "验收", "steps": [{"step_id": "s1", "title": "读"}]})
    propose_plan(task_dir, plan)
    save_acceptance_result(task_dir, 1, {"status": True, "codes": [],
                                         "results": {"v1": {"ok": True, "attempts": 2}}})

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-acc2", command="plan", args={"action": "acceptance"})
        result = await harness.until("command_result", "c-acc2")
        assert "最近执行结果" in result["text"] and "attempts=2" in result["text"]
        await harness.close()

    asyncio.run(scenario())


def test_plan_command_edits_step_title(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261106-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    from app.runtime.plan import Plan, load_plan, propose_plan
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-edit-step", "task_id": task_id,
                           "objective": "编辑", "steps": [{"step_id": "s1", "title": "旧标题"}]})
    propose_plan(task_dir, plan)

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-edit", command="plan",
                     args={"action": "edit-step", "step_id": "s1", "title": "新标题"})
        result = await harness.until("command_result", "c-edit")
        assert "新标题" in result["text"]
        revised = load_plan(task_dir / "plan" / "plan.v2.json")
        assert revised.steps[0].title == "新标题" and revised.status == "draft"
        await harness.close()

    asyncio.run(scenario())


def test_plan_approve_emits_notification(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261106-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    from app.runtime.plan import Plan, propose_plan
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-notify", "task_id": task_id,
                           "objective": "通知", "steps": [{"step_id": "s1", "title": "读"}]})
    propose_plan(task_dir, plan)

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-appr", command="plan", args={"action": "approve"})
        await harness.until("command_result", "c-appr")
        from app.runtime.notifications import read_notifications
        events = [item for item in read_notifications(tmp_path) if item["kind"] == "plan_approved"]
        assert events and events[-1]["plan_id"] == "pl-notify"
        await harness.close()

    asyncio.run(scenario())


def test_plan_mode_submit_plan_notifies(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    from core.contracts import Response, ToolCall
    agent = Agent(FakeProvider([
        Response(tool_calls=[ToolCall("submit_plan", {
            "objective": "通知计划",
            "steps": [{"step_id": "s1", "title": "读"}],
        }, id="plan-1")]),
        "完成",
    ]), ToolRegistry(tmp_path), approval=ApprovalGate())

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-propose", text="先规划", task={
            "kind": "run", "mode": "plan", "objective": "先规划",
        })
        await harness.until("done", "r-propose")
        from app.runtime.notifications import read_notifications
        events = [item for item in read_notifications(tmp_path) if item["kind"] == "plan_proposed"]
        assert events and events[-1]["task_id"]
        await harness.close()

    asyncio.run(scenario())


def test_plan_prepare_background_creates_task(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261106-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    from app.runtime.plan import Plan, approve_plan, propose_plan
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-bg", "task_id": task_id,
                           "objective": "后台计划", "steps": [{"step_id": "s1", "title": "读"}]})
    propose_plan(task_dir, plan)
    approve_plan(task_dir, plan, approved_by="user")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-bg", command="plan", args={"action": "prepare-background"})
        result = await harness.until("command_result", "c-bg")
        assert "task_id=" in result["text"]
        bg = TaskStore(session.root).list(kind="run")
        assert bg and bg[-1].plan_id == "pl-bg" and bg[-1].mode == "act"
        await harness.close()

    asyncio.run(scenario())


def test_notifications_command_filters_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    from app.runtime.notifications import notify
    notify(tmp_path, "worker_started", task_id="t-1")
    notify(tmp_path, "plan_acceptance", task_id="t-1", status=True)

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-kind", command="notifications", args={"kind": "plan_acceptance"})
        result = await harness.until("command_result", "c-kind")
        assert "plan_acceptance" in result["text"]
        assert "worker_started" not in result["text"]
        await harness.close()

    asyncio.run(scenario())


def test_bound_plan_acceptance_total_timeout_stops_run(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_AUTONOMY", "full")
    monkeypatch.setenv("VIBE_ACCEPTANCE_TOTAL_TIMEOUT_SECONDS", "0.3")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, approve_plan, propose_plan
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-timeout", "task_id": plan_task,
        "objective": "验收超时",
        "acceptance_criteria": [{"id": "ac1", "text": "sleep", "confirmed": True}],
        "verification_plan": [{"id": "v1", "covers": ["ac1"], "command": "sleep 5",
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
        harness.send("run", "r-timeout", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-timeout", "plan_version": 1,
        })
        done = await harness.until("done", "r-timeout")
        assert done["result"]["status"] == "stopped"
        assert done["result"]["stop_reason"] == "acceptance_timeout"
        await harness.close()

    asyncio.run(scenario())


def test_complete_plan_step_accepts_model_evidence_args(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, approve_plan, propose_plan
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-extra", "task_id": plan_task,
                           "objective": "额外参数", "steps": [{"step_id": "s1", "title": "读"}]})
    propose_plan(plan_dir, plan)
    approve_plan(plan_dir, plan, approved_by="user")
    from core.contracts import Response, ToolCall
    agent = Agent(FakeProvider([
        Response(tool_calls=[ToolCall("start_plan_step", {
            "step_id": "s1", "evidence_refs": ["README.md"]}, id="t1")]),
        Response(tool_calls=[ToolCall("read_file", {"path": "README.md"}, id="t2")]),
        Response(tool_calls=[ToolCall("complete_plan_step", {
            "step_id": "s1", "evidence": [{"type": "file_read"}]}, id="t3")]),
        "完成",
    ]), ToolRegistry(tmp_path), approval=ApprovalGate())

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-extra", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-extra", "plan_version": 1,
        })
        done = await harness.until("done", "r-extra")
        assert done["result"]["status"] == "done"
        await harness.close()

    asyncio.run(scenario())


def test_plan_completion_blocks_later_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_AUTONOMY", "full")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, approve_plan, propose_plan
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-gate", "task_id": plan_task,
        "objective": "完成门", "steps": [{"step_id": "s1", "title": "读 README", "kind": "read"}],
    })
    propose_plan(plan_dir, plan)
    approve_plan(plan_dir, plan, approved_by="user")
    from core.contracts import Response, ToolCall
    agent = Agent(FakeProvider([
        Response(tool_calls=[ToolCall("start_plan_step", {"step_id": "s1"}, id="t1")]),
        Response(tool_calls=[ToolCall("read_file", {"path": "README.md"}, id="t2")]),
        Response(tool_calls=[ToolCall("complete_plan_step", {"step_id": "s1"}, id="t3")]),
        Response(tool_calls=[ToolCall("read_file", {"path": "README.md"}, id="t4")]),
        "完成",
    ]), ToolRegistry(tmp_path), approval=ApprovalGate())

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-gate2", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-gate", "plan_version": 1,
        })
        done = await harness.until("done", "r-gate2")
        assert done["result"]["status"] == "done"
        outputs = [event.get("output", "") for event in harness.events if event["type"] == "tool_result"]
        assert any("tool_gate" in output and "all plan steps complete" in output for output in outputs)
        assert agent.tool_gate is None
        await harness.close()

    asyncio.run(scenario())


def test_plan_completion_force_finishes_after_repeated_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_AUTONOMY", "full")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, approve_plan, propose_plan
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-force", "task_id": plan_task,
                           "objective": "force", "steps": [{"step_id": "s1", "title": "读"}]})
    propose_plan(plan_dir, plan)
    approve_plan(plan_dir, plan, approved_by="user")
    from core.contracts import Response, ToolCall
    agent = Agent(FakeProvider([
        Response(tool_calls=[ToolCall("start_plan_step", {"step_id": "s1"}, id="t1")]),
        Response(tool_calls=[ToolCall("read_file", {"path": "README.md"}, id="t2")]),
        Response(tool_calls=[ToolCall("complete_plan_step", {"step_id": "s1"}, id="t3")]),
        Response(tool_calls=[ToolCall("read_file", {"path": "README.md"}, id="t4")]),
        Response(tool_calls=[ToolCall("read_file", {"path": "README.md"}, id="t5")]),
        "不应到达",
    ]), ToolRegistry(tmp_path), approval=ApprovalGate())

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-force", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-force", "plan_version": 1,
        })
        done = await harness.until("done", "r-force")
        assert done["result"]["status"] == "done"
        outputs = [event.get("output", "") for event in harness.events if event["type"] == "tool_result"]
        gate_errors = [output for output in outputs if "tool_gate" in output]
        assert len(gate_errors) == 2
        await harness.close()

    asyncio.run(scenario())


def test_bound_plan_step_kind_blocks_other_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_AUTONOMY", "full")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, approve_plan, propose_plan
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-kind", "task_id": plan_task,
                           "objective": "step gate", "steps": [{"step_id": "s1", "title": "读", "kind": "read"}]})
    propose_plan(plan_dir, plan)
    approve_plan(plan_dir, plan, approved_by="user")
    from core.contracts import Response, ToolCall
    agent = Agent(FakeProvider([
        Response(tool_calls=[ToolCall("start_plan_step", {"step_id": "s1"}, id="t1")]),
        Response(tool_calls=[ToolCall("bash", {"command": "echo hi"}, id="t2")]),
        Response(tool_calls=[ToolCall("read_file", {"path": "README.md"}, id="t3")]),
        Response(tool_calls=[ToolCall("complete_plan_step", {"step_id": "s1"}, id="t4")]),
        "完成",
    ]), ToolRegistry(tmp_path), approval=ApprovalGate())

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-kind", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-kind", "plan_version": 1,
        })
        done = await harness.until("done", "r-kind")
        assert done["result"]["status"] == "done"
        outputs = [event.get("output", "") for event in harness.events if event["type"] == "tool_result"]
        assert any("plan_step_tool_blocked" in output and "bash" in output for output in outputs)
        await harness.close()

    asyncio.run(scenario())


def test_bound_plan_edit_step_enforces_file_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_AUTONOMY", "full")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, approve_plan, propose_plan
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-scope", "task_id": plan_task,
                           "objective": "edit scope", "steps": [
                               {"step_id": "s1", "title": "写 a", "kind": "edit", "files": ["a.txt"]},
                           ]})
    propose_plan(plan_dir, plan)
    approve_plan(plan_dir, plan, approved_by="user")
    from core.contracts import Response, ToolCall
    agent = Agent(FakeProvider([
        Response(tool_calls=[ToolCall("start_plan_step", {"step_id": "s1"}, id="t1")]),
        Response(tool_calls=[ToolCall("write_file", {"path": "b.txt", "content": "b"}, id="t2")]),
        Response(tool_calls=[ToolCall("apply_patch", {"patch": "*** Begin Patch\n*** End Patch"}, id="t2b")]),
        Response(tool_calls=[ToolCall("write_file", {"path": "a.txt", "content": "a2"}, id="t3")]),
        Response(tool_calls=[ToolCall("complete_plan_step", {"step_id": "s1"}, id="t4")]),
        "完成",
    ]), ToolRegistry(tmp_path), approval=ApprovalGate())

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-scope", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-scope", "plan_version": 1,
        })
        done = await harness.until("done", "r-scope")
        assert done["result"]["status"] == "done"
        assert not (tmp_path / "b.txt").exists()
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "a2"
        outputs = [event.get("output", "") for event in harness.events if event["type"] == "tool_result"]
        assert any("plan_step_file_scope" in output for output in outputs)
        assert any("apply_patch" in output and "plan_step_file_scope" in output for output in outputs)
        await harness.close()

    asyncio.run(scenario())


def test_resume_rebinds_paused_plan_task(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, approve_plan, propose_plan
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-resume", "task_id": plan_task,
                           "objective": "resume", "steps": [{"step_id": "s1", "title": "读"}]})
    propose_plan(plan_dir, plan)
    approve_plan(plan_dir, plan, approved_by="user")
    agent = _agent(tmp_path, script=("完成", "继续完成"))
    from app.safety.policy import ApprovalDecision

    async def allow(call, preview):
        return ApprovalDecision(True, "once")
    agent.approval.handler = allow

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-resume", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-resume", "plan_version": 1,
        })
        first = await harness.until("done", "r-resume")
        assert first["result"]["status"] == "stopped"
        tasks = TaskStore(session.root).list(kind="run")
        assert tasks[-1].status == "paused"
        harness.send("command", "c-resume", command="resume")
        state = await harness.until("task_state")
        assert state["id"] == "c-resume" and state["task"]["task_id"] == tasks[-1].task_id
        done = await harness.until("done", "c-resume")
        assert done["result"]["status"] in {"stopped", "done"}
        resumed = TaskStore(session.root).get(tasks[-1].task_id)
        assert len(resumed.segments) >= 2
        await harness.close()

    asyncio.run(scenario())


def _repair_plan(plan_dir, *, command="grep -q ok out.txt"):
    from app.runtime.plan import Plan, approve_plan, propose_plan

    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-repair", "task_id": "t-20261106-000000-abcdef",
        "objective": "修复验收",
        "acceptance_criteria": [{"id": "ac1", "text": "out.txt 含 ok", "confirmed": True}],
        "verification_plan": [{"id": "v1", "covers": ["ac1"], "command": command,
                               "source": "plan_approval"}],
        "steps": [{"step_id": "s1", "title": "写 out.txt", "kind": "edit", "files": ["out.txt"]}],
    })
    propose_plan(plan_dir, plan)
    approve_plan(plan_dir, plan, approved_by="user")


def _repair_agent(tmp_path, script):
    from app.safety.policy import ApprovalDecision

    agent = Agent(FakeProvider(script), ToolRegistry(tmp_path), approval=ApprovalGate())

    async def allow(call, preview):
        return ApprovalDecision(True, "once")
    agent.approval.handler = allow
    return agent


def test_bound_plan_acceptance_repair_recovers(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_AUTONOMY", "full")
    monkeypatch.setenv("VIBE_ACCEPTANCE_REPAIR_ATTEMPTS", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    plan_dir = session.root / "tasks" / "t-20261106-000000-abcdef"
    _repair_plan(plan_dir)
    from core.contracts import Response, ToolCall
    agent = _repair_agent(tmp_path, [
        Response(tool_calls=[ToolCall("start_plan_step", {"step_id": "s1"}, id="t1")]),
        Response(tool_calls=[ToolCall("write_file", {"path": "out.txt", "content": "bad"}, id="t2")]),
        Response(tool_calls=[ToolCall("complete_plan_step", {"step_id": "s1"}, id="t3")]),
        "写好了",
        Response(tool_calls=[ToolCall("read_file", {"path": "out.txt"}, id="t4")]),
        Response(tool_calls=[ToolCall("write_file", {"path": "out.txt", "content": "ok"}, id="t5")]),
        "已修复",
    ])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-repair", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-repair", "plan_version": 1,
        })
        done = await harness.until("done", "r-repair")
        assert done["result"]["status"] == "done"
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "ok"
        import json as _json
        saved = _json.loads((plan_dir / "acceptance.v1.json").read_text(encoding="utf-8"))
        assert saved["status"] is True
        assert agent.provider.i == 7
        notices = [event["message"] for event in harness.events if event["type"] == "notice"]
        assert any("自动修复第 1/1 次" in message for message in notices)
        from app.runtime.notifications import read_notifications
        repairs = [item for item in read_notifications(tmp_path) if item["kind"] == "plan_repair"]
        assert repairs and repairs[-1]["attempt"] == 1
        await harness.close()

    asyncio.run(scenario())


def test_bound_plan_acceptance_repair_exhausted_stops(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_AUTONOMY", "full")
    monkeypatch.setenv("VIBE_ACCEPTANCE_REPAIR_ATTEMPTS", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    plan_dir = session.root / "tasks" / "t-20261106-000000-abcdef"
    _repair_plan(plan_dir)
    from core.contracts import Response, ToolCall
    agent = _repair_agent(tmp_path, [
        Response(tool_calls=[ToolCall("start_plan_step", {"step_id": "s1"}, id="t1")]),
        Response(tool_calls=[ToolCall("write_file", {"path": "out.txt", "content": "bad"}, id="t2")]),
        Response(tool_calls=[ToolCall("complete_plan_step", {"step_id": "s1"}, id="t3")]),
        "写好了",
        Response(tool_calls=[ToolCall("write_file", {"path": "out.txt", "content": "still bad"}, id="t4")]),
        "又没修好",
    ])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-bad", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-repair", "plan_version": 1,
        })
        done = await harness.until("done", "r-bad")
        assert done["result"]["status"] == "stopped"
        assert done["result"]["stop_reason"].startswith("acceptance_")
        assert agent.provider.i == 6
        notices = [event["message"] for event in harness.events if event["type"] == "notice"]
        assert sum("自动修复" in message for message in notices) == 1
        await harness.close()

    asyncio.run(scenario())


def test_bound_plan_repair_does_not_run_when_plan_incomplete(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_ACCEPTANCE_REPAIR_ATTEMPTS", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    plan_dir = session.root / "tasks" / "t-20261106-000000-abcdef"
    _repair_plan(plan_dir)
    agent = _repair_agent(tmp_path, ["完成"])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-incomplete2", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-repair", "plan_version": 1,
        })
        done = await harness.until("done", "r-incomplete2")
        assert done["result"]["status"] == "stopped"
        assert done["result"]["stop_reason"] == "plan_incomplete"
        assert agent.provider.i == 1
        await harness.close()

    asyncio.run(scenario())


def test_bound_plan_repair_enforces_edit_file_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_AUTONOMY", "full")
    monkeypatch.setenv("VIBE_ACCEPTANCE_REPAIR_ATTEMPTS", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    plan_dir = session.root / "tasks" / "t-20261106-000000-abcdef"
    _repair_plan(plan_dir)
    from core.contracts import Response, ToolCall
    agent = _repair_agent(tmp_path, [
        Response(tool_calls=[ToolCall("start_plan_step", {"step_id": "s1"}, id="t1")]),
        Response(tool_calls=[ToolCall("write_file", {"path": "out.txt", "content": "bad"}, id="t2")]),
        Response(tool_calls=[ToolCall("complete_plan_step", {"step_id": "s1"}, id="t3")]),
        "写好了",
        Response(tool_calls=[ToolCall("write_file", {"path": "other.txt", "content": "x"}, id="t4")]),
        Response(tool_calls=[ToolCall("write_file", {"path": "out.txt", "content": "ok"}, id="t5")]),
        "已修复",
    ])

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-scope", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-repair", "plan_version": 1,
        })
        done = await harness.until("done", "r-scope")
        assert done["result"]["status"] == "done"
        assert not (tmp_path / "other.txt").exists()
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "ok"
        blocked = [event.get("output", "") for event in harness.events
                   if event["type"] == "tool_result" and not event.get("ok")]
        assert any("plan_repair_file_scope" in output for output in blocked)
        await harness.close()

    asyncio.run(scenario())


def test_tasks_command_shows_pending_approvals(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task = TaskStore(session.root).create(
        kind="goal", objective="待审批任务", workspace=str(tmp_path), mode="goal",
        priority=3,
    )
    ApprovalInbox(session.root).submit(
        task.task_id, tool="bash", tier="exec",
        decisions=["once", "deny"], preview="ls",
    )

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        ready = await harness.until("ready")
        hint = next(item for item in ready["tasks"] if item["task_id"] == task.task_id)
        assert hint["pending_approvals"] == 1
        assert hint["priority"] == 3
        harness.send("command", "c-tasks2", command="tasks")
        result = await harness.until("command_result", "c-tasks2")
        assert "待审批 1" in result["text"]
        assert "priority=3" in result["text"]
        await harness.close()

    asyncio.run(scenario())


def test_bound_plan_acceptance_marks_task_verified(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_AUTONOMY", "full")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, approve_plan, propose_plan
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-verified", "task_id": plan_task,
        "objective": "验收后视为已验证",
        "acceptance_criteria": [{"id": "ac1", "text": "文件存在", "confirmed": True}],
        "verification_plan": [{"id": "v1", "covers": ["ac1"], "command": "test -f a.txt",
                               "source": "plan_approval"}],
        "steps": [{"step_id": "s1", "title": "写 a", "kind": "edit", "files": ["a.txt"]}],
    })
    propose_plan(plan_dir, plan)
    approve_plan(plan_dir, plan, approved_by="user")
    from core.contracts import Response, ToolCall
    agent = Agent(FakeProvider([
        Response(tool_calls=[ToolCall("start_plan_step", {"step_id": "s1"}, id="t1")]),
        Response(tool_calls=[ToolCall("write_file", {"path": "a.txt", "content": "a2"}, id="t2")]),
        Response(tool_calls=[ToolCall("complete_plan_step", {"step_id": "s1"}, id="t3")]),
        "完成",
    ]), ToolRegistry(tmp_path), approval=ApprovalGate())

    async def allow(call, preview):
        from app.safety.policy import ApprovalDecision
        return ApprovalDecision(True, "once")
    agent.approval.handler = allow

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-verified", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-verified", "plan_version": 1,
        })
        done = await harness.until("done", "r-verified")
        assert done["result"]["status"] == "done"
        assert done["result"]["verified"] is True
        states = [event for event in harness.events if event["type"] == "task_state"]
        assert states and states[-1]["task"]["status"] == "completed"
        await harness.close()

    asyncio.run(scenario())


def test_bound_plan_injects_steps_into_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, approve_plan, propose_plan
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-prompt", "task_id": plan_task,
        "objective": "注入步骤提示", "steps": [
            {"step_id": "s1", "title": "读 README", "kind": "read"},
            {"step_id": "s2", "title": "总结", "kind": "read", "depends_on": ["s1"]},
        ],
    })
    propose_plan(plan_dir, plan)
    approve_plan(plan_dir, plan, approved_by="user")
    provider = FakeProvider(["完成"])
    agent = Agent(provider, ToolRegistry(tmp_path), approval=ApprovalGate())

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-prompt", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-prompt", "plan_version": 1,
        })
        await harness.until("done", "r-prompt")
        user_messages = [m for m in provider.seen[0] if m.role == "user"]
        prompt = user_messages[-1].content
        assert "[已批准计划]" in prompt
        assert "- s1: 读 README (kind=read)" in prompt
        assert "- s2: 总结 (kind=read)" in prompt
        assert "start_plan_step" in prompt and "complete_plan_step" in prompt
        await harness.close()

    asyncio.run(scenario())


def test_complete_plan_step_accepts_manual_evidence_refs_without_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    plan_task = "t-20261106-000000-abcdef"
    plan_dir = session.root / "tasks" / plan_task
    from app.runtime.plan import Plan, approve_plan, propose_plan
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-manual-evidence", "task_id": plan_task,
        "objective": "纯分析步骤", "steps": [
            {"step_id": "s1", "title": "读并总结", "kind": "read"},
        ],
    })
    propose_plan(plan_dir, plan)
    approve_plan(plan_dir, plan, approved_by="user")
    from app.runtime.plan_execution import load_ledger
    from core.contracts import Response, ToolCall
    agent = Agent(FakeProvider([
        Response(tool_calls=[ToolCall("start_plan_step", {"step_id": "s1"}, id="t1")]),
        Response(tool_calls=[ToolCall("complete_plan_step", {
            "step_id": "s1",
            "evidence_refs": ["README.md: hello", "summary: README 说明用途"],
        }, id="t2")]),
        "完成",
    ]), ToolRegistry(tmp_path), approval=ApprovalGate())

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-manual", text="执行", task={
            "kind": "run", "mode": "act", "objective": "执行",
            "plan_id": "pl-manual-evidence", "plan_version": 1,
        })
        done = await harness.until("done", "r-manual")
        assert done["result"]["status"] == "done"
        ledger = load_ledger(plan_dir, 1)
        assert ledger.steps["s1"].status == "done"
        manual = [item for item in ledger.steps["s1"].evidence
                  if isinstance(item, dict) and item.get("kind") == "manual"]
        assert len(manual) == 2 and "README.md" in manual[0]["ref"]
        await harness.close()

    asyncio.run(scenario())


def _goal_with_acceptance(tmp_path, command):
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="目标验收", workspace=str(tmp_path), mode="goal")
    from app.runtime.goals import GoalStore
    goals = GoalStore(session.root)
    record = goals.ensure_for_task(task)
    record.acceptance_criteria = [{"id": "ac1", "text": "README 存在", "confirmed": True}]
    record.verification_plan = [{"command": command}]
    goals.save(record)
    return session, task


def test_goal_acceptance_verifies_and_completes(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_AUTONOMY", "full")
    session, task = _goal_with_acceptance(tmp_path, "test -f README.md")
    from app.runtime.notifications import read_notifications
    from app.safety.policy import ApprovalDecision
    agent = _agent(tmp_path, script=("完成",))

    async def allow(call, preview):
        return ApprovalDecision(True, "once")
    agent.approval.handler = allow

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-goal-acc", text="完成目标", task={
            "kind": "goal", "task_id": task.task_id, "objective": "目标验收",
        })
        done = await harness.until("done", "r-goal-acc")
        assert done["result"]["status"] == "done"
        assert done["result"]["verified"] is True
        states = [event for event in harness.events if event["type"] == "task_state"]
        assert states and states[-1]["task"]["status"] == "completed"
        acceptance = session.root / "tasks" / task.task_id / "acceptance.v1.json"
        assert acceptance.is_file()
        events = [item for item in read_notifications(tmp_path) if item["kind"] == "goal_acceptance"]
        assert events and events[-1]["status"] is True
        round_record = session.latest_round()
        assert round_record and round_record["acceptance"]["status"] is True
        assert round_record["acceptance"]["goal_id"]
        await harness.close()

    asyncio.run(scenario())


def test_goal_acceptance_failure_pauses_task(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_AUTONOMY", "full")
    session, task = _goal_with_acceptance(tmp_path, "false")
    from app.safety.policy import ApprovalDecision
    agent = _agent(tmp_path, script=("完成",))

    async def allow(call, preview):
        return ApprovalDecision(True, "once")
    agent.approval.handler = allow

    async def scenario():
        harness = Harness(agent, Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-goal-fail", text="完成目标", task={
            "kind": "goal", "task_id": task.task_id, "objective": "目标验收",
        })
        done = await harness.until("done", "r-goal-fail")
        assert done["result"]["status"] == "stopped"
        assert done["result"]["stop_reason"].startswith("acceptance_")
        assert "目标验收未通过" in done["result"]["text"] and "false" in done["result"]["text"]
        states = [event for event in harness.events if event["type"] == "task_state"]
        assert states and states[-1]["task"]["status"] == "paused"
        await harness.close()

    asyncio.run(scenario())


def test_trace_command_renders_session_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    session.append_trace({"type": "run_start", "ts": 1.0})
    session.append_trace({"type": "llm_end", "ts": 2.0, "prompt_tokens": 10,
                          "completion_tokens": 5, "cost_usd": 0.001})
    session.append_trace({"type": "tool_end", "ts": 3.0, "tool": "bash", "ok": False})
    session.append_trace({"type": "run_end", "ts": 4.0, "status": "stopped",
                          "stop_reason": "max_run_seconds"})

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-trace", command="trace")
        result = await harness.until("command_result", "c-trace")
        assert "trace 摘要" in result["text"]
        assert "max_run_seconds=1" in result["text"]
        assert "tokens 15" in result["text"]
        await harness.close()

    asyncio.run(scenario())


def test_tasks_command_tolerates_corrupt_meta(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    store = TaskStore(session.root)
    task = store.create(kind="run", objective="损坏容错任务", workspace=str(tmp_path), mode="act")
    corrupt = session.root / "tasks" / "t-20261107-000000-badbad"
    corrupt.mkdir(parents=True)
    (corrupt / "meta.json").write_text("{bad json", encoding="utf-8")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-corrupt-tasks", command="tasks")
        result = await harness.until("command_result", "c-corrupt-tasks")
        assert task.task_id in result["text"]
        await harness.close()

    asyncio.run(scenario())


def test_trace_command_accepts_hours_argument(tmp_path, monkeypatch):
    import time

    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    now = time.time()
    session.append_trace({"type": "llm_end", "ts": now - 7200, "prompt_tokens": 100,
                          "completion_tokens": 50})
    session.append_trace({"type": "llm_end", "ts": now - 60, "prompt_tokens": 10,
                          "completion_tokens": 5})

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-trace-hours", command="trace", args={"hours": 1})
        result = await harness.until("command_result", "c-trace-hours")
        assert "tokens 15" in result["text"]
        assert "tokens 150" not in result["text"]
        harness.send("command", "c-trace-bad", command="trace", args={"hours": 0})
        bad = await harness.until("error", "c-trace-bad")
        assert "hours" in bad["message"]
        harness.send("command", "c-after-bad", command="tasks")
        after = await harness.until("command_result", "c-after-bad")
        assert after["command"] == "tasks"
        await harness.close()

    asyncio.run(scenario())


def test_command_args_validation_rejects_malformed_frames(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-bad-notify", command="notifications", args={"kind": ""})
        bad_notify = await harness.until("error", "c-bad-notify")
        assert "notifications" in bad_notify["message"]
        harness.send("command", "c-bad-explore", command="explore", args={})
        bad_explore = await harness.until("error", "c-bad-explore")
        assert "explore" in bad_explore["message"]
        harness.send("command", "c-after-invalid", command="tasks")
        after = await harness.until("command_result", "c-after-invalid")
        assert after["command"] == "tasks"
        await harness.close()

    asyncio.run(scenario())


def test_stdio_rejects_malformed_frames_and_stays_responsive(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    malformed = [
        "not json",
        json.dumps([1, 2, 3]),
        json.dumps({"v": 2, "type": "command", "id": "bad-v", "command": "tasks"}),
        json.dumps({"v": 1, "type": "command", "id": "f-extra-args", "command": "tasks", "args": {}}),
        json.dumps({"v": 1, "type": "command", "id": "f-unknown", "command": "totally-unknown"}),
        json.dumps({"v": 1, "type": "run", "id": "f-empty-run", "text": "   "}),
        json.dumps({"v": 1, "type": "command", "id": "f-null-args", "command": "trace", "args": None}),
    ]

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        for frame in malformed:
            harness.incoming.put_nowait(frame)
        harness.send("command", "c-after-malformed", command="tasks")
        result = await harness.until("command_result", "c-after-malformed")
        assert result["command"] == "tasks"
        errors = [event for event in harness.events if event["type"] == "error"]
        assert len(errors) >= len(malformed)
        assert {event.get("code") for event in errors} <= {"invalid_request"}
        await harness.close()

    asyncio.run(scenario())


def test_stdio_rejects_duplicate_request_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "dup-id", command="tasks")
        await harness.until("command_result", "dup-id")
        harness.send("command", "dup-id", command="tasks")
        duplicate = await harness.until("error", "dup-id")
        assert duplicate["code"] == "duplicate_id"
        harness.send("command", "after-dup", command="tasks")
        after = await harness.until("command_result", "after-dup")
        assert after["command"] == "tasks"
        await harness.close()

    asyncio.run(scenario())


def test_stdio_rejects_second_command_while_busy(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    monkeypatch.setenv("VIBE_FAKE_DELAY_SECONDS", "0.3")
    session = SessionStore.new(tmp_path, "fake", purpose="user")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("run", "r-busy", text="你好")
        harness.send("command", "c-while-busy", command="tasks")
        busy = await harness.until("error", "c-while-busy")
        assert busy["code"] == "busy"
        done = await harness.until("done", "r-busy")
        assert done["result"]["status"] == "done"
        harness.send("command", "c-after-busy", command="tasks")
        after = await harness.until("command_result", "c-after-busy")
        assert after["command"] == "tasks"
        await harness.close()

    asyncio.run(scenario())


def test_priority_command_updates_created_task(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    store = TaskStore(session.root)
    task = store.create(kind="run", objective="队列任务", workspace=str(tmp_path), mode="act")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-priority", command="priority",
                     args={"task_id": task.task_id, "priority": 9})
        result = await harness.until("command_result", "c-priority")
        assert "优先级为 9" in result["text"]
        harness.send("command", "c-priority-bad", command="priority",
                     args={"task_id": "", "priority": 9})
        bad = await harness.until("error", "c-priority-bad")
        assert "priority" in bad["message"]
        await harness.close()

    asyncio.run(scenario())
    assert store.get(task.task_id).priority == 9


def test_queue_command_lists_created_plan_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    store = TaskStore(session.root)
    task = store.create(kind="run", objective="排队任务", workspace=str(tmp_path),
                        mode="act", plan_id="pl-q", plan_version=1, priority=3)

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-queue", command="queue")
        result = await harness.until("command_result", "c-queue")
        assert "计划队列（1）" in result["text"]
        assert task.task_id in result["text"] and "priority 3" in result["text"]
        assert "pl-qv1" in result["text"]
        await harness.close()

    asyncio.run(scenario())


def test_queue_command_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-queue-empty", command="queue")
        result = await harness.until("command_result", "c-queue-empty")
        assert "当前没有待运行的 created 计划任务" in result["text"]
        await harness.close()

    asyncio.run(scenario())


def test_plan_edit_step_can_change_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261106-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    from app.runtime.plan import Plan, load_plan, propose_plan
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-edit-kind", "task_id": task_id,
                           "objective": "编辑类型", "steps": [{"step_id": "s1", "title": "读"}]})
    propose_plan(task_dir, plan)

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-edit-kind", command="plan",
                     args={"action": "edit-step", "step_id": "s1", "kind": "verify"})
        result = await harness.until("command_result", "c-edit-kind")
        assert "[verify/read]" in result["text"]
        harness.send("command", "c-edit-bad", command="plan",
                     args={"action": "edit-step", "step_id": "s1", "kind": "bogus"})
        bad = await harness.until("error", "c-edit-bad")
        assert "kind" in bad["message"]
        await harness.close()

    asyncio.run(scenario())
    revised = load_plan(task_dir / "plan" / "plan.v2.json")
    assert revised.steps[0].kind == "verify"
    assert revised.steps[0].title == "读"


def test_plan_edit_step_can_change_files(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261106-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    from app.runtime.plan import Plan, load_plan, propose_plan
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-edit-files", "task_id": task_id,
                           "objective": "编辑文件范围",
                           "steps": [{"step_id": "s1", "title": "写", "kind": "edit"}]})
    propose_plan(task_dir, plan)

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-edit-files", command="plan",
                     args={"action": "edit-step", "step_id": "s1", "files": ["a.py", "b.py"]})
        await harness.until("command_result", "c-edit-files")
        harness.send("command", "c-edit-files-bad", command="plan",
                     args={"action": "edit-step", "step_id": "s1", "files": [""]})
        bad = await harness.until("error", "c-edit-files-bad")
        assert "files" in bad["message"]
        await harness.close()

    asyncio.run(scenario())
    revised = load_plan(task_dir / "plan" / "plan.v2.json")
    assert revised.steps[0].files == ["a.py", "b.py"]


def test_plan_edit_step_can_change_dependencies(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261106-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    from app.runtime.plan import Plan, load_plan, propose_plan
    plan = Plan.from_dict({"schema_version": 1, "plan_id": "pl-deps", "task_id": task_id,
                           "objective": "依赖编辑", "steps": [
                               {"step_id": "s1", "title": "读"},
                               {"step_id": "s2", "title": "写", "kind": "edit"},
                           ]})
    propose_plan(task_dir, plan)

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-dep", command="plan",
                     args={"action": "edit-step", "step_id": "s2", "depends_on": ["s1"]})
        ok = await harness.until("command_result", "c-dep")
        assert "draft" in ok["text"]
        harness.send("command", "c-dep-cycle", command="plan",
                     args={"action": "edit-step", "step_id": "s1", "depends_on": ["s2"]})
        cycle = await harness.until("command_result", "c-dep-cycle")
        assert "循环" in cycle["text"]
        harness.send("command", "c-dep-missing", command="plan",
                     args={"action": "edit-step", "step_id": "s2", "depends_on": ["s9"]})
        missing = await harness.until("command_result", "c-dep-missing")
        assert "不存在" in missing["text"]
        await harness.close()

    asyncio.run(scenario())
    plan_dir = task_dir / "plan"
    revised = load_plan(plan_dir / "plan.v2.json")
    assert revised.steps[1].depends_on == ["s1"]
    assert not (plan_dir / "plan.v3.json").exists()


def test_plan_card_shows_approval_details(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_TASK_RUNTIME", "1")
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    task_id = "t-20261106-000000-abcdef"
    task_dir = session.root / "tasks" / task_id
    from app.runtime.plan import Plan, propose_plan
    plan = Plan.from_dict({
        "schema_version": 1, "plan_id": "pl-card", "task_id": task_id,
        "objective": "审批卡目标", "constraints": ["只读"],
        "acceptance_criteria": [{"id": "ac1", "text": "命令通过", "confirmed": True}],
        "verification_plan": [{"id": "v1", "covers": ["ac1"], "command": "true",
                               "source": "plan_approval"}],
        "steps": [{"step_id": "s1", "title": "读"}],
    })
    propose_plan(task_dir, plan)

    async def scenario():
        harness = Harness(_agent(tmp_path), Config(workspace=tmp_path), session)
        await harness.until("ready")
        harness.send("command", "c-card", command="plan", args={"action": "card"})
        result = await harness.until("command_result", "c-card")
        text = result["text"]
        assert "计划审批卡" in text and "审批卡目标" in text
        assert "约束：只读" in text and "验收标准" in text
        assert "验证命令" in text and "cmd=true" in text
        assert "步骤：" in text and "1. s1 读 [read/read]" in text
        assert "executable=False" in text
        assert "不可执行原因：" in text and "status=proposed" in text
        await harness.close()

    asyncio.run(scenario())
