#!/usr/bin/env python3
"""真实模型 Plan P1 验收脚本：生成计划 → 批准 → 执行 → 审批 → 验收。

需要已配置 VIBE_API_KEY（或 OPENAI/DEEPSEEK key）。默认自动回送审批帧模拟 TUI 用户。
输出 JSON 摘要并写 <workspace>/.vibe/real-p1-acceptance/<timestamp>.json；失败 exit 1。
"""
from __future__ import annotations

import argparse
import asyncio
import json
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


async def _run(config: Config, workspace: Path) -> dict:
    session = SessionStore.new(workspace, "openai", purpose="user")
    # 1) generate plan
    gen = Harness(_agent(config, workspace), config, session)
    await gen.until("ready")
    prompt = (
        "必须调用 submit_plan 一次，提交恰好两个步骤的只读计划："
        '{"step_id":"s1","title":"读取 README.md","kind":"read"} 和 '
        '{"step_id":"s2","title":"总结项目用途","kind":"read"}，s2 depends_on ["s1"]。'
        "提交后立即结束。"
    )
    gen.send("run", "gen-1", text=prompt, task={"kind": "run", "mode": "plan", "objective": "真实 P1 计划"})
    state = await gen.until("task_state", "gen-1")
    plan_task_id = state["task"]["task_id"]
    await gen.until("done", "gen-1")
    await gen.close()
    plan_file = session.root / "tasks" / plan_task_id / "plan" / "plan.v1.json"
    if not plan_file.is_file():
        return {"ok": False, "stage": "generate", "error": "no plan.v1.json"}
    loaded = load_plan(plan_file)
    loaded.acceptance_criteria = [{"id": "ac1", "text": "验收命令成功", "confirmed": True}]
    loaded.verification_plan = [{"id": "v1", "covers": ["ac1"], "command": "true",
                                 "source": "plan_approval"}]
    save_plan(session.root / "tasks" / plan_task_id, loaded)
    approve_plan(session.root / "tasks" / plan_task_id, load_plan(plan_file), approved_by="smoke")
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    # 2) execute with approval frames
    exe = Harness(_agent(config, workspace), config, session)
    await exe.until("ready")
    exe.send("run", "exec-1", text="按计划执行 s1、s2，全部完成后结束。", task={
        "kind": "run", "mode": "act", "objective": "执行真实 P1 计划",
        "plan_id": plan["plan_id"], "plan_version": plan["version"],
    })
    approvals = 0
    approval_tools: list[str] = []
    while True:
        event = await asyncio.wait_for(exe.outgoing.get(), 300)
        if event["type"] == "approval_request":
            approvals += 1
            call = event.get("call") if isinstance(event.get("call"), dict) else {}
            approval_tools.append(str(call.get("name") or event.get("tool") or "tool"))
            exe.send("approval", f"approval-{approvals}",
                     approval_id=event["approval_id"], decision="once")
        if event["type"] == "done" and event["id"] == "exec-1":
            done = event
            break
    await exe.close()
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
        "ok": ok, "stage": "execute", "task_id": plan_task_id, "run_status": done["result"].get("status"),
        "steps": steps, "acceptance": acceptance, "approvals": approvals,
        "approval_tools": approval_tools,
        "stop_reason": done["result"].get("stop_reason"),
        "subagent": sub,
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
    parser.add_argument("--keep", action="store_true", help="保留临时工作区")
    parser.add_argument("--keep-on-failure", action="store_true",
                        help="失败时保留临时工作区（默认删除）")
    args = parser.parse_args(argv)
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
        result = asyncio.run(_run(config, workspace))
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = workspace / ".vibe" / "real-p1-acceptance"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{stamp}.json"
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**result, "report": str(out_file)}, ensure_ascii=False, indent=2))
    if temporary and not args.keep and (result.get("ok") or not args.keep_on_failure):
        shutil.rmtree(workspace, ignore_errors=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
