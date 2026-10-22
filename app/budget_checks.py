from __future__ import annotations

import json
import shlex
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from app.agent import Agent
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.tools import ToolRegistry
from core.budget import BudgetPolicy
from core.context import ContextBuilder, estimate_tokens, total_tokens
from core.models import Message, Response, ToolCall
from core.state import TaskState

LEVELS = ("context", "compaction", "total")


async def _allow_check(call: ToolCall, preview: str) -> ApprovalDecision:
    return ApprovalDecision(True, "once")


async def _prepare(gate: ApprovalGate, path: Path, root: Path, content: str | None = None) -> None:
    relative = str(path.relative_to(root))
    decision = await gate.request(
        ToolCall("write_file", {"path": relative, "content": content or ""}),
        f"offline budget check artifact: {relative}", root,
    )
    if not decision.allowed:
        raise PermissionError(f"budget check artifact denied: {relative}")
    if content is None:
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _failed(level: str, exc: Exception) -> dict:
    return {"level": level, "status": "error", "checks": {}, "agent_status": "error",
            "stop_reason": "exception", "usage": {}, "error": f"{type(exc).__name__}: {exc}"}


def _skipped(level: str) -> dict:
    return {"level": level, "status": "skipped", "checks": {}, "agent_status": "",
            "stop_reason": "previous_layer_failed", "usage": {}, "error": None}


async def _run_layer(level: str, run_dir: Path, gate: ApprovalGate) -> dict:
    workspace = run_dir / level
    artifacts = workspace / ".vibe"
    await _prepare(gate, artifacts / "offload", run_dir)
    skills = artifacts / "skills.json"
    await _prepare(gate, skills, run_dir, '{"discover": false, "paths": []}')
    window = 1024 if level == "context" else 32_000
    policy = BudgetPolicy.for_context_window(window, output_cap=128 if level == "context" else 1024)
    context = ContextBuilder(policy=policy, keep_recent_groups=1, offload_dir=artifacts / "offload")
    task = "修复 calc.add 并运行验证，保留原始测试。"
    history: list[Message] = []
    limit = 5000 if level == "total" else 100_000
    interval = 10_000 if level == "compaction" else 0
    if level == "context":
        task = "固定且不可截断的任务：" + "x" * 12_000
        script = [Response("unexpected request", usage={"total_tokens": 10})]
    elif level == "total":
        command = f"{shlex.quote(sys.executable)} -c " + shlex.quote(
            "from pathlib import Path; Path('never-command.txt').write_text('must not exist')")
        script = [Response(tool_calls=[
            ToolCall("write_file", {"path": "never.txt", "content": "must not exist"}, id="blocked-write"),
            ToolCall("bash", {"command": command}, id="blocked-command"),
        ], usage={"prompt_tokens": 4900, "completion_tokens": 100, "total_tokens": 5000})]
    else:
        await _prepare(gate, workspace / "calc.py", run_dir, "def add(a, b):\n    return a - b\n")
        await _prepare(gate, workspace / "test_calc.py", run_dir,
                       "from calc import add\nassert add(2, 3) == 5\nassert add(-2, 1) == -1\nprint('checks passed')\n")
        history = [Message("user", task)]
        for index in range(6):
            history.extend([
                Message("assistant", tool_calls=[ToolCall("read_file", {"path": "calc.py"}, id=f"old-{index}")]),
                Message("tool", f"archived-{index}:" + "x" * 8000,
                        name="read_file", tool_call_id=f"old-{index}"),
            ])
        command = f"{shlex.quote(sys.executable)} test_calc.py"
        script = [
            Response(tool_calls=[ToolCall("read_file", {"path": "calc.py"}, id="read")],
                     usage={"prompt_tokens": 7900, "completion_tokens": 100, "total_tokens": 8000}),
            Response(tool_calls=[ToolCall("edit_file", {"path": "calc.py", "old": "return a - b",
                                                        "new": "return a + b"}, id="edit")],
                     usage={"prompt_tokens": 7900, "completion_tokens": 100, "total_tokens": 8000}),
            Response(tool_calls=[ToolCall("bash", {"command": command, "verification": True}, id="verify")],
                     usage={"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}),
            Response("修复已验证。", usage={"prompt_tokens": 90, "completion_tokens": 10, "total_tokens": 100}),
        ]
    provider = FakeProvider(script)
    state = TaskState(goal=task)
    registry = ToolRegistry(workspace, context=context, state=state, skills_config=str(skills))
    registry._tools = {name: tool for name, tool in registry._tools.items()
                       if name in {"read_file", "edit_file", "write_file", "bash"}}
    agent = Agent(provider, registry, context=context, state=state, approval=gate, history=history,
                  system="Complete the small local coding task and verify the change.",
                  max_run_tokens=limit, compact_interval_tokens=interval, max_steps=8,
                  max_run_seconds=30, llm_retries=0, tool_retries=0)
    traces: list[dict] = []
    messages = list(history)
    history_updates: list[list[Message]] = []
    outcomes: list[tuple[ToolCall, bool, str]] = []
    result = await agent.run(task, trace=traces.append, on_message=messages.append,
                             on_history=history_updates.append,
                             on_result=lambda call, ok, output: outcomes.append((call, ok, output)))
    await _prepare(gate, artifacts / "trace.jsonl", run_dir,
                   "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in traces))
    await _prepare(gate, artifacts / "messages.jsonl", run_dir,
                   "".join(json.dumps(asdict(message), ensure_ascii=False) + "\n" for message in messages))
    await _prepare(gate, artifacts / "context.json", run_dir, _json([asdict(message) for message in agent.history]))
    requests = [total_tokens(batch) + estimate_tokens(json.dumps(specs, ensure_ascii=False))
                for batch, specs in zip(provider.seen, provider.seen_tools)]
    capacity = policy.prompt_cap - policy.scratch_cap
    evidence = {"workspace": str(workspace), "requests": len(provider.seen),
                "request_tokens": requests, "request_capacity": capacity,
                "trace": str(artifacts / "trace.jsonl"), "messages": str(artifacts / "messages.jsonl")}
    common = {"one_run": sum(event["type"] == "run_start" for event in traces) == 1
              and sum(event["type"] == "run_end" for event in traces) == 1,
              "requests_fit_context": all(tokens <= capacity for tokens in requests)}
    if level == "context":
        checks = {**common, "expected_context_stop": result.status == "stopped" and result.stop_reason == "context_window",
                  "no_request": not provider.seen, "no_usage": result.usage["total_tokens"] == 0}
    elif level == "total":
        audit = [json.loads(line) for line in (run_dir / "audit.jsonl").read_text().splitlines()]
        checks = {**common, "expected_budget_stop": result.status == "stopped" and result.stop_reason == "token_budget",
                  "usage_preserved": result.usage["total_tokens"] == 5000,
                  "unexecuted_write": not (workspace / "never.txt").exists()
                  and any(event["type"] == "tool_not_executed" and event["tool"] == "write_file" for event in traces),
                  "unexecuted_command": not (workspace / "never-command.txt").exists()
                  and any(event["type"] == "tool_not_executed" and event["tool"] == "bash" for event in traces),
                  "no_tool_approval": not any(event["workspace"] == str(workspace.resolve()) for event in audit),
                  "no_tool_side_effects": not outcomes}
    else:
        compactions = [event for event in traces if event["type"] == "history_compacted"]
        refs = [ref for event in compactions for ref in event["offloaded"]]
        archives = [context.resolve(ref) for ref in refs]
        budget_used = [event["used_tokens"] for event in traces if event["type"] == "budget_update"]
        checks = {**common, "completed": result.status == "done",
                  "interval_compaction": len(compactions) == 2
                  and all(event["reason"] == "interval" for event in compactions)
                  and any(event["after_tokens"] < event["before_tokens"] for event in compactions),
                  "tools_continue_after_compaction": len(provider.seen) == 4 and all(provider.seen_tools)
                  and [call.name for call, ok, _ in outcomes if ok] == ["read_file", "edit_file", "bash"],
                  "usage_accumulates": result.usage["total_tokens"] == 16400 and budget_used == sorted(budget_used),
                  "raw_history_preserved": any(message.content.startswith("archived-0:") for message in messages)
                  and bool(archives) and any("archived-0:" in str(archive) for archive in archives),
                  "offload_persisted": bool(refs) and all(
                      (artifacts / "offload" / f"{ref}.json").is_file() for ref in refs),
                  "summary_reference_preserved": bool(refs) and any(
                      f"[offloaded: {ref}]" in message.content for ref in refs for message in provider.seen[1]),
                  "code_repaired": (workspace / "calc.py").read_text() == "def add(a, b):\n    return a + b\n",
                  "test_fixture_unchanged": (workspace / "test_calc.py").read_text()
                  == "from calc import add\nassert add(2, 3) == 5\nassert add(-2, 1) == -1\nprint('checks passed')\n",
                  "verification_succeeded": bool(state.command_results) and state.command_results[-1]["verification"]
                  and state.command_results[-1]["status"] == "succeeded" and state.command_results[-1]["exit_code"] == 0}
        evidence.update({"offload_refs": refs, "command_results": state.command_results,
                         "compactions": len(compactions), "history_updates": len(history_updates)})
    error = getattr(result, "error", None) or (result.text if result.status == "error" else None)
    if any(not ok for _, ok, _ in outcomes):
        error = "; ".join(f"{call.name}: {output}" for call, ok, output in outcomes if not ok)
    return {"level": level, "status": "error" if error else "passed" if all(checks.values()) else "failed",
            "checks": checks, "agent_status": result.status, "stop_reason": result.stop_reason,
            "usage": result.usage, "error": error, "evidence": evidence,
            "budget": {"max_run_tokens": limit, "context_window": window, "compact_interval_tokens": interval}}


async def run_budget_checks(output_root: Path, level: str = "all") -> dict:
    """离线验收预算边界；脚本结果不代表模型成绩。"""
    if level not in ("all", *LEVELS):
        raise ValueError(f"unknown budget check level: {level}")
    selected = LEVELS if level == "all" else (level,)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    run_dir = Path(output_root).resolve() / "budget-checks" / stamp
    gate = ApprovalGate(_allow_check, run_dir / "audit.jsonl")
    results = []
    for current in selected:
        if results and results[-1]["status"] != "passed":
            results.append(_skipped(current))
            continue
        try:
            results.append(await _run_layer(current, run_dir, gate))
        except Exception as exc:
            results.append(_failed(current, exc))
    report = {"run_dir": str(run_dir), "total": len(results),
              "passed": sum(result["status"] == "passed" for result in results),
              "fake": True, "results": results}
    try:
        await _prepare(gate, run_dir / "report.json", run_dir, _json(report))
    except Exception as exc:
        for result in results:
            result["status"] = "error"
            result["error"] = f"report: {type(exc).__name__}: {exc}"
        report["passed"] = 0
    return report
