"""计划验收命令执行（15 P1 起步）：可信命令经 ApprovalGate 后由 harness 执行。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.runtime.acceptance import PlanAcceptance, VerificationCommand
from app.safety.sandbox import run_command_async

TRUSTED_COMMAND_SOURCES = {"project_profile", "plan_approval"}


def command_digest(command: str, exit_code: int | None, output: str) -> str:
    payload = f"{command}\n{exit_code}\n{output[:4000]}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def run_verification_plan(
    acceptance: PlanAcceptance,
    workspace: str | Path,
    approval_gate: Any,
    *,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """逐条执行 verification plan；未批准/命令不可信一律不执行。"""
    root = Path(workspace)
    results: dict[str, dict[str, Any]] = {}
    for item in acceptance.verification_plan:
        if item.source not in TRUSTED_COMMAND_SOURCES:
            results[item.id] = {"ok": False, "reason": "untrusted_command"}
            continue
        from core.contracts import ToolCall
        call = ToolCall("bash", {"command": item.command, "cwd": "."}, id=f"verify-{item.id}")
        try:
            decision = await approval_gate.request(call, f"验收命令 {item.id}: {item.command}", root)
        except Exception as exc:
            results[item.id] = {"ok": False, "reason": f"approval_error: {type(exc).__name__}"}
            continue
        if not getattr(decision, "allowed", False):
            results[item.id] = {"ok": False, "reason": "approval_denied"}
            continue
        code, output, timed_out = await run_command_async(
            item.command, root, timeout=timeout, max_chars=20_000,
        )
        ok = (not timed_out) and code == 0
        results[item.id] = {
            "ok": ok, "exit_code": code, "timed_out": timed_out,
            "digest": command_digest(item.command, code, output),
            "output": output[-4000:],
        }
    status, codes = acceptance.status(results)
    return {"results": results, "status": status, "codes": codes}


def trusted_commands(acceptance: PlanAcceptance) -> list[VerificationCommand]:
    return [item for item in acceptance.verification_plan
            if item.source in TRUSTED_COMMAND_SOURCES and item.command.strip()]
