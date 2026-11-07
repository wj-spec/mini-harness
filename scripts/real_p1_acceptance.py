#!/usr/bin/env python3
"""真实模型 Plan P1 验收脚本：生成计划 → 批准 → 执行 → 审批 → 验收。

需要已配置 VIBE_API_KEY（或 OPENAI/DEEPSEEK key）。默认自动回送审批帧模拟 TUI 用户。
输出 JSON 摘要并写 <workspace>/.vibe/real-p1-acceptance/<timestamp>.json；失败 exit 1。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.__main__ import build_provider  # noqa: E402
from app.config import Config  # noqa: E402
from app.runtime.agent import Agent  # noqa: E402
from app.runtime.plan import approve_plan, load_plan, save_plan  # noqa: E402
from app.safety.policy import ApprovalGate  # noqa: E402
from app.session.store import SessionStore  # noqa: E402
from app.stdio import serve  # noqa: E402
from app.tools.registry import ToolRegistry  # noqa: E402


class Harness:
    def __init__(self, agent, config, session, timeout: float = 240.0):
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.outgoing: asyncio.Queue = asyncio.Queue()
        self.events: list[dict] = []
        self.timeout = timeout

        async def requests():
            while (frame := await self.incoming.get()) is not None:
                yield frame

        def emit(event):
            self.events.append(event)
            self.outgoing.put_nowait(event)

        self.task = asyncio.create_task(serve(agent, config, session=session, requests=requests(), emit=emit))

    def send(self, kind, request_id, **data):
        self.incoming.put_nowait(json.dumps({"v": 1, "type": kind, "id": request_id, **data}))

    async def until(self, kind, request_id=None):
        async with asyncio.timeout(self.timeout):
            while True:
                event = await self.outgoing.get()
                if event["type"] == kind and (request_id is None or event["id"] == request_id):
                    return event

    async def close(self):
        self.incoming.put_nowait(None)
        await asyncio.wait_for(self.task, 30)


def _agent(config: Config, workspace: Path) -> Agent:
    return Agent(
        build_provider(config, False), ToolRegistry(workspace), approval=ApprovalGate(),
        max_steps=20, max_run_seconds=300.0, llm_timeout=60.0, llm_retries=0,
        max_run_tokens=120_000, max_continuations=1,
    )


def _init_repo(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "README.md").write_text("# real-p1\n真实模型 Plan P1 验收工作区。\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "smoke@example.com"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Smoke"], cwd=workspace, check=True)
    (workspace / ".gitignore").write_text(".vibe/\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=workspace, check=True)


async def _generate_plan(config: Config, workspace: Path, session: SessionStore,
                        verify_command: str = "true", *, prompt: str | None = None,
                        adjust=None) -> tuple[str | None, dict | None]:
    base_prompt = prompt or (
        "必须调用 submit_plan 一次，提交恰好两个步骤的只读计划："
        '{"step_id":"s1","title":"读取 README.md","kind":"read"} 和 '
        '{"step_id":"s2","title":"总结项目用途","kind":"read"}，s2 depends_on ["s1"]。'
        "提交后立即结束。"
    )
    plan_task_id: str | None = None
    for attempt in range(2):
        gen = Harness(_agent(config, workspace), config, session)
        try:
            await gen.until("ready")
            request_id = f"gen-{attempt + 1}"
            text = base_prompt
            if attempt:
                text = ("上一次运行没有产生计划文件。这次必须调用 submit_plan 工具一次，"
                        + base_prompt)
            gen.send("run", request_id, text=text,
                     task={"kind": "run", "mode": "plan", "objective": "真实 P1 计划"})
            state = await gen.until("task_state", request_id)
            candidate_id = state["task"]["task_id"]
            await gen.until("done", request_id)
        finally:
            await gen.close()
        candidate = session.root / "tasks" / candidate_id / "plan" / "plan.v1.json"
        if candidate.is_file():
            plan_task_id = candidate_id
            break
    if plan_task_id is None:
        return None, None
    plan_file = session.root / "tasks" / plan_task_id / "plan" / "plan.v1.json"
    loaded = load_plan(plan_file)
    loaded.acceptance_criteria = [{"id": "ac1", "text": "验收命令成功", "confirmed": True}]
    loaded.verification_plan = [{"id": "v1", "covers": ["ac1"], "command": verify_command,
                                 "source": "plan_approval"}]
    if adjust is not None:
        adjust(loaded)
    save_plan(session.root / "tasks" / plan_task_id, loaded)
    approve_plan(session.root / "tasks" / plan_task_id, load_plan(plan_file), approved_by="smoke")
    return plan_task_id, json.loads(plan_file.read_text(encoding="utf-8"))


async def _run(config: Config, workspace: Path, verify_command: str = "true",
               background: bool = False) -> dict:
    session = SessionStore.new(workspace, "openai", purpose="user")
    # 1) generate plan
    plan_task_id, plan = await _generate_plan(config, workspace, session, verify_command)
    if plan is None:
        return {"ok": False, "stage": "generate", "error": "no plan.v1.json"}
    # 2) execute；--background 走 prepare-background + detached worker
    approvals = 0
    approval_tools: list[str] = []
    if background:
        prep = Harness(_agent(config, workspace), config, session)
        try:
            await prep.until("ready")
            prep.send("command", "bg-1", command="plan", args={"action": "prepare-background"})
            prepared = await prep.until("command_result", "bg-1")
        finally:
            await prep.close()
        match = re.search(r"task_id=(t-[0-9a-zA-Z-]+)", str(prepared.get("text") or ""))
        if not match:
            return {"ok": False, "stage": "prepare-background",
                    "error": str(prepared.get("text") or "")[:500]}
        bg_task_id = match.group(1)
        (workspace / ".vibe").mkdir(exist_ok=True)
        (workspace / ".vibe" / "unattended.json").write_text(
            '{"schema_version":1,"allow_exec":true}', encoding="utf-8")
        env = dict(os.environ, VIBE_TASK_RUNTIME="1", VIBE_AUTONOMY="full",
                   VIBE_LLM_TIMEOUT=str(config.llm_timeout),
                   VIBE_MAX_STEPS="20", VIBE_MAX_RUN_TOKENS="150000")
        proc = subprocess.run(
            [sys.executable, "-m", "app", "--worker", "--workspace", str(workspace),
             "--session", session.id, "--task-id", bg_task_id],
            cwd=str(Path(__file__).resolve().parents[1]), env=env,
            capture_output=True, text=True, timeout=300,
        )
        from app.runtime.tasks import TaskStore
        final_task = TaskStore(session.root).get(bg_task_id)
        ledger_file = session.root / "tasks" / plan_task_id / "plan" / "execution.v1.json"
        ledger = json.loads(ledger_file.read_text(encoding="utf-8")) if ledger_file.is_file() else {}
        acc_file = session.root / "tasks" / plan_task_id / "acceptance.v1.json"
        acceptance = json.loads(acc_file.read_text(encoding="utf-8")).get("status") if acc_file.is_file() else None
        steps = {item["step_id"]: item["status"] for item in ledger.get("steps", [])}
        plan_ok = final_task.status == "completed" and steps == {"s1": "done", "s2": "done"} and acceptance is True
        sub = await _run_subagent(config, workspace, session)
        return {
            "ok": plan_ok and sub.get("status") == "ok", "stage": "background",
            "background": True, "worker_exit": proc.returncode,
            "verify_command": verify_command, "task_id": bg_task_id,
            "run_status": final_task.status, "steps": steps, "acceptance": acceptance,
            "approvals": 0, "approval_tools": [], "stop_reason": None, "subagent": sub,
        }

    done = None
    for attempt in range(2):
        exe = Harness(_agent(config, workspace), config, session)
        try:
            await exe.until("ready")
            request_id = f"exec-{attempt + 1}"
            exe.send("run", request_id, text="按计划执行 s1、s2，全部完成后结束。", task={
                "kind": "run", "mode": "act", "objective": "执行真实 P1 计划",
                "plan_id": plan["plan_id"], "plan_version": plan["version"],
            })
            while True:
                event = await asyncio.wait_for(exe.outgoing.get(), 300)
                if event["type"] == "approval_request":
                    approvals += 1
                    call = event.get("call") if isinstance(event.get("call"), dict) else {}
                    approval_tools.append(str(call.get("name") or event.get("tool") or "tool"))
                    exe.send("approval", f"approval-{approvals}",
                             approval_id=event["approval_id"], decision="once")
                if event["type"] == "done" and event["id"] == request_id:
                    done = event
                    break
        finally:
            await exe.close()
        if done is not None:
            status = done["result"].get("status")
            stop_reason = str(done["result"].get("stop_reason") or "")
            if status == "done" or stop_reason.startswith("acceptance_") or stop_reason == "plan_incomplete":
                break
    ledger_file = session.root / "tasks" / plan_task_id / "plan" / "execution.v1.json"
    ledger = json.loads(ledger_file.read_text(encoding="utf-8")) if ledger_file.is_file() else {}
    acc_file = session.root / "tasks" / plan_task_id / "acceptance.v1.json"
    acceptance = json.loads(acc_file.read_text(encoding="utf-8")).get("status") if acc_file.is_file() else None
    steps = {item["step_id"]: item["status"] for item in ledger.get("steps", [])}
    plan_ok = done["result"].get("status") == "done" and steps == {"s1": "done", "s2": "done"} and acceptance is True
    # 3) read-only subagent smoke
    sub = await _run_subagent(config, workspace, session)
    ok = plan_ok and sub.get("status") == "ok"
    return {
        "ok": ok, "stage": "execute", "verify_command": verify_command,
        "task_id": plan_task_id, "run_status": done["result"].get("status"),
        "steps": steps, "acceptance": acceptance, "approvals": approvals,
        "approval_tools": approval_tools,
        "stop_reason": done["result"].get("stop_reason"),
        "subagent": sub,
    }



async def _run_repair(config: Config, workspace: Path,
                      verify_command: str = "test -f .vibe/real-p1-repair-marker.txt") -> dict:
    """真实验收失败 → 修复回灌 → 重新验收（VIBE_ACCEPTANCE_REPAIR_ATTEMPTS=1）。"""
    session = SessionStore.new(workspace, "openai", purpose="user")

    def adjust_repair_plan(loaded) -> None:
        # 修复场景需要写 marker；清掉模型可能生成的“只读/不改文件”约束，保持计划自洽。
        loaded.constraints = [item for item in loaded.constraints
                              if "只读" not in item and "不修改" not in item]
        loaded.out_of_scope = [item for item in loaded.out_of_scope
                               if "修改" not in item and "创建" not in item]

    plan_task_id, plan = await _generate_plan(
        config, workspace, session, verify_command, adjust=adjust_repair_plan,
    )
    if plan is None:
        return {"ok": False, "stage": "generate", "error": "no plan.v1.json"}
    approvals = 0
    approval_tools: list[str] = []
    done = None
    previous = os.environ.get("VIBE_ACCEPTANCE_REPAIR_ATTEMPTS")
    os.environ["VIBE_ACCEPTANCE_REPAIR_ATTEMPTS"] = "1"
    try:
        for attempt in range(2):
            request_id = f"repair-{attempt + 1}"
            exe = Harness(_agent(config, workspace), config, session)
            try:
                await exe.until("ready")
                exe.send("run", request_id, text="按计划执行 s1、s2，全部完成后结束。", task={
                    "kind": "run", "mode": "act", "objective": "执行真实修复计划",
                    "plan_id": plan["plan_id"], "plan_version": plan["version"],
                })
                while True:
                    event = await asyncio.wait_for(exe.outgoing.get(), 300)
                    if event["type"] == "approval_request":
                        approvals += 1
                        call = event.get("call") if isinstance(event.get("call"), dict) else {}
                        approval_tools.append(str(call.get("name") or event.get("tool") or "tool"))
                        exe.send("approval", f"approval-{approvals}",
                                 approval_id=event["approval_id"], decision="once")
                    if event["type"] == "done" and event.get("id") == request_id:
                        done = event
                        break
            finally:
                await exe.close()
            # 与前台路径一致：只有 provider 错误才重试一次；验收失败/计划问题不再重试。
            if done is None or done["result"].get("status") != "error":
                break
    finally:
        if previous is None:
            os.environ.pop("VIBE_ACCEPTANCE_REPAIR_ATTEMPTS", None)
        else:
            os.environ["VIBE_ACCEPTANCE_REPAIR_ATTEMPTS"] = previous
    ledger_file = session.root / "tasks" / plan_task_id / "plan" / "execution.v1.json"
    ledger = json.loads(ledger_file.read_text(encoding="utf-8")) if ledger_file.is_file() else {}
    acc_file = session.root / "tasks" / plan_task_id / "acceptance.v1.json"
    acceptance = json.loads(acc_file.read_text(encoding="utf-8")).get("status") if acc_file.is_file() else None
    steps = {item["step_id"]: item["status"] for item in ledger.get("steps", [])}
    from app.runtime.notifications import read_notifications
    repairs = [item for item in read_notifications(workspace) if item["kind"] == "plan_repair"]
    marker = (workspace / ".vibe" / "real-p1-repair-marker.txt").is_file()
    status = done["result"].get("status") if done else None
    ok = status == "done" and acceptance is True and bool(repairs) and marker
    return {
        "ok": ok, "stage": "repair", "verify_command": verify_command,
        "task_id": plan_task_id, "run_status": status,
        "steps": steps, "acceptance": acceptance, "approvals": approvals,
        "approval_tools": approval_tools,
        "repair_attempts": len(repairs), "marker_created": marker,
        "stop_reason": done["result"].get("stop_reason") if done else None,
        "text": (done["result"].get("text") or "")[:800] if done else None,
    }


async def _run_merge(config: Config, workspace: Path,
                     verify_command: str = "grep -q merge-ok merge-marker.txt") -> dict:
    """真实模型 edit 计划 → 后台 worktree 执行 → 合并审批 → 合并后按计划重验。"""
    session = SessionStore.new(workspace, "openai", purpose="user")
    edit_prompt = (
        "必须调用 submit_plan 一次，提交恰好两个步骤的计划："
        's1 读取 README.md（kind=read）；'
        's2 创建 merge-marker.txt，内容为 merge-ok（kind=edit，files 只含 ["merge-marker.txt"]），'
        's2 depends_on ["s1"]。提交后立即结束。'
    )

    def adjust(plan) -> None:
        if len(plan.steps) >= 2:
            plan.steps[1].kind = "edit"
            plan.steps[1].files = ["merge-marker.txt"]
            plan.steps[1].title = "创建 merge-marker.txt，内容 merge-ok"

    plan_task_id, plan = await _generate_plan(
        config, workspace, session, verify_command, prompt=edit_prompt, adjust=adjust,
    )
    if plan is None:
        return {"ok": False, "stage": "generate", "error": "no plan.v1.json"}
    prep = Harness(_agent(config, workspace), config, session)
    try:
        await prep.until("ready")
        prep.send("command", "bg-1", command="plan", args={"action": "prepare-background"})
        prepared = await prep.until("command_result", "bg-1")
    finally:
        await prep.close()
    match = re.search(r"task_id=(t-[0-9a-zA-Z-]+)", str(prepared.get("text") or ""))
    if not match:
        return {"ok": False, "stage": "prepare-background",
                "error": str(prepared.get("text") or "")[:500]}
    bg_task_id = match.group(1)
    (workspace / ".vibe").mkdir(exist_ok=True)
    (workspace / ".vibe" / "unattended.json").write_text(
        '{"schema_version":1,"allow_exec":true}', encoding="utf-8")
    env = dict(os.environ, VIBE_TASK_RUNTIME="1", VIBE_AUTONOMY="full",
               VIBE_LLM_TIMEOUT=str(config.llm_timeout), VIBE_MAX_STEPS="20",
               VIBE_MAX_RUN_TOKENS="150000", VIBE_ACCEPTANCE_REPAIR_ATTEMPTS="1")
    worker = subprocess.run(
        [sys.executable, "-m", "app", "--worker", "--workspace", str(workspace),
         "--session", session.id, "--task-id", bg_task_id],
        cwd=str(Path(__file__).resolve().parents[1]), env=env,
        capture_output=True, text=True, timeout=420,
    )
    from app.runtime.tasks import TaskStore
    final_task = TaskStore(session.root).get(bg_task_id)
    ledger_file = session.root / "tasks" / plan_task_id / "plan" / "execution.v1.json"
    ledger = json.loads(ledger_file.read_text(encoding="utf-8")) if ledger_file.is_file() else {}
    acc_file = session.root / "tasks" / plan_task_id / "acceptance.v1.json"
    acceptance = json.loads(acc_file.read_text(encoding="utf-8")).get("status") if acc_file.is_file() else None
    steps = {item["step_id"]: item["status"] for item in ledger.get("steps", [])}
    if final_task.status != "completed" or acceptance is not True:
        return {"ok": False, "stage": "execute", "worker_exit": worker.returncode,
                "worker_stderr": (worker.stderr or "")[-1500:],
                "task_status": final_task.status, "steps": steps, "acceptance": acceptance}

    root = str(Path(__file__).resolve().parents[1])
    merge_cmd = [sys.executable, "-m", "app", "--merge-task", bg_task_id,
                 "--workspace", str(workspace), "--session", session.id]
    requested = subprocess.run(merge_cmd, cwd=root, env=dict(os.environ),
                               capture_output=True, text=True, timeout=120)
    approval = re.search(r"approval requested: (a-[0-9a-zA-Z]+)", requested.stdout or "")
    if requested.returncode != 2 or not approval:
        return {"ok": False, "stage": "merge-request", "returncode": requested.returncode,
                "stdout": (requested.stdout or "")[-1000:],
                "stderr": (requested.stderr or "")[-1000:]}
    approved = subprocess.run(merge_cmd + ["--approve-merge", approval.group(1)],
                              cwd=root, env=dict(os.environ),
                              capture_output=True, text=True, timeout=180)
    result: dict = {}
    try:
        text = approved.stdout or ""
        result = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        pass
    marker = workspace / "merge-marker.txt"
    from app.runtime.notifications import read_notifications
    notifications = read_notifications(workspace)
    kinds = [item.get("kind") for item in notifications]
    completed = [item for item in notifications if item.get("kind") == "merge_completed"]
    ok = (
        approved.returncode == 0
        and result.get("merged") is True
        and result.get("post_merge_verified") is True
        and bool(result.get("verify_commands"))
        and marker.is_file()
        and "merge-ok" in marker.read_text(encoding="utf-8", errors="replace")
        and "merge_approval_requested" in kinds
        and completed and completed[-1].get("verified") is True
    )
    return {
        "ok": bool(ok), "stage": "merge", "worker_exit": worker.returncode,
        "task_status": final_task.status, "steps": steps, "acceptance": acceptance,
        "approval_id": approval.group(1), "merge_returncode": approved.returncode,
        "merged": result.get("merged"), "post_merge_verified": result.get("post_merge_verified"),
        "verify_commands": result.get("verify_commands"),
        "merge_stderr": (approved.stderr or "")[-1000:],
        "notifications": {"merge_approval_requested": "merge_approval_requested" in kinds,
                          "merge_completed_verified": bool(completed and completed[-1].get("verified"))},
    }


def _agent_version() -> str:
    override = os.getenv("VIBE_BENCH_AGENT_VERSION", "").strip()
    if override:
        return override
    try:
        out = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parents[1]), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        value = out.stdout.strip()
        return value if out.returncode == 0 and value else "unknown"
    except Exception:
        return "unknown"


async def _run_suite(config: Config, workspace: Path, verify_command: str = "true") -> dict:
    """一次跑完前台/后台/修复回灌/合并重验四种真实链路，返回聚合结果。"""
    modes = [
        ("foreground", lambda: _run(config, workspace, verify_command)),
        ("background", lambda: _run(config, workspace, verify_command, background=True)),
        ("queue", lambda: _run_queue(config, workspace)),
        ("goal", lambda: _run_goal_stage(config, workspace)),
        ("repair", lambda: _run_repair(config, workspace)),
        ("merge", lambda: _run_merge(config, workspace)),
    ]
    results: dict[str, dict] = {}
    for name, runner in modes:
        try:
            results[name] = await runner()
        except Exception as exc:  # 单模式异常不阻断后续模式
            results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": all(bool(item.get("ok")) for item in results.values()),
        "stage": "suite",
        "modes": {
            name: {key: item.get(key) for key in (
                "ok", "stage", "run_status", "task_status", "steps", "acceptance",
                "repair_attempts", "merged", "post_merge_verified", "verified",
                "queue_completed", "queue_stopped_early", "dry_run_executable", "error",
            ) if key in item}
            for name, item in results.items()
        },
        "detail": results,
    }


async def _run_queue(config: Config, workspace: Path) -> dict:
    """真实计划 → --prepare-plan-task → --run-plan-queue 串行 drain 的真实链路。"""
    session = SessionStore.new(workspace, "openai", purpose="user")
    plan_task_id, plan = await _generate_plan(config, workspace, session, "true")
    if plan is None:
        return {"ok": False, "stage": "generate", "error": "no plan.v1.json"}
    (workspace / ".vibe").mkdir(exist_ok=True)
    (workspace / ".vibe" / "unattended.json").write_text(
        '{"schema_version":1,"allow_exec":true}', encoding="utf-8")
    root = str(Path(__file__).resolve().parents[1])
    base = [sys.executable, "-m", "app", "--workspace", str(workspace), "--session", session.id]
    env = dict(os.environ, VIBE_AUTONOMY="full")
    prepared = subprocess.run(base + ["--prepare-plan-task"], cwd=root, env=env,
                              capture_output=True, text=True, timeout=120)
    task_id = None
    try:
        prepared_payload = json.loads((prepared.stdout or "").strip())
        task_id = prepared_payload.get("task_id")
    except json.JSONDecodeError:
        pass
    if prepared.returncode != 0 or not task_id:
        return {"ok": False, "stage": "prepare",
                "stdout": (prepared.stdout or "")[-1000:],
                "stderr": (prepared.stderr or "")[-1000:]}
    dry = subprocess.run(base + ["--run-plan-queue", "--queue-dry-run"], cwd=root, env=env,
                         capture_output=True, text=True, timeout=60)
    dry_payload: dict = {}
    try:
        dry_payload = json.loads((dry.stdout or "").strip())
    except json.JSONDecodeError:
        pass
    run = subprocess.run(base + ["--run-plan-queue", "--queue-task-timeout", "420"],
                         cwd=root, env=env, capture_output=True, text=True, timeout=600)
    payload: dict = {}
    try:
        payload = json.loads((run.stdout or "").strip())
    except json.JSONDecodeError:
        pass
    from app.runtime.tasks import TaskStore
    final_task = TaskStore(session.root).get(task_id)
    ledger_file = session.root / "tasks" / plan_task_id / "plan" / "execution.v1.json"
    ledger = json.loads(ledger_file.read_text(encoding="utf-8")) if ledger_file.is_file() else {}
    acc_file = session.root / "tasks" / plan_task_id / "acceptance.v1.json"
    acceptance = json.loads(acc_file.read_text(encoding="utf-8")).get("status") if acc_file.is_file() else None
    steps = {item["step_id"]: item["status"] for item in ledger.get("steps", [])}
    dry_rows = dry_payload.get("queue") or []
    ok = (
        dry.returncode == 0
        and bool(dry_rows) and dry_rows[0].get("plan_executable") is True
        and run.returncode == 0
        and payload.get("completed") == 1 and payload.get("stopped_early") is None
        and final_task.status == "completed"
        and steps == {"s1": "done", "s2": "done"} and acceptance is True
    )
    return {
        "ok": ok, "stage": "queue", "task_id": task_id,
        "dry_run_returncode": dry.returncode,
        "dry_run_executable": bool(dry_rows and dry_rows[0].get("plan_executable")),
        "queue_returncode": run.returncode,
        "queue_completed": payload.get("completed"),
        "queue_stopped_early": payload.get("stopped_early"),
        "task_status": final_task.status, "steps": steps, "acceptance": acceptance,
        "stderr": (run.stderr or "")[-1000:],
    }


async def _run_goal_stage(config: Config, workspace: Path) -> dict:
    """真实模型 goal run + 已确认 criteria 可信验收（goal_acceptance）。"""
    from app.runtime.goals import GoalStore
    from app.runtime.tasks import TaskStore

    session = SessionStore.new(workspace, "openai", purpose="user")
    store = TaskStore(session.root)
    task = store.create(kind="goal", objective="真实目标验收", workspace=str(workspace), mode="goal")
    goals = GoalStore(session.root)
    record = goals.ensure_for_task(task)
    record.acceptance_criteria = [{"id": "ac1", "text": "可信命令通过", "confirmed": True}]
    record.verification_plan = [{"command": "true"}]
    goals.save(record)
    approvals = 0
    done = None
    harness = Harness(_agent(config, workspace), config, session)
    try:
        await harness.until("ready")
        harness.send("run", "goal-1", text="用一句话说明当前工作区用途后结束。", task={
            "kind": "goal", "task_id": task.task_id, "objective": task.objective,
        })
        while True:
            event = await asyncio.wait_for(harness.outgoing.get(), 300)
            if event["type"] == "approval_request":
                approvals += 1
                harness.send("approval", f"approval-{approvals}",
                             approval_id=event["approval_id"], decision="once")
            if event["type"] == "done" and event.get("id") == "goal-1":
                done = event
                break
    finally:
        await harness.close()
    final = store.get(task.task_id)
    from app.runtime.notifications import read_notifications
    events = [item for item in read_notifications(workspace) if item.get("kind") == "goal_acceptance"]
    acceptance_file = session.root / "tasks" / task.task_id / "acceptance.v1.json"
    acceptance = None
    if acceptance_file.is_file():
        try:
            acceptance = json.loads(acceptance_file.read_text(encoding="utf-8")).get("status")
        except (OSError, json.JSONDecodeError):
            acceptance = None
    verified = bool(done and done["result"].get("verified") is True)
    ok = (
        done is not None and done["result"].get("status") == "done"
        and verified and final.status == "completed"
        and acceptance is True
        and bool(events) and events[-1].get("status") is True
    )
    return {
        "ok": ok, "stage": "goal", "task_id": task.task_id,
        "run_status": done["result"].get("status") if done else None,
        "verified": verified, "task_status": final.status,
        "acceptance": acceptance, "approvals": approvals,
        "stop_reason": done["result"].get("stop_reason") if done else None,
    }

async def _run_subagent(config: Config, workspace: Path, session: SessionStore) -> dict:
    harness = Harness(_agent(config, workspace), config, session)
    await harness.until("ready")
    harness.send("command", "explore-1", command="explore",
                 args={"prompt": "读取 README.md，一句话说明项目用途。只读。"})
    result = await harness.until("command_result", "explore-1")
    events = [event for event in harness.events if event["type"] == "subagent_event"]
    await harness.close()
    status = events[-1].get("status") if events else "missing"
    return {"status": status, "task_id": events[-1].get("task_id") if events else None,
            "text": str(result.get("text", ""))[:300]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=None, help="工作区；默认新建临时 git 仓库")
    parser.add_argument("--background", action="store_true",
                        help="使用后台 worker 执行计划（等价 /plan background）")
    parser.add_argument("--repair", action="store_true",
                        help="验收命令先失败，验证自动修复回灌后重新验收通过")
    parser.add_argument("--repair-verify-command", default="test -f .vibe/real-p1-repair-marker.txt",
                        help="--repair 模式的验收命令（默认要求创建 marker 文件）")
    parser.add_argument("--merge", action="store_true",
                        help="真实 edit 计划 → 后台 worktree 执行 → 合并审批 → 合并后重验")
    parser.add_argument("--merge-verify-command", default="grep -q merge-ok merge-marker.txt",
                        help="--merge 模式的验收命令（同时用于合并后重验）")
    parser.add_argument("--suite", action="store_true",
                        help="依次跑 foreground/background/queue/goal/repair/merge 六种真实模型验收")
    parser.add_argument("--suite-runs", type=int, default=1, metavar="N",
                        help="与 --suite 搭配：重复次数（1-5，默认 1；用于稳定性样本）")
    parser.add_argument("--verify-command", default="true",
                        help="计划验收命令（默认 true；需为可信 source=plan_approval）")
    parser.add_argument("--keep", action="store_true", help="保留临时工作区")
    parser.add_argument("--discard-on-failure", action="store_true",
                        help="失败时也删除临时工作区（默认保留便于诊断）")
    args = parser.parse_args(argv)
    if args.suite_runs != 1 and not args.suite:
        print(json.dumps({"ok": False, "error": "--suite-runs requires --suite"}, ensure_ascii=False))
        return 2
    if args.suite_runs < 1 or args.suite_runs > 5:
        print(json.dumps({"ok": False, "error": "--suite-runs must be between 1 and 5"},
                         ensure_ascii=False))
        return 2
    config = Config.from_env()
    if not config.api_key:
        print(json.dumps({"ok": False, "error": "missing API key"}, ensure_ascii=False))
        return 2
    temporary = not bool(args.workspace)
    workspace = Path(args.workspace or tempfile.mkdtemp(prefix="mini-harness-real-p1-"))
    if temporary or not (workspace / ".git").exists():
        _init_repo(workspace)
    config.workspace = workspace
    try:
        if args.repair:
            result = asyncio.run(_run_repair(config, workspace, args.repair_verify_command))
        elif args.merge:
            result = asyncio.run(_run_merge(config, workspace, args.merge_verify_command))
        elif args.suite and args.suite_runs > 1:
            runs = []
            for index in range(args.suite_runs):
                run_workspace = workspace / f"run-{index + 1}"
                _init_repo(run_workspace)
                config.workspace = run_workspace
                run_result = asyncio.run(_run_suite(config, run_workspace, args.verify_command))
                modes = {name: bool(mode.get("ok")) for name, mode in (run_result.get("modes") or {}).items()}
                runs.append({"run": index + 1, "ok": bool(run_result.get("ok")),
                             "modes": modes,
                             "workspace": str(run_workspace)})
            result = {
                "ok": all(item["ok"] for item in runs),
                "suite_runs": len(runs),
                "passed_runs": sum(1 for item in runs if item["ok"]),
                "runs": runs,
            }
            config.workspace = workspace
        elif args.suite:
            result = asyncio.run(_run_suite(config, workspace, args.verify_command))
        else:
            result = asyncio.run(_run(config, workspace, args.verify_command, args.background))
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if isinstance(result, dict):
        result.setdefault("agent_version", _agent_version())
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = workspace / ".vibe" / "real-p1-acceptance"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{stamp}.json"
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**result, "report": str(out_file)}, ensure_ascii=False, indent=2))
    if temporary and not args.keep and (result.get("ok") or args.discard_on_failure):
        shutil.rmtree(workspace, ignore_errors=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
