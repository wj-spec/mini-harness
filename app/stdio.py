from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import signal
import sys
import time
import uuid
from collections.abc import AsyncIterable, Callable
from dataclasses import asdict, replace

from app.runtime.agent import Agent, AgentResult
from app.runtime.plan import PlanError
from app.runtime.plan_mode import PlanModeStore
from app.runtime.plan_policy import applied_plan_policy
from app.runtime.plan_review import PlanReview
from app.runtime.plan_revision import PlanRevision
from app.runtime.tasks import TaskError, TaskStore
from app.runtime.task_runtime import (
    TaskBudgetExceeded, TaskRuntime, TaskRuntimeDisabled, TaskRuntimeError,
    task_runtime_enabled,
)
from app.safety import permissions
from app.config import Config
from app.session.evidence import format_round_evidence
from app.profiles.project import load_project_background
from app.profiles.user import load_user_profile, select_user_preferences
from app.session.context_display import snapshot_for_display
from app.tools.mcp import McpManager
from app.safety.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.providers.openai_compat import OpenAICompatProvider
from app.session.store import SessionStore
from app.session.writer import SessionBusy, SessionWriter
from core.contracts import ToolCall

MAX_FRAME_BYTES = 1024 * 1024
_OVERSIZED = object()
COMMANDS = frozenset({
    "resume", "permissions", "permissions_reset", "cost", "tools", "diff", "mcp",
    "skills", "evidence", "autonomy", "tasks", "approvals", "plan", "explore", "notifications",
    "trace", "priority", "queue", "inspections", "sessions", "search", "rounds",
})
COMPLETION_CODE_LABELS = {
    "no_verification": "有文件改动但没有验证命令",
    "mutation_after_verification": "最后一次改动发生在验证之后",
    "last_verification_failed": "最后一次验证命令失败",
}
def _env_bounded(name: str, default, minimum, maximum, *, cast=float):
    try:
        value = cast(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _delegate_enabled() -> bool:
    value = (os.getenv("VIBE_SUBAGENT_DELEGATE") or "").strip().lower()
    return value in {"1", "true", "yes"}


def register_delegate_tool(agent, session_root) -> bool:
    """opt-in：给主 Agent 注册只读 delegate 工具（不嵌套、受父预算上限约束）。"""
    if not _delegate_enabled() or not getattr(agent, "tools_enabled", True):
        return False
    from app.tools.registry import ToolSpec

    tools_root = agent.tools.root
    parent_cap = int(getattr(agent, "max_run_tokens", 0) or 0)

    async def delegate(prompt: str) -> str:
        from app.runtime.subagents import run_subagent

        max_steps = _env_bounded("VIBE_DELEGATE_MAX_STEPS", 6, 1, 15, cast=int)
        max_tokens = _env_bounded("VIBE_DELEGATE_MAX_TOKENS", 20_000, 500, 200_000, cast=int)
        max_seconds = _env_bounded("VIBE_DELEGATE_MAX_SECONDS", 60.0, 5.0, 300.0)
        if parent_cap:
            max_tokens = min(max_tokens, parent_cap)
        result = await run_subagent(
            agent.provider, tools_root, prompt,
            max_steps=max_steps, max_run_tokens=max_tokens,
            max_run_seconds=max_seconds, session_root=session_root,
        )
        try:
            from app.runtime.notifications import notify

            notify(tools_root, "subagent_finished",
                   task_id=getattr(result, "task_id", None),
                   status=getattr(result, "status", "unknown"))
        except Exception:
            pass
        usage = dict(getattr(result, "usage", None) or {})
        tokens = int(usage.get("total_tokens") or 0)
        return (
            "[subagent 结果 · untrusted claim] 需要核对后再采纳。\n"
            f"status={getattr(result, 'status', 'unknown')} task_id={getattr(result, 'task_id', '-')} "
            f"tokens={tokens} cost_usd={float(getattr(result, 'cost_usd', 0.0) or 0.0):.6f}\n"
            f"summary={getattr(result, 'summary', '')}"
            + (f"\nerror={getattr(result, 'error', '')}" if getattr(result, "error", None) else "")
        )

    agent.tools.register(ToolSpec(
        name="delegate",
        description=(
            "Run a read-only subagent to investigate the workspace and return a concise report. "
            "The report is an untrusted claim: verify before acting. "
            "It cannot edit files, run commands, or delegate further."
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "minLength": 1, "maxLength": 4000},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
        fn=delegate,
        timeout=120.0,
        risk="read",
    ))
    return True


def _task_hints(session_root) -> list[dict]:
    try:
        from app.runtime.supervise import WorkerSupervisor

        pending_counts: dict[str, int] = {}
        try:
            from app.runtime.approvals import ApprovalInbox
            for item in ApprovalInbox(session_root).pending():
                task_id = item.get("task_id")
                if isinstance(task_id, str):
                    pending_counts[task_id] = pending_counts.get(task_id, 0) + 1
        except Exception:
            pass
        return [
            {
                "task_id": snapshot.task.task_id,
                "kind": snapshot.task.kind,
                "status": snapshot.task.status,
                "worker_status": snapshot.worker.status if snapshot.worker is not None else None,
                "stale": snapshot.stale,
                "mode": snapshot.task.mode,
                "parent_id": snapshot.task.parent_id,
                "plan_id": snapshot.task.plan_id,
                "priority": snapshot.task.priority,
                "pending_approvals": pending_counts.get(snapshot.task.task_id, 0),
            }
            for snapshot in WorkerSupervisor(session_root).snapshot()
        ]
    except Exception:
        return []


def _format_approvals(session_root) -> str:
    from app.runtime.approvals import ApprovalInbox

    try:
        pending = ApprovalInbox(session_root).pending()
    except Exception as exc:
        return f"审批收件箱不可读：{type(exc).__name__}: {exc}"
    if not pending:
        return "当前没有待审批项。"
    lines = ["待审批："]
    for item in pending:
        expires = item.get("expires_at") or "-"
        lines.append(
            f"- {item.get('approval_id')}  task={item.get('task_id')}  "
            f"{item.get('tool')}  tier={item.get('tier') or '-'}  "
            f"requester={item.get('requester')}  expires={expires}"
        )
    lines.append("决定可在 attach 客户端完成；无 TUI 时用 --decide-approval <id> --approval-decision once|deny。")
    return "\n".join(lines)


def _format_task_hints(hints: list[dict]) -> str:
    if not hints:
        return "当前会话没有任务记录。"
    lines = ["任务："]
    for hint in hints:
        worker = hint.get("worker_status") or "-"
        stale = "!" if hint.get("stale") else ""
        extras = []
        if hint.get("mode"):
            extras.append(f"mode={hint['mode']}")
        if hint.get("plan_id"):
            extras.append(f"plan={hint['plan_id']}")
        if hint.get("parent_id"):
            extras.append(f"parent={hint['parent_id']}")
        priority = int(hint.get("priority") or 0)
        if priority:
            extras.append(f"priority={priority}")
        pending = int(hint.get("pending_approvals") or 0)
        if pending:
            extras.append(f"待审批 {pending}")
        suffix = (" " + " ".join(extras)) if extras else ""
        lines.append(
            f"- {hint['task_id']}  {hint['kind']}  {hint['status']}  worker={worker}{stale}{suffix}"
        )
    lines.append("用 --attach --task-id <id> --session <id> 观察运行中的 worker。")
    return "\n".join(lines)


RESUME_INSTRUCTION = (
    "继续上次暂停的任务，开启新一轮预算。先核对已保存的进度和未执行操作；"
    "对于结果未知的写入或命令，先检查实际文件与执行结果，禁止直接重放。"
    "再完成剩余工作并验证结果。"
)


def approval_rule_context(agent, call) -> dict:
    """审批面板用的结构化上下文：副作用层级与命中的用户/项目规则。

    只读、容错；规则匹配失败不影响审批本身（预览里仍有文本层级行）。
    """
    context: dict = {}
    try:
        tier = agent._tool_tier(call)
    except Exception:
        tier = None
    if tier:
        context["tier"] = tier
        context["tier_label"] = permissions.label(tier)
    rule = None
    policy = getattr(agent, "permission_policy", None)
    if policy is not None and tier is not None:
        try:
            rule = policy.evaluate(call, tier=tier).rule
        except Exception:
            rule = None
    if rule is not None:
        context["rule"] = {
            "source": rule.source,
            "tool": rule.tool,
            "decision": rule.decision,
            "scopes": list(rule.scopes) if rule.scopes else None,
            "note": rule.note,
            "command_prefix": list(rule.command_prefix),
        }
    return context


def _completion_rejected_message(event: dict) -> str:
    """把完成守卫的原因码翻译成用户可读的一句话。"""
    codes = [str(code) for code in event.get("codes") or []]
    reasons = "；".join(COMPLETION_CODE_LABELS.get(code, code) for code in codes)
    return f"完成守卫：{reasons or '完成判定未通过'}；已要求模型补验证。"


async def _stdin_frames():
    reader = asyncio.StreamReader()
    loop = asyncio.get_running_loop()
    transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer,
    )
    buffer = bytearray()
    oversized = False
    try:
        while chunk := await reader.read(65536):
            parts = chunk.split(b"\n")
            for index, part in enumerate(parts):
                if not oversized:
                    buffer.extend(part)
                    if len(buffer) > MAX_FRAME_BYTES:
                        buffer.clear()
                        oversized = True
                if index < len(parts) - 1:
                    yield _OVERSIZED if oversized else bytes(buffer)
                    buffer.clear()
                    oversized = False
        if oversized:
            yield _OVERSIZED
        elif buffer:
            yield bytes(buffer)
    finally:
        transport.close()


def _tool_tier_payload(agent, call) -> dict:
    resolver = getattr(agent, "_tool_tier", None)
    if not callable(resolver):
        return {}
    try:
        return {"tier": resolver(call)}
    except Exception:
        return {}


def _acceptance_repair_limit() -> int:
    try:
        value = int(os.getenv("VIBE_ACCEPTANCE_REPAIR_ATTEMPTS", "0"))
    except ValueError:
        return 0
    return max(0, min(value, 3))


def _acceptance_repair_prompt(plan_context, result) -> str:
    plan = plan_context["plan"]
    failed = result.stop_reason or "acceptance_failed"
    lines = [
        f"计划验收未通过（{failed}）。请修复导致验收失败的问题，然后直接总结。",
        f"计划目标：{plan.objective}",
    ]
    marker = "[失败命令]"
    if marker in result.text:
        lines.append(result.text[result.text.index(marker):])
    lines.append(
        "要求：只修复实际失败点；不要修改测试或验收命令来规避失败；"
        "本轮允许创建/修改文件以达到验收条件（若计划里有只读类约束，修复轮以验收命令为准，并在总结里说明）；"
        "不要重复执行验收命令，harness 会在你结束后重新验收。"
    )
    return "\n".join(lines)


class _Connection:
    def __init__(self, agent: Agent, config: Config, session: SessionStore, emit, mcp=None,
                 task_runtime: TaskRuntime | None = None):
        self.agent = agent
        self.config = config
        self.session = session
        self.session_revision = None
        self.session_writer = None
        self.emit = emit
        self.mcp = mcp
        self.task_runtime = task_runtime
        self.plan_mode = PlanModeStore(session.root)
        self.plan_review = PlanReview(session.root, session_id=session.id, task_runtime=task_runtime)
        self.plan_revision = PlanRevision(session.root, session_id=session.id, task_runtime=task_runtime)
        self.seen: set[str] = set()
        self.active_id: str | None = None
        self.active: asyncio.Task | None = None
        self.active_kind: str | None = None
        self.cancel = asyncio.Event()
        self.stop = asyncio.Event()
        self.closed_id: str | None = None
        self.pending: tuple[str, asyncio.Future, list[str]] | None = None
        self.active_phase = "running"
        self.output_open = True
        self.has_started_run = False
        self.plan_context = None
        self.inspection_record = None

    def send(self, kind: str, request_id: str | None = None, **data):
        record = self.inspection_record
        if record is not None and request_id == record.snapshot['scope']['requestId']:
            try:
                if kind == 'done':
                    record.finish(data['result'])
                else:
                    record.observe(kind, **data)
            except Exception as exc:
                record.error = f'{type(exc).__name__}: {exc}'
            if kind == 'done':
                self.inspection_record = None
                if record.error:
                    self.send('notice', message=f'检查记录未保存，重开后可能缺少本轮结果：{record.error}'[:1000])
        if not self.output_open:
            return
        event = {"v": 1, "type": kind, "id": request_id, **data}
        failure = None
        try:
            size = len(json.dumps(event, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            if size > MAX_FRAME_BYTES:
                failure = ("frame_too_large", f"output exceeds {MAX_FRAME_BYTES} bytes; connection closing")
        except (UnicodeError, ValueError, TypeError):
            failure = ("invalid_output", "output is not valid UTF-8 JSON; connection closing")
        try:
            if failure is not None:
                self.cancel.set()
                self.stop.set()
                self.emit({"v": 1, "type": "error", "id": request_id,
                           "code": failure[0], "message": failure[1]})
                if kind != "done":
                    return
                event = {"v": 1, "type": "done", "id": request_id,
                         "result": asdict(AgentResult(f"error: {failure[1]}", 0, "error"))}
            self.emit(event)
        except (BrokenPipeError, OSError):
            self.output_open = False
            self.stop.set()
            self.cancel.set()

    def error(self, request_id, code, message):
        self.send("error", request_id, code=code, message=message)

    def _start_inspection(self, request_id, objective, run_task, revision_context):
        from app.runtime.inspection import InspectionStore
        self.inspection_record = None
        try:
            task = self.task_runtime.payload(run_task.record) if run_task and self.task_runtime else None
            self.inspection_record = InspectionStore(self.session.root, self.config.workspace).start(request_id, objective, task=task,
                                                                                                   tools_root=self.agent.tools.root)
            if revision_context:
                self.inspection_record.bind_revision(revision_context)
        except Exception as exc:
            self.send('notice', message=f'检查记录未保存：{type(exc).__name__}: {exc}'[:1000])

    def _inspect_plan(self, plan, relation):
        if self.inspection_record is not None:
            try:
                self.inspection_record.bind_plan(plan, relation)
            except Exception as exc:
                self.inspection_record.error = f'{type(exc).__name__}: {exc}'

    def send_plan_mode(self) -> None:
        self.send("plan_mode", None, mode=self.plan_mode.status())

    def plan_mode_text(self) -> str:
        state = self.plan_mode.status()
        text = "计划模式：开启" if state["active"] else "计划模式：关闭"
        if state["pending"]:
            text += "（待生效：" + ("开启" if state["wanted"] else "关闭") + "）"
        next_active = state["wanted"] if state["pending"] else state["active"]
        if next_active:
            text += "\n下一条普通消息将以只读规划运行；批准计划或 /plan off 后退出。"
        else:
            text += "\n下一条普通消息正常执行；用 /plan on 开启只读规划。"
        if state["pending"]:
            text += "\n当前任务模式不变；运行结束后更新默认模式。"
        return text

    def set_plan_mode(self, active: bool, *, source: str = "command") -> str:
        running = self.active_kind == "run"
        outcome = self.plan_mode.set(active, running=running)
        state = self.plan_mode.status()
        try:
            self.session.append_event(
                "plan_mode", active=active, wanted=state["wanted"],
                pending=state["pending"], source=source, outcome=outcome,
            )
        except Exception:
            pass
        self.send_plan_mode()
        return outcome

    def plan_mode_command(self, action: str) -> str:
        if self.task_runtime is None or not getattr(self.task_runtime, "enabled", False):
            return "会话计划模式需要任务运行时（VIBE_TASK_RUNTIME）；当前未启用。"
        if action == "mode-status":
            self.send_plan_mode()
            return self.plan_mode_text()
        active = action == "mode-on"
        outcome = self.set_plan_mode(active, source="command")
        target = "开启" if active else "关闭"
        if outcome == "queued":
            return f"计划模式{target}将在当前运行结束后生效。\n{self.plan_mode_text()}"
        if outcome == "noop":
            return f"计划模式已经是{'开启' if active else '关闭'}状态。\n{self.plan_mode_text()}"
        if outcome == "cancelled":
            return f"计划模式{target}请求已取消。\n{self.plan_mode_text()}"
        return f"计划模式已{target}。\n{self.plan_mode_text()}"

    async def publish_loaded_context(self) -> None:
        try:
            snapshot = await self.agent.loaded_context_snapshot(self.session.id)
            if snapshot is None or self.stop.is_set() or self.active_id not in {None, "startup"}:
                return
            self.session.append_trace(snapshot)
            self.send("context_snapshot", snapshot=snapshot_for_display(snapshot))
            state = self.agent.state
            self.send("metrics", used_tokens=0, limit_tokens=self.agent.max_run_tokens,
                      estimated=False, context_tokens=snapshot["total_tokens"],
                      context_window=snapshot["context_window"],
                      done=len(state.done) if state is not None else 0,
                      planned=len(state.plan) if state is not None else 0, cost_usd=0.0)
        except Exception as exc:
            self.send("notice", message=f"基础上下文统计失败：{type(exc).__name__}: {exc}"[:16000])

    async def approve(self, call: ToolCall, preview: str) -> ApprovalDecision:
        if self.stop.is_set() or self.cancel.is_set():
            return ApprovalDecision(False, "deny")
        approval_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        decisions = self.agent.approval.decisions_for(call)
        self.pending = (approval_id, future, decisions)
        self.send("status", self.active_id, phase="waiting_approval")
        self.send("approval_request", self.active_id, approval_id=approval_id,
                  call=asdict(call), preview=preview, decisions=decisions,
                  **approval_rule_context(self.agent, call))
        decision = "deny"
        try:
            decision = await future
            if self.stop.is_set() or self.cancel.is_set():
                decision = "deny"
            return ApprovalDecision(decision in {"once", "session", "workspace"}, decision)
        finally:
            self.pending = None
            self.send("approval_resolved", self.active_id,
                      approval_id=approval_id, decision=decision)
            if not self.cancel.is_set() and not self.stop.is_set():
                self.send("status", self.active_id, phase=self.active_phase)

    def stopping(self):
        if self.active is not None and not self.active.done():
            self.cancel.set()
            if self.pending is not None and not self.pending[1].done():
                self.pending[1].set_result("deny")
            self.send("status", self.active_id, phase="stopping")

    def restore(self):
        messages = self.session.load_messages() or self.agent.history
        for message in messages:
            payload = asdict(message)
            if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_FRAME_BYTES - 1024:
                payload = {"role": message.role, "content": message.content[:MAX_FRAME_BYTES // 8]
                           + "\n[显示已截断；完整内容保留在会话中]", "tool_calls": [
                               {"name": call.name[:128], "id": call.id,
                                "args": {"_display_truncated": "完整参数保留在会话中"}}
                               for call in message.tool_calls[:64]
                               if call.id is None or len(call.id) <= 128
                           ],
                           "name": (message.name or "")[:128] or None,
                           "tool_call_id": message.tool_call_id if message.tool_call_id is None
                           or len(message.tool_call_id) <= 128 else None,
                           "execution": getattr(message, "execution", None)}
                self.send("notice", message="历史消息过大，界面显示已截断；模型上下文快照保持完整。")
            self.send("history_message", message=payload)
        state = self.agent.state
        latest = self.session.latest_run()
        lines = [f"已恢复会话 {self.session.id}；历史记录 {len(messages)} 条；未自动续跑。"]
        if state is not None:
            lines.extend([f"任务：{state.goal}", "已完成：" + ("；".join(state.done) or "暂无已记录项"),
                          "待继续：" + ("；".join(state.open) or "请核对目标与实际结果")])
        if latest is not None:
            lines.append("上轮状态：" + str(latest.get("status", "interrupted"))
                         + "；停止原因：" + str(latest.get("stop_reason") or "未记录"))
        lines.append("结果未知的操作需要先核对；取消不代表撤销。输入 /resume 显式开启新一轮预算。")
        self.send("notice", message="\n".join(lines)[:16000])

    async def initialize(self):
        operation = asyncio.create_task(self.mcp.register_into(self.agent.tools, self.agent.approval))
        cancelled = asyncio.create_task(self.cancel.wait())
        try:
            finished, _ = await asyncio.wait({operation, cancelled}, return_when=asyncio.FIRST_COMPLETED)
            if cancelled in finished or self.stop.is_set():
                operation.cancel()
                self.send("notice", message="MCP 初始化已取消；未获批准的服务不会启动。")
            else:
                loaded = operation.result()
                self.send("notice", message=f"MCP 初始化完成：{len(loaded)} 个工具，{len(self.mcp.clients)} 个服务。")
                for error in self.mcp.errors:
                    self.send("notice", message=f"MCP: {error}"[:16000])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.send("notice", message=f"MCP 初始化失败：{type(exc).__name__}: {exc}"[:16000])
        finally:
            for task in (operation, cancelled):
                if not task.done():
                    task.cancel()
            await asyncio.gather(operation, cancelled, return_exceptions=True)
            if self.cancel.is_set() or self.stop.is_set():
                try:
                    await self.mcp.close()
                except Exception as exc:
                    self.send("notice", message=f"MCP 取消后关闭失败：{type(exc).__name__}: {exc}"[:16000])
            self.active_id = None
            self.active_phase = "running"
            if not self.stop.is_set():
                await self.publish_loaded_context()
                self.send("status", phase="idle")

    def review_plan(self, request_id: str, args: dict) -> None:
        action = args.get("action")
        if action not in ("review", "approve-execute", "approve", "dismiss"):
            raise ValueError("invalid plan review action")
        allowed = {"action", "target", "cursor"} if action == "review" else {"action", "target"}
        if set(args) - allowed or (action != "review" and "target" not in args):
            raise ValueError("invalid plan review action or fields")
        if "target" in args and not isinstance(args["target"], dict):
            raise ValueError("plan review target must be an object")
        if action == "review":
            self.send_review(request_id, self.plan_review.read_review(args.get("target"), args.get("cursor", 0)))
            return
        busy = self.active is not None and not self.active.done()
        decision = self.plan_review.decide_review(
            args["target"], "approve-only" if action == "approve" else action, request_id, busy=busy,
        )
        self.send_review(request_id, decision.receipt)
        if not busy and (decision.run_task is not None
                         or decision.receipt["outcome"] in {"approved", "approved_not_started"}):
            try:
                self.set_plan_mode(False, source="approval")
            except (OSError, ValueError) as exc:
                self.error(request_id, "plan_mode_error", str(exc))
        if decision.run_task is None:
            return
        run_task = decision.run_task
        self.active_id = request_id
        self.active_kind = "run"
        self.cancel = asyncio.Event()
        self.send("task_state", request_id, task=self.task_runtime.payload(run_task.record))
        self.send("status", request_id, phase="running")
        self.active = asyncio.create_task(self.session_operation(
            self.run_review(request_id, args["target"], run_task), self.session_writer))

    def revise_plan(self, request_id: str, args: dict) -> None:
        action = args.get("action")
        if action == "list-revisions":
            if not {'action', 'target'} <= set(args) or set(args) - {'action', 'target', 'cursor'}:
                raise ValueError('revision history requires target and optional cursor')
            self.send('plan_revision_history', request_id, **self.plan_revision.history(args['target'], args.get('cursor')))
            return
        legacy = action == "request-changes" and "target" not in args
        if legacy and set(args) == {"action", "feedback"}:
            snapshot = self.plan_review.read_review()
            if snapshot["code"]:
                self.error(request_id, snapshot["code"], snapshot["message"])
                return
            args = {**args, "target": snapshot["target"]}
        allowed = {"action", "target", "feedback"} | ({"expected_segment"} if action == "resume-revision" else set())
        if set(args) != allowed:
            raise ValueError("revision requires exact target, feedback and observed segment for resume")
        busy = self.active is not None and not self.active.done()
        if action == "query-revision":
            self.send("plan_revision", request_id, **self.plan_revision.read(args["target"], args["feedback"]))
            return
        if not self.agent.tools_enabled:
            self.error(request_id, "native_tools_disabled", "当前后端未启用计划提交工具，不能启动修订")
            return
        if action == "resume-revision":
            decision = self.plan_revision.resume(args["target"], args["feedback"], request_id,
                                                 expected_segment=args["expected_segment"], busy=busy)
        else:
            decision = self.plan_revision.request(args["target"], args["feedback"], request_id, busy=busy)
        self.send("plan_revision", request_id, **decision.receipt)
        if decision.run_task is None:
            if legacy:
                self.send("command_result", request_id, command="plan", text=decision.receipt["message"])
            return
        self.active_id = request_id
        self.active_kind = "run"
        self.cancel = asyncio.Event()
        self.send("task_state", request_id, task=self.task_runtime.payload(decision.run_task.record))
        self.send("status", request_id, phase="running")
        self.active = asyncio.create_task(self.session_operation(
            self.run_review(request_id, args["target"], decision.run_task, revision=True), self.session_writer))

    def send_review(self, request_id: str, receipt: dict) -> None:
        from app.runtime import plan as plan_artifacts

        actions = []
        summary = receipt["summary"]
        if (plan_artifacts.fcntl is not None and not (self.active and not self.active.done())
                and receipt["outcome"] in {"ready", "approved", "approved_not_started"}
                and summary is not None):
            approvable = summary["status"] in {"draft", "proposed"}
            if self.task_runtime is not None and (approvable or summary["executable"]):
                actions.append("approve-execute")
            if approvable:
                actions.append("approve-only")
            actions.append("dismiss")
        self.send("plan_review", request_id, **{**receipt, "available_actions": actions})

    def review_execution_plan(self, target: dict, run_task):
        from app.runtime.plan import can_execute, content_hash, load_plan_ref

        snapshot = self.plan_review.read_review(target)
        if snapshot["code"]:
            raise PlanError(snapshot["message"], code=snapshot["code"])
        if not snapshot["launch"] or snapshot["launch"]["task_id"] != run_task.task_id:
            raise PlanError("运行绑定无法核对", code="needs_reconciliation")
        task_dir = self.session.root / "tasks" / target["plan_task_id"]
        plan = load_plan_ref(task_dir, target["plan_id"], target["plan_version"])
        if content_hash(plan) != target["plan_hash"]:
            raise PlanError("计划内容已变化，请重新审阅", code="content_changed")
        if not can_execute(plan):
            raise PlanError("计划批准已失效，请重新审阅", code="plan_stale")
        return task_dir, plan

    async def run_review(self, request_id: str, target: dict, run_task, *, text=None, revision=False) -> None:
        previous_gate = self.agent.approval.execution_gate
        previous_tool_gate = self.agent.tool_gate
        previous_tools = self.agent.tools
        previous_names = set(previous_tools.names())
        try:
            if revision:
                context = self.plan_revision.context(run_task)
                await self.run(request_id, "按反馈修订原计划：" + context.feedback, run_task, revision_context=context)
            else:
                bound_plan = self.review_execution_plan(target, run_task)
                await self.run(request_id, text if text is not None else run_task.objective,
                               run_task, reviewed_plan=bound_plan)
        except Exception as exc:
            code = exc.code if isinstance(exc, PlanError) else "run_preparation_error"
            self.error(request_id, code, str(exc))
            result = AgentResult(f"运行未继续：{exc}", 0, "stopped", stop_reason=code)
            try:
                record = self.task_runtime.finish_run(run_task, result)
                self.send("task_state", request_id, task=self.task_runtime.payload(record))
            except Exception as cleanup_error:
                self.error(request_id, "task_runtime_error", str(cleanup_error))
            self.agent.approval.execution_gate = previous_gate
            self.agent.tool_gate = previous_tool_gate
            self.agent.tools = previous_tools
            for name in {"start_plan_step", "complete_plan_step", "submit_plan"} - previous_names:
                previous_tools.unregister(name)
            self.plan_context = None
            self.active_id = None
            self.active_kind = None
            self.send("done", request_id, result=asdict(result))
            if not self.stop.is_set():
                self.send("status", phase="idle")

    def _plan_command(self, action: str, args: dict | None = None) -> str:
        from app.runtime.plan import (
            PlanError, can_execute, content_hash, load_plan, render_plan, reject_plan,
        )
        if action in {"mode-on", "mode-off", "mode-status"}:
            return self.plan_mode_command(action)
        tasks_root = self.session.root / "tasks"
        if action == "list":
            rows = []
            for path in sorted(tasks_root.glob("*/plan/plan.v*.json")):
                try:
                    item = load_plan(path)
                except PlanError:
                    continue
                rows.append((item.version, path.stat().st_mtime, item))
            if not rows:
                return "当前会话没有计划产物。"
            rows.sort(key=lambda row: (row[1], row[0]))
            return "计划版本：\n" + "\n".join(
                f"- v{item.version} {item.status} {item.objective} ({item.plan_id})"
                for _, _, item in rows[-20:]
            )
        candidates = sorted(
            tasks_root.glob("*/plan/plan.v*.json"),
            key=lambda path: path.stat().st_mtime if path.exists() else 0,
            reverse=True,
        ) if tasks_root.is_dir() else []
        if not candidates:
            return "当前会话没有计划产物；用 /plan <任务> 生成。"
        if action == "prepare-background":
            from app.runtime.plan import executable_plans
            executable = executable_plans(self.session.root)
            if not executable:
                return "没有可执行的 approved 计划；先批准或修订计划。"
            path, plan = executable[0]
        else:
            path = candidates[0]
        try:
            plan = load_plan(path) if action != "prepare-background" else plan
            if action == "markdown":
                from app.runtime.plan_review import render_markdown
                return render_markdown(plan)
            if action == "dismiss":
                from app.runtime.plan_review import dismiss as dismiss_review
                dismiss_review(path.parent.parent, plan, by="user")
                return (f"已关闭计划审阅（{plan.plan_id} v{plan.version}）；"
                        f"计划保持 {plan.status}，批准动作未发生。")
            if action == "card":
                from app.runtime.plan import render_approval_card
                return render_approval_card(plan)
            if action == "prepare-background":
                if self.task_runtime is None or not getattr(self.task_runtime, "enabled", False):
                    return "任务运行时未启用，无法准备后台计划。"
                try:
                    task, reused = self.plan_review.prepare_background({
                        'session_id': self.session.id, 'plan_task_id': plan.task_id,
                        'plan_id': plan.plan_id, 'plan_version': plan.version, 'plan_hash': content_hash(plan),
                    })
                except Exception as exc:
                    return f"后台计划准备失败：{exc}"
                return (f"background task {'already prepared' if reused else 'prepared'}: task_id={task.task_id} "
                        f"plan_id={plan.plan_id} version={plan.version}")
            if action == "acceptance":
                lines = ["验收标准："]
                criteria = plan.acceptance_criteria
                if not criteria:
                    lines.append("- （无）")
                for item in criteria:
                    required = item.get("required", True) is not False
                    confirmed = item.get("confirmed") is True
                    lines.append(f"- {item.get('id', '?')} {item.get('text', '')} "
                                 f"[{'已确认' if confirmed else '候选'}] [{'required' if required else 'optional'}]")
                lines.append(f"可信验证命令（{len(plan.verification_plan)}）：")
                for item in plan.verification_plan:
                    lines.append(f"- {item.get('id', '?')} [{item.get('source', '?')}] {item.get('command', '')}")
                if not plan.verification_plan:
                    lines.append("- （无）")
                try:
                    from app.runtime.acceptance import load_acceptance_result
                    saved = load_acceptance_result(path.parent.parent, plan.version)
                except Exception:
                    saved = None
                if saved is not None:
                    lines.append(f"最近执行结果：status={saved.get('status')} codes={saved.get('codes')}")
                    for vid, item in (saved.get("results") or {}).items():
                        lines.append(f"- {vid}: ok={item.get('ok')} attempts={item.get('attempts', 1)}")
                return "\n".join(lines)
            if action == "diff":
                from app.runtime.plan import PlanError, plan_path
                va = int((args or {}).get("version_a", 0))
                vb = int((args or {}).get("version_b", 0))
                try:
                    old = load_plan(plan_path(path.parent.parent, va))
                    new = load_plan(plan_path(path.parent.parent, vb))
                except (PlanError, OSError) as exc:
                    return f"计划差异读取失败：{exc}"
                old_steps = {item.step_id: item for item in old.steps}
                new_steps = {item.step_id: item for item in new.steps}
                lines = [f"计划差异 v{va} → v{vb}"]
                if old.objective != new.objective:
                    lines.append(f"objective: {old.objective} → {new.objective}")
                if old.status != new.status:
                    lines.append(f"status: {old.status} → {new.status}")
                for step_id in sorted(set(old_steps) & set(new_steps)):
                    before, after = old_steps[step_id], new_steps[step_id]
                    if (before.title, before.kind, before.depends_on) != (after.title, after.kind, after.depends_on):
                        lines.append(f"step {step_id}: {before.title}/{before.kind} → {after.title}/{after.kind}")
                for step_id in sorted(set(new_steps) - set(old_steps)):
                    lines.append(f"+ step {step_id} {new_steps[step_id].title}")
                for step_id in sorted(set(old_steps) - set(new_steps)):
                    lines.append(f"- step {step_id} {old_steps[step_id].title}")
                if old.approval != new.approval:
                    lines.append("approval: changed")
                return "\n".join(lines)
            if action == "step":
                step_id = str((args or {}).get("step_id", "")).strip()
                step = next((item for item in plan.steps if item.step_id == step_id), None)
                if step is None:
                    return f"计划中没有步骤 {step_id}"
                text = (f"步骤 {step.step_id}：{step.title}\n"
                        f"kind={step.kind} risk={step.risk} executor={step.executor}\n"
                        f"depends_on={step.depends_on}\nfiles={step.files}\n"
                        f"acceptance={step.acceptance}\nverification={step.verification}")
                try:
                    from app.runtime.plan_execution import load_ledger
                    record = load_ledger(path.parent.parent, plan.version).steps.get(step_id)
                    if record is not None:
                        text += f"\nstatus={record.status} evidence={len(record.evidence)}"
                        for item in record.evidence[-5:]:
                            text += f"\n- {item.get('kind')} {item.get('ref')} ok={item.get('ok')}"
                except Exception:
                    pass
                return text
            if action == "approve":
                decision = self.plan_review.decide_review(
                    {"session_id": self.session.id, "plan_task_id": plan.task_id, "plan_id": plan.plan_id,
                     "plan_version": plan.version, "plan_hash": content_hash(plan)}, "approve-only", str(uuid.uuid4()),
                )
                if decision.receipt["code"]:
                    return "计划操作失败：" + decision.receipt["message"]
                plan = load_plan(path)
                if self.plan_mode.status()["active"]:
                    self.set_plan_mode(False, source="approval")
                try:
                    from app.runtime.notifications import notify
                    notify(self.agent.tools.root, "plan_approved", task_id=plan.task_id,
                           plan_id=plan.plan_id, version=plan.version)
                except Exception:
                    pass
            elif action == "reject":
                reject_plan(path.parent.parent, plan, rejected_by="user", reason="user rejected")
                try:
                    from app.runtime.notifications import notify
                    notify(self.agent.tools.root, "plan_rejected", task_id=plan.task_id,
                           plan_id=plan.plan_id, version=plan.version)
                except Exception:
                    pass
            elif action == "edit-step":
                from app.runtime.plan import revise_plan
                step_id = str((args or {}).get("step_id", "")).strip()
                title = (args or {}).get("title")
                kind = (args or {}).get("kind")
                files = (args or {}).get("files")
                depends_on = (args or {}).get("depends_on")
                steps = plan.to_dict().get("steps", [])
                if not any(str(item.get("step_id")) == step_id for item in steps):
                    return f"计划中没有步骤 {step_id}"
                from app.runtime.plan import find_dependency_cycle
                for item in steps:
                    if str(item.get("step_id")) == step_id:
                        if isinstance(title, str) and title.strip():
                            item["title"] = title.strip()
                        if isinstance(kind, str) and kind:
                            item["kind"] = kind
                        if isinstance(files, list) and files:
                            item["files"] = [str(path) for path in files]
                        if isinstance(depends_on, list):
                            item["depends_on"] = [str(step) for step in depends_on]
                step_ids = {str(item.get("step_id")) for item in steps}
                for item in steps:
                    if item.get("depends_on"):
                        for dependency in item["depends_on"]:
                            if dependency == item.get("step_id"):
                                return f"步骤 {item.get('step_id')} 不能依赖自身"
                            if dependency not in step_ids:
                                return f"步骤 {item.get('step_id')} 依赖不存在的步骤 {dependency}"
                cycle = find_dependency_cycle(steps)
                if cycle is not None:
                    return f"计划依赖存在循环：{' → '.join(cycle)}"
                try:
                    plan = revise_plan(path.parent.parent, plan, steps=steps, requester="user")
                except (PlanError, ValueError) as exc:
                    return f"计划步骤修订失败：{exc}"
            elif action == "revise":
                from app.runtime.plan import PlanError, revise_plan
                try:
                    plan = revise_plan(path.parent.parent, plan,
                                       objective=str((args or {}).get("objective", "")),
                                       requester="user")
                except (PlanError, ValueError) as exc:
                    return f"计划修订失败：{exc}"
            text = render_plan(plan) + f"\nexecutable={can_execute(plan)}"
            if action == "approve":
                text += "\n会话计划模式：关闭（批准后退出）。"
            try:
                from app.runtime.plan_execution import load_ledger
                ledger = load_ledger(path.parent.parent, plan.version)
                text += "\n执行台账："
                for step_id, record in ledger.steps.items():
                    text += f"\n- {step_id}: {record.status} ({len(record.evidence)} evidence)"
            except Exception:
                pass
            return text
        except (PlanError, OSError) as exc:
            return f"计划操作失败：{exc}"

    async def command(self, request_id, command, args=None):
        skills = None
        if command == "resume" and self.agent.history:
            try:
                latest = self.session.latest_run() or {}
                # 旧任务没有可靠的当前运行绑定，不能猜最新 task 或退回无约束续聊。
                if "task_id" not in latest and any((self.session.root / "tasks").glob("*/meta.json")):
                    raise TaskRuntimeError("旧会话缺少续做绑定，请核对任务后显式指定 task_id 继续")
                task_id = latest.get("task_id")
                run_task = None
                if task_id is not None:
                    if self.task_runtime is None or not self.task_runtime.enabled:
                        raise TaskRuntimeDisabled("续做原任务需要启用 VIBE_TASK_RUNTIME")
                    record = self.task_runtime.store.get(task_id)
                    meta = {"kind": record.kind, "task_id": record.task_id,
                            "objective": record.objective, "mode": record.mode}
                    if record.plan_id:
                        meta.update(plan_id=record.plan_id, plan_version=record.plan_version)
                    if record.goal_id:
                        meta["goal_id"] = record.goal_id
                    if record.mode == "plan" and "creation" in record.extra:
                        if not self.agent.tools_enabled:
                            raise TaskRuntimeError("当前后端未启用计划提交工具，不能续做修订")
                        creation = record.extra["creation"]
                        target = creation.get("target") if isinstance(creation, dict) else None
                        if not isinstance(target, dict) or target.get("operation") != "plan-revision":
                            raise TaskRuntimeError("修订来源凭据无法核对")
                        observed = next((s.segment for s in record.segments if s.run_id == latest.get("request_id")), None)
                        decision = self.plan_revision.resume(target.get("source"), target.get("feedback"), request_id,
                                                             expected_segment=observed)
                        self.send("plan_revision", request_id, **decision.receipt)
                        if decision.run_task is None:
                            raise TaskRuntimeError(decision.receipt["message"])
                        run_task = decision.run_task
                    else:
                        run_task = self.task_runtime.prepare_run(request_id, meta)
                    self.active_kind = "run"
                    self.send("task_state", request_id,
                              task=self.task_runtime.payload(run_task.record))
            except Exception as exc:
                self.error(request_id, "resume_unavailable", str(exc))
                self.send("done", request_id, result=asdict(AgentResult(
                    f"未续做：{exc}", 0, "stopped", stop_reason="resume_unavailable")))
                self.active_id = None
                self.active_kind = None
                self.send("status", phase="idle")
                return
            await self.run(request_id, RESUME_INSTRUCTION, run_task)
            return
        try:
            if command == "resume":
                self.send("done", request_id,
                          result=asdict(AgentResult("没有可继续的任务。", 0, "error")))
                return
            if command in {"permissions", "permissions_reset"}:
                if command == "permissions_reset":
                    self.agent.approval.clear(self.agent.tools.root)
                rules = self.agent.approval.permissions(self.agent.tools.root)
                text = ("已撤销当前工作区的本进程授权；授权档位和持久策略保持不变。\n"
                        if command.endswith("reset") else "")
                text += (f"workspace 授权：{'已开启' if rules['workspace_allowed'] else '未开启'}；"
                         f"session 精确规则：{rules['session_rules']} 条。重启后重新审批。")
                level = getattr(self.agent, "autonomy", "ask")
                text += f"\n授权档位：{level}（{permissions.AUTONOMY_LABELS.get(level, level)}）。"
                policy = getattr(self.agent, "permission_policy", None)
                if policy is not None:
                    summary = policy.summary()
                    text += (f"\n策略规则：user {summary['user_rules']} 条，"
                             f"project {summary['project_rules']} 条（project 只能收紧）。")
                    described = policy.describe_rules()
                    if described:
                        text += "\n策略规则明细：\n" + "\n".join(described[:20])
                        if len(described) > 20:
                            text += f"\n… 另有 {len(described) - 20} 条"
                    else:
                        text += "\n策略规则明细：（无）"
                error = getattr(self.agent, "permission_error", None)
                if error:
                    text += f"\n策略错误（保留上一份）：{error}"
            elif command == "cost":
                summary = self.session.cost_summary()
                text = (f"session {self.session.id} · runs {summary['runs']} · "
                        f"tokens {summary['total_tokens']:,} "
                        f"(in {summary['prompt_tokens']:,} / out {summary['completion_tokens']:,}) · "
                        f"cost ${summary['cost_usd']:.6f} · tools {summary['tool_calls']} · "
                        f"last {summary['last_status'] or '未运行'}")
                if summary.get("subagent_runs"):
                    text += (f" · subagent {summary['subagent_runs']} 次 "
                             f"{summary['subagent_tokens']:,} tokens "
                             f"${summary['subagent_cost_usd']:.6f}")
            elif command == "tools":
                text = "已注册工具：\n" + "\n".join(
                    f"- {spec['function']['name']}: {spec['function']['description']}"
                    for spec in self.agent.tools.specs()
                ) if self.agent.tools_enabled else "chat-only：工具已禁用。"
            elif command == "skills":
                text = (self.agent.manage_skills(**(args or {})) if self.agent.tools_enabled
                        else "skills 不可用：chat-only 模式已禁用工具。")
                if self.agent.tools_enabled:
                    skills = self.agent.tools.skill_entries()
            elif command == "mcp":
                if self.mcp is None or not self.mcp.servers:
                    text = "未配置 MCP 服务。"
                else:
                    def server_status(name):
                        client = self.mcp.clients.get(name)
                        if client is None:
                            return "未连接"
                        failure = getattr(client, "broken", None)
                        if failure or getattr(client, "client", True) is None:
                            return f"不可用：{failure or 'closed'}"
                        return "已连接"

                    text = ("MCP 服务：\n" + "\n".join(
                        f"- {name}: {server_status(name)}"
                        for name in self.mcp.servers
                    ) + f"\n工具：{len(self.mcp.tools)} 个")
                    if self.mcp.errors:
                        text += "\n" + "\n".join(self.mcp.errors)
            elif command == "evidence":
                text = format_round_evidence(self.session.latest_round())
            elif command == "plan":
                text = self._plan_command((args or {}).get("action", "show"), args or {})
            elif command == "notifications":
                from app.runtime.notifications import read_notifications
                items = read_notifications(self.agent.tools.root)
                kind = (args or {}).get("kind")
                if kind:
                    items = [item for item in items if item.get("kind") == kind]
                items = items[-20:]
                if not items:
                    text = "当前没有通知。"
                else:
                    text = f"最近通知（{len(items)}）：\n" + "\n".join(
                        f"- {item.get('ts', '')} {item.get('kind', '')} "
                        f"task={item.get('task_id', '-')} {item.get('status', '')}".rstrip()
                        for item in items
                    )
            elif command == "explore":
                from app.runtime.subagents import run_subagent
                prompt = str((args or {}).get("prompt", ""))
                self.send("notice", None, message=f"子 Agent 开始：{prompt[:120]}")
                sub = await run_subagent(
                    self.agent.provider, self.agent.tools.root,
                    prompt,
                    max_steps=_env_bounded("VIBE_EXPLORE_MAX_STEPS", 6, 1, 20, cast=int),
                    max_run_tokens=_env_bounded("VIBE_EXPLORE_MAX_TOKENS", 20_000, 500, 200_000, cast=int),
                    max_run_seconds=_env_bounded("VIBE_EXPLORE_MAX_SECONDS", 60.0, 5.0, 600.0),
                    session_root=self.session.root,
                )
                try:
                    usage = dict(sub.usage or {})
                    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                    completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
                    total_tokens = int(usage.get("total_tokens") or prompt_tokens + completion_tokens)
                    self.session.append_trace({
                        "type": "run_end", "kind": "subagent",
                        "ts": time.time(), "run_id": f"subagent:{sub.task_id or '-'}",
                        "status": sub.status, "stop_reason": sub.stop_reason,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                        "cost_usd": float(sub.cost_usd or 0.0),
                        "tool_calls": 0,
                    })
                except OSError:
                    pass
                self.send("notice", None,
                          message=f"子 Agent {sub.status}：{sub.task_id or '-'}")
                if sub.task_id:
                    self.send("subagent_event", None, task_id=sub.task_id,
                              status=sub.status, summary=sub.summary[:500])
                    try:
                        from app.runtime.notifications import notify
                        notify(self.agent.tools.root, "subagent_finished",
                               task_id=sub.task_id, status=sub.status)
                    except Exception:
                        pass
                text = f"子 Agent {sub.status}：{sub.summary}"
                if sub.task_id:
                    text += f"\n任务：{sub.task_id}"
                try:
                    sub_usage = dict(sub.usage or {})
                    sub_tokens = int(sub_usage.get("total_tokens")
                                     or int(sub_usage.get("prompt_tokens") or 0)
                                     + int(sub_usage.get("completion_tokens") or 0))
                    if sub_tokens or sub.cost_usd:
                        text += f"\n用量：{sub_tokens:,} tokens · ${float(sub.cost_usd or 0.0):.6f}"
                except (TypeError, ValueError):
                    pass
                if sub.error:
                    text += f"\n错误：{sub.error}"
            elif command == "queue":
                from app.runtime.tasks import TaskStore
                tasks = [task for task in TaskStore(self.session.root).list(status="created")
                         if task.plan_id]
                tasks.sort(key=lambda task: (-task.priority, task.created_at, task.task_id))
                if not tasks:
                    text = "当前没有待运行的 created 计划任务。"
                else:
                    lines = [f"计划队列（{len(tasks)}）："]
                    for index, task in enumerate(tasks, start=1):
                        bind = f"{task.plan_id}v{task.plan_version}" if task.plan_id else "-"
                        lines.append(
                            f"{index}. {task.task_id} · priority {task.priority} · {bind} · "
                            f"{task.mode} · {task.objective[:80]}"
                        )
                    text = "\n".join(lines)
            elif command == "priority":
                from app.runtime.tasks import TaskError, TaskStore
                task_id = str((args or {}).get("task_id") or "")
                priority_value = (args or {}).get("priority")
                try:
                    task = TaskStore(self.session.root).set_priority(task_id, priority_value)
                except (ValueError, TaskError) as exc:
                    text = f"调整失败：{exc}"
                else:
                    text = f"已调整 {task.task_id} 优先级为 {task.priority}"
            elif command == "trace":
                import time as _time

                from app.session.observability import render_trace_summary, summarize_trace

                hours = float(args["hours"]) if isinstance(args, dict) and args.get("hours") is not None else None
                since_ts = None
                if hours is not None:
                    since_ts = _time.time() - hours * 3600.0
                summary = summarize_trace(self.session.root, since_ts=since_ts)
                text = render_trace_summary(summary)
                if summary.get("events", 0) == 0 and since_ts is not None:
                    text += f"\n（最近 {hours:g} 小时没有事件）"
            elif command == "tasks":
                text = _format_task_hints(_task_hints(self.session.root))
            elif command == "approvals":
                text = _format_approvals(self.session.root)
            elif command == "autonomy":
                requested = (args or {}).get("level")
                if requested not in permissions.AUTONOMY_LEVELS:
                    raise ValueError(f"unknown autonomy level: {requested!r}")
                self.agent._cli_autonomy = requested
                self.agent._reload_permission_policy()
                effective = getattr(self.agent, "autonomy", "ask")
                label = permissions.AUTONOMY_LABELS.get(effective, effective)
                text = f"授权档位已切换：{effective}（{label}）"
                if effective != requested:
                    cap = getattr(self.agent.permission_policy, "autonomy_cap", None)
                    reason = f"项目上限为 {cap}" if cap else "项目策略限制"
                    text += f"\n请求档位 {requested} 未生效（{reason}），已按 {effective} 生效。"
            else:
                call = ToolCall("bash", {"command": "git diff --stat && git diff --color=never", "timeout": 10},
                                f"{request_id}:diff")
                preview = self.agent.tools.preview(call)
                try:
                    # 与模型发起的工具审批一致：预览尾部标注副作用层级。
                    preview += f"\n\n[审批层] {permissions.label(self.agent._tool_tier(call))}"
                except Exception:
                    pass
                decision = await self.agent.approval.request(
                    call, preview, self.agent.tools.root,
                )
                if self.cancel.is_set() or self.stop.is_set():
                    text = "diff 已取消；命令未执行。"
                elif not decision.allowed:
                    text = "diff 未获授权；命令未执行。"
                else:
                    ok, output = await self.agent.tools.execute_async(call, cancel=self.cancel)
                    text = output or ("no diff" if ok else "diff failed")
            payload = {"command": command, "text": str(text)[:100000]}
            if skills is not None:
                payload["skills"] = skills
            if command in {"permissions", "permissions_reset", "autonomy"}:
                payload["autonomy"] = getattr(self.agent, "autonomy", "ask")
            self.send("command_result", request_id, **payload)
        except asyncio.CancelledError:
            self.send("command_result", request_id, command=command, text="命令已取消；已开始操作的结果需要核对。")
        except Exception as exc:
            self.send("command_result", request_id, command=command,
                      text=f"命令失败：{type(exc).__name__}: {exc}"[:16000])
        finally:
            self.active_id = None
            self.active_kind = None
            if not self.stop.is_set():
                if command == "skills" and not self.has_started_run:
                    await self.publish_loaded_context()
                self.send("status", phase="idle")

    async def session_operation(self, operation, writer):
        try:
            return await operation
        finally:
            if writer is not None:
                writer.close()
                if self.session_writer is writer:
                    self.session_writer = None

    def refresh_session_context(self) -> None:
        revision = self.session.context_revision()
        if revision == self.session_revision:
            return
        history = self.session.load_context()
        state = self.session.load_state()
        changed = history != self.agent.history or (state is not None and state != self.agent.state)
        if (history or state is not None) and changed:
            self.agent.restore_context(history, state)
            if self.session_revision is not None:
                self.send('notice', None, message='已接入此会话的最新结果，继续前会重新核对文件。')
        self.session_revision = revision

    async def run(self, request_id: str, text: str, run_task=None, *, reviewed_plan=None, revision_context=None):
        self.refresh_session_context()
        if run_task is not None and "creation" in run_task.record.extra and reviewed_plan is None and revision_context is None:
            creation = run_task.record.extra["creation"]
            target = creation.get("target") if isinstance(creation, dict) else None
            revision = isinstance(target, dict) and target.get("operation") == "plan-revision"
            await self.run_review(request_id, target, run_task, text=text, revision=revision)
            return
        self.has_started_run = True
        self._start_inspection(request_id, text, run_task, revision_context)
        previous_tools_enabled = self.agent.tools_enabled
        previous_tools = self.agent.tools
        previous_stop_after_tools = getattr(self.agent, "stop_after_tools", frozenset())
        plan_tool_registered = False
        plan_context = None
        active_plan_step = {"step_id": None}
        plan_step_tools_registered = False
        profile = None
        plan_policy_scope = None
        if run_task is not None:
            from app.runtime.modes import ModeError, resolve_profile, validate_profile_tools
            profile = resolve_profile(run_task.record.mode)
            if profile.mode == "plan":
                from app.runtime.tasks import TaskStore
                self.agent.tools.register_plan_tool(
                    revision_context.task_dir if revision_context else TaskStore(self.session.root).task_dir(run_task.task_id),
                    on_submitted=lambda plan: self._inspect_plan(plan, 'revision-result' if revision_context else 'planning'),
                    **({"revision": revision_context.plan, "feedback": revision_context.feedback,
                        "revision_id": revision_context.revision_id} if revision_context else {}),
                )
                plan_tool_registered = True
                self.agent.stop_after_tools = frozenset({"submit_plan"})
                try:
                    if not self.plan_mode.status()["active"]:
                        self.plan_mode.set(True, running=False)
                    self.send_plan_mode()
                except Exception:
                    pass
            try:
                validate_profile_tools(profile, self.agent.tools.names())
            except ModeError as exc:
                self.error(request_id, "task_profile_error", str(exc))
                self.agent.tools_enabled = previous_tools_enabled
                self.agent.stop_after_tools = previous_stop_after_tools
                self.active_kind = None
                return
            try:
                self.task_runtime.store.append_event(
                    run_task.task_id, "mode_profile",
                    data={"mode": profile.mode, "profile": profile.mode,
                          "tools": profile.tools if profile.tools == "*" else list(profile.tools),
                          "system": profile.system},
                )
            except Exception:
                pass
            if run_task.record.mode == "chat":
                self.agent.tools_enabled = False
            elif isinstance(profile.tools, tuple):
                self.agent.tools = previous_tools.subset(profile.tools)
        previous_gate = getattr(self.agent.approval, "execution_gate", None) if self.agent.approval is not None else None
        previous_tool_gate = getattr(self.agent, "tool_gate", None)
        if run_task is not None and run_task.record.mode != "plan" and run_task.record.plan_id and self.agent.approval is not None:
            from app.runtime.plan import can_execute, find_plan
            found = reviewed_plan or find_plan(self.session.root, run_task.record.plan_id, run_task.record.plan_version)
            if found is None:
                try:
                    self.task_runtime.store.append_event(
                        run_task.task_id, "plan_binding",
                        data={"found": False, "plan_id": run_task.record.plan_id},
                    )
                    from app.runtime.notifications import notify
                    notify(self.config.workspace, "plan_binding", task_id=run_task.task_id,
                           found=False, plan_id=run_task.record.plan_id)
                except Exception:
                    pass
                self.agent.approval.execution_gate = lambda _call: "plan_not_approved"
            else:
                bound_task_dir, bound_plan = found
                self._inspect_plan(bound_plan, 'execution')
                if not can_execute(bound_plan):
                    reason = "plan_stale" if bound_plan.status == "approved" else "plan_not_approved"
                    try:
                        self.task_runtime.store.append_event(
                            run_task.task_id, "plan_binding",
                            data={"found": True, "executable": False, "reason": reason},
                        )
                        from app.runtime.notifications import notify
                        notify(self.config.workspace, "plan_binding", task_id=run_task.task_id,
                               found=True, executable=False, reason=reason)
                    except Exception:
                        pass
                    self.agent.approval.execution_gate = lambda _call: reason
                else:
                    self.agent.approval.execution_gate = None
                    from app.runtime.plan_execution import ExecutionError, StepLedger, load_ledger, save_ledger
                    try:
                        ledger = load_ledger(bound_task_dir, bound_plan.version)
                    except ExecutionError as exc:
                        if reviewed_plan is not None and (
                            not isinstance(exc.__cause__, FileNotFoundError) or len(run_task.record.segments) > 1
                        ):
                            raise
                        ledger = StepLedger.from_plan(bound_plan)
                        save_ledger(bound_task_dir, ledger)
                    plan_context = {"plan": bound_plan, "ledger": ledger, "task_dir": bound_task_dir}
                    self.plan_context = plan_context
                    from app.runtime.modes import READ_ONLY_TOOLS
                    edit_tools = {"write_file", "edit_file", "apply_patch"}
                    exec_tools = {"bash", "run_command"}

                    def plan_step_tool_gate(call) -> str | None:
                        if call.name in {"start_plan_step", "complete_plan_step"}:
                            return None
                        if call.name in READ_ONLY_TOOLS:
                            return None
                        current = active_plan_step["step_id"]
                        if current is None:
                            return "no active plan step; start a step before using non-read tools"
                        step = next((item for item in bound_plan.steps if item.step_id == current), None)
                        kind = step.kind if step is not None else "read"
                        if kind == "edit" and call.name in edit_tools:
                            if step is not None and step.files and call.name == "apply_patch":
                                return (f"plan_step_file_scope: step {current} declares files; "
                                        "apply_patch cannot be scoped, use write_file/edit_file")
                            if step is not None and step.files and call.name in {"write_file", "edit_file"}:
                                path = str(call.args.get("path") or "").replace("\\", "/").lstrip("./")
                                allowed = {str(item).replace("\\", "/").lstrip("./") for item in step.files}
                                if path not in allowed:
                                    return (f"plan_step_file_scope: step {current} only allows "
                                            f"{sorted(allowed)}; blocked {path!r}")
                            return None
                        if kind in {"run", "verify"} and call.name in exec_tools:
                            return None
                        return (f"plan_step_tool_blocked: step {current} kind={kind} "
                                f"does not allow tool {call.name}")

                    self.agent.tool_gate = plan_step_tool_gate
                    try:
                        self.task_runtime.store.append_event(
                            run_task.task_id, "plan_binding",
                            data={"found": True, "executable": True, "step_tools": True},
                        )
                        from app.runtime.notifications import notify
                        notify(self.config.workspace, "plan_binding", task_id=run_task.task_id,
                               found=True, executable=True, step_tools=True)
                    except Exception:
                        pass

                    def start_plan_step(step_id: str, **_ignored) -> str:
                        if not ledger.can_execute_step(bound_plan, step_id):
                            return f"ERROR step_blocked: {step_id} dependencies not done"
                        try:
                            if ledger.record(step_id).status != "in_progress":
                                ledger.start_step(step_id)
                        except ExecutionError as exc:
                            return f"ERROR {exc}"
                        active_plan_step["step_id"] = step_id
                        save_ledger(bound_task_dir, ledger)
                        return f"started {step_id}"

                    def complete_plan_step(step_id: str, evidence=None, evidence_refs=None,
                                            **_ignored) -> str:
                        refs: list[str] = []
                        for item in (evidence_refs if isinstance(evidence_refs, list) else []):
                            if isinstance(item, str) and item.strip():
                                refs.append(item.strip())
                        for item in (evidence if isinstance(evidence, list) else []):
                            if isinstance(item, str) and item.strip():
                                refs.append(item.strip())
                            elif isinstance(item, dict):
                                ref = (item.get("ref") or item.get("path") or item.get("command")
                                       or item.get("detail") or item.get("summary"))
                                if isinstance(ref, str) and ref.strip():
                                    refs.append(ref.strip())
                        for ref in refs[:10]:
                            try:
                                ledger.add_evidence(step_id, kind="manual", ref=ref[:500], ok=True)
                            except ExecutionError:
                                break
                        try:
                            record = ledger.complete_step(bound_plan, step_id)
                            if active_plan_step["step_id"] == step_id:
                                active_plan_step["step_id"] = None
                        except ExecutionError as exc:
                            hint = ""
                            if "step_evidence_missing" in str(exc):
                                hint = ("; pass evidence_refs (files/commands you used) or do the step "
                                        "work first (read_file/edit/command), then call "
                                        "complete_plan_step again")
                            return f"ERROR {exc}{hint}"
                        save_ledger(bound_task_dir, ledger)
                        if ledger.plan_complete(bound_plan):
                            self.agent.tool_gate = lambda _call: (
                                "all plan steps complete; stop calling tools and summarize"
                            )
                            return (f"step {step_id}: {record.status}; "
                                    "all plan steps complete — stop calling tools and summarize now")
                        pending = next((item.step_id for item in bound_plan.steps
                                        if ledger.record(item.step_id).status != "done"), None)
                        if pending:
                            return (f"step {step_id}: {record.status}; next step is {pending}; "
                                    f"start it directly with start_plan_step and do not re-read files")
                        return f"step {step_id}: {record.status}"

                    from app.tools.registry import ToolSpec
                    self.agent.tools.register(ToolSpec(
                        name="start_plan_step",
                        description="Start a step from the approved plan before doing its work.",
                        parameters={"type": "object", "properties": {
                            "step_id": {"type": "string"},
                            "evidence": {"type": "array"},
                            "evidence_refs": {"type": "array"},
                        }, "required": ["step_id"], "additionalProperties": False},
                        fn=start_plan_step,
                    ))
                    self.agent.tools.register(ToolSpec(
                        name="complete_plan_step",
                        description="Complete a plan step only after its required evidence exists.",
                        parameters={"type": "object", "properties": {
                            "step_id": {"type": "string"},
                            "evidence": {"type": "array"},
                            "evidence_refs": {"type": "array"},
                        }, "required": ["step_id"], "additionalProperties": False},
                        fn=complete_plan_step,
                    ))
                    plan_step_tools_registered = True
        if plan_context is not None:
            # 绑定计划时把步骤与协议显式注入本轮输入，避免只给 objective 时模型跳过 step 工具。
            plan = plan_context["plan"]
            step_lines = []
            for step in plan.steps:
                detail = f"kind={step.kind}"
                if step.files:
                    detail += f", files={','.join(step.files)}"
                step_lines.append(f"- {step.step_id}: {step.title} ({detail})")
            text = text + (
                "\n\n[已批准计划]\n"
                f"目标：{plan.objective}\n"
                "步骤：\n" + "\n".join(step_lines) + "\n"
                "执行要求：每一步先用 start_plan_step(step_id) 开始；完成该步工作后调用 "
                "complete_plan_step(step_id)；不要跳过步骤；全部步骤完成后停止调用工具并总结。"
            )
        result = AgentResult("cancelled", 0, "cancelled")
        completed = False
        metrics = {
            "used_tokens": 0, "limit_tokens": self.agent.max_run_tokens,
            "estimated": False, "context_tokens": None,
            "context_window": self.config.context_window,
            "done": 0, "planned": 0, "cost_usd": 0.0,
        }
        last_metrics = None
        executions: dict[int, dict] = {}
        terminal_trace = None
        trace_identity = {}
        observed_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        observed_tools = 0
        started = time.monotonic()
        start_files_changed = len(self.agent.state.files_changed) if self.agent.state is not None else 0

        def tool_result(call, ok, output):
            nonlocal observed_tools
            observed_tools += 1
            if plan_context is not None and active_plan_step["step_id"] and call.name not in {"start_plan_step", "complete_plan_step"}:
                step_id = active_plan_step["step_id"]
                kind = "read"
                if call.name in {"write_file", "edit_file", "apply_patch"}:
                    kind = "file_change"
                elif call.name == "bash":
                    kind = "command_ok"
                ref = str(call.args.get("path") or call.args.get("command") or call.name)
                try:
                    from app.runtime.plan_execution import save_ledger
                    plan_context["ledger"].add_evidence(step_id, kind=kind, ref=ref, ok=bool(ok))
                    save_ledger(plan_context["task_dir"], plan_context["ledger"])
                except Exception:
                    pass
            execution = executions.pop(id(call), None)
            self.send("tool_result", request_id, call=asdict(call), ok=ok, output=output,
                      **({"execution": execution} if execution is not None else {}))

        def publish_metrics(force=False):
            nonlocal last_metrics
            if force or metrics != last_metrics:
                self.send("metrics", request_id, **metrics)
                last_metrics = dict(metrics)

        def set_cost(value):
            if type(value) in (int, float) and math.isfinite(value):
                metrics["cost_usd"] = value

        def record_trace(event):
            nonlocal terminal_trace, observed_tools
            if event["type"] in {"run_start", "run_end"}:
                event = {**event, "task_id": run_task.task_id if run_task is not None else None,
                         "request_id": request_id}
            if run_task is not None and self.task_runtime is not None:
                self.task_runtime.record_trace(run_task, event)
            trace_identity.update({key: event[key] for key in ("run_id", "model") if key in event})
            if self.inspection_record is not None:
                self.inspection_record.trace(event.get('run_id'))
            if event["type"] == "context_snapshot":
                self.session.append_trace(event)
                self.send("context_snapshot", request_id, snapshot=snapshot_for_display(event))
                metrics["context_tokens"] = event["total_tokens"]
                publish_metrics()
                return
            if event["type"] == "llm_end":
                set_cost(metrics["cost_usd"] + event["cost_usd"])
                for key in observed_usage:
                    observed_usage[key] += event.get(key, 0)
                metrics["used_tokens"] = observed_usage["total_tokens"]
                metrics["estimated"] = metrics["estimated"] or event.get("usage_estimated", False)
            elif event["type"] == "budget_update":
                metrics.update(
                    used_tokens=event["used_tokens"], limit_tokens=event["limit_tokens"],
                    estimated=event["estimated"],
                )
                publish_metrics()
            elif event["type"] == "tool_end":
                # TraceRecorder 的生命周期钩子是后续行为 trace 的预留接口；
                # 工具计数以 on_result 回调为准，这里不再重复累加。
                pass
            elif event["type"] == "run_end":
                if "total_tokens" in event:
                    metrics["used_tokens"] = event["total_tokens"]
                    metrics["limit_tokens"] = event["limit_tokens"]
                    metrics["estimated"] = event["usage_estimated"]
                set_cost(event.get("cost_usd"))
                terminal_trace = dict(event)
                return
            elif event["type"] == "completion_rejected":
                # notice 必须 id=null（任务进行中的系统提示，不绑定请求）。
                self.send("notice", None, message=_completion_rejected_message(event))
            self.session.append_trace(event)

        def record_context(context):
            self.session.append_event("context", total_tokens=context.tokens, sections=context.sections)

        def record_state(state):
            self.session.save_state(state)
            metrics.update(done=len(state.done), planned=len(state.plan))
            publish_metrics()

        def persist_message(message):
            self.session.append_message(message)
            if message.name == "steering":
                # Ink 协议要求 notice 的 id 为 null；带 run id 会被判为协议错误并断连。
                self.send("notice", None, message=f"已应用纠偏：{message.content}")

        try:
            if profile is not None and profile.mode == "plan":
                plan_policy_scope = applied_plan_policy(self.agent, revision=revision_context)
                plan_policy_scope.__enter__()
            # 每次任务前重新读取，profile 的增删在下一次 run 生效。
            self.agent.set_project_background(load_project_background(self.config.workspace))
            user_profile = load_user_profile()
            # 测试/基准会话不注入个人偏好，避免污染对照与泄露用户画像。
            user_entries = (
                [entry for entry in user_profile.entries if entry.status == "confirmed"]
                if user_profile.enabled and self.session.purpose == "user" else []
            )
            selected_preferences = select_user_preferences(user_entries, text)
            self.agent.set_user_background(selected_preferences)
            try:
                self.session.append_trace({
                    "type": "user_profile_selection", "run_id": request_id,
                    "task": text[:500],
                    "confirmed": len(user_entries),
                    "selected": [
                        {"text": entry.text, "source": entry.source, "triggers": list(entry.triggers)}
                        for entry in selected_preferences
                    ],
                })
            except OSError:
                pass
            publish_metrics()
            repair_limit = _acceptance_repair_limit()
            repair_attempt = 0
            total_steps = 0
            total_cost = 0.0
            total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            prompt = text
            while True:
                if reviewed_plan is not None:
                    try:
                        self.review_execution_plan(run_task.record.extra["creation"]["target"], run_task)
                    except PlanError as exc:
                        self.error(request_id, exc.code, str(exc))
                        result = AgentResult(f"运行未继续：{exc}", 0, "stopped", stop_reason=exc.code)
                        break
                result = await self.agent.run(
                    prompt,
                    on_text=lambda delta: self.send("text_delta", request_id, delta=delta),
                    on_tool=lambda call: self.send("tool_call", request_id, call=asdict(call), **_tool_tier_payload(self.agent, call)),
                    on_result=tool_result,
                    on_execution=lambda call, execution: executions.__setitem__(id(call), execution),
                    on_context=record_context,
                    on_state=record_state,
                    on_message=persist_message,
                    on_history=self.session.save_context,
                    trace=record_trace,
                    cancel=self.cancel,
                )
                if revision_context is not None:
                    result = self._revision_result(run_task, result)
                completed = True
                total_steps += int(result.steps or 0)
                total_cost += float(result.cost_usd or 0.0)
                for key in total_usage:
                    total_usage[key] += int((result.usage or {}).get(key) or 0)
                if result.status != "done":
                    break
                if plan_context is not None:
                    result = await self._verify_bound_plan(plan_context, result)
                elif run_task is not None and run_task.record.kind == "goal":
                    result = await self._verify_goal_acceptance(run_task, result)
                if result.status == "done":
                    break
                repair_needed = (
                    plan_context is not None
                    and result.stop_reason.startswith("acceptance_")
                    and result.stop_reason != "acceptance_timeout"
                )
                if (not repair_needed or repair_attempt >= repair_limit
                        or self.cancel.is_set() or self.stop.is_set()):
                    break
                repair_attempt += 1
                self._enter_plan_repair_mode(plan_context)
                try:
                    self.send("notice", None,
                              message=f"计划验收未通过，自动修复第 {repair_attempt}/{repair_limit} 次")
                    from app.runtime.notifications import notify
                    notify(self.agent.tools.root, "plan_repair",
                           task_id=plan_context["plan"].task_id,
                           plan_id=plan_context["plan"].plan_id,
                           plan_version=plan_context["plan"].version,
                           attempt=repair_attempt, stop_reason=result.stop_reason)
                except Exception:
                    pass
                prompt = _acceptance_repair_prompt(plan_context, result)
            if repair_attempt:
                result = replace(
                    result, steps=total_steps,
                    usage={**dict(result.usage or {}), **total_usage},
                    cost_usd=total_cost,
                )
        except asyncio.CancelledError:
            result = AgentResult("cancelled", 0, "cancelled", dict(observed_usage), metrics["cost_usd"],
                                 trace_identity.get("run_id", ""))
        except Exception as exc:
            self.error(request_id, "session_error" if isinstance(exc, OSError) else "agent_error",
                       f"{type(exc).__name__}: {exc}")
            result = AgentResult(f"error: {type(exc).__name__}: {exc}", 0, "error", dict(observed_usage),
                                 metrics["cost_usd"], trace_identity.get("run_id", ""))
        finally:
            if plan_policy_scope is not None:
                try:
                    plan_policy_scope.__exit__(None, None, None)
                except Exception:
                    pass
            failures = []
            snapshots = [("context", self.session.save_context, self.agent.history)]
            if self.agent.state is not None:
                snapshots.append(("state", self.session.save_state, self.agent.state))
            for name, save, value in snapshots:
                try:
                    save(value)
                except OSError as exc:
                    failures.append(f"{name}: {exc}")
                    self.error(request_id, "session_error", f"{name} save failed: {exc}")
            if failures:
                result = replace(result, text="error: session save failed: " + "; ".join(failures),
                                 status="error", stop_reason="")
            if completed:
                metrics["used_tokens"] = result.usage.get(
                    "total_tokens", result.usage.get("prompt_tokens", 0) + result.usage.get("completion_tokens", 0),
                )
                set_cost(result.cost_usd)
            ending = terminal_trace or {
                "type": "run_end", "ts": time.time(), "run_id": result.run_id,
                "model": self.config.model, **trace_identity,
                "steps": result.steps, **observed_usage, "cost_usd": result.cost_usd,
                "tool_calls": observed_tools, "duration_ms": round((time.monotonic() - started) * 1000, 1),
                "limit_tokens": self.agent.max_run_tokens,
                "remaining_tokens": max(0, self.agent.max_run_tokens - metrics["used_tokens"]),
                "usage_estimated": metrics["estimated"],
            }
            ending.update(status=result.status, stop_reason=result.stop_reason,
                          task_id=run_task.task_id if run_task is not None else None, request_id=request_id)
            if result.status == "error":
                ending["error"] = result.text
            try:
                self.session.append_trace(ending)
            except OSError as exc:
                self.error(request_id, "session_error", f"trace save failed: {exc}")
                result = replace(result, text=f"error: trace save failed: {exc}", status="error", stop_reason="")
            if self.agent.state is not None:
                metrics.update(done=len(self.agent.state.done), planned=len(self.agent.state.plan))
            if run_task is not None and self.task_runtime is not None:
                try:
                    record = self.task_runtime.finish_run(run_task, result)
                    if record is not None:
                        self.send("task_state", request_id,
                                  task=self.task_runtime.payload(record))
                except Exception as exc:
                    self.error(request_id, "task_runtime_error",
                               f"{type(exc).__name__}: {exc}")
            try:
                self.plan_mode.commit_pending()
            except Exception:
                pass
            self.send_plan_mode()
            if result.stop_reason == "plan_submitted":
                self.send("notice", None,
                          message="计划已提交（proposed）；批准后退出计划模式。"
                                  "用 /plan card 或 /plan markdown 查看。")
            if plan_context is not None:
                result = self._plan_result(plan_context, result, start_files_changed)
            publish_metrics(force=True)
            if revision_context is not None:
                binding = run_task.record.extra["creation"]["target"]
                self.send("plan_revision", request_id, **self.plan_revision.read(binding["source"], binding["feedback"]))
            self.session_revision = self.session.context_revision()
            self.send("done", request_id, result=asdict(result))
            self.active_id = None
            self.agent.tools_enabled = previous_tools_enabled
            self.agent.tools = previous_tools
            self.agent.tool_gate = previous_tool_gate
            self.agent.stop_after_tools = previous_stop_after_tools
            self.plan_context = None
            self.active_kind = None
            if self.agent.approval is not None:
                self.agent.approval.execution_gate = previous_gate
            if plan_tool_registered:
                previous_tools.unregister("submit_plan")
            if plan_step_tools_registered:
                previous_tools.unregister("start_plan_step")
                previous_tools.unregister("complete_plan_step")
            if not self.stop.is_set():
                self.send("status", phase="idle")

    def _revision_result(self, run_task, result):
        binding = run_task.record.extra["creation"]["target"]
        receipt = self.plan_revision.read(binding["source"], binding["feedback"])
        submitted = receipt["outcome"] == "submitted"
        if result.status == "done" and not submitted:
            code = receipt["code"] or "revision_not_submitted"
            result = replace(result, status="stopped", stop_reason=code)
        message = (f"已提交 v{receipt['result_target']['plan_version']}，等待重新审阅"
                   if submitted else f"未提交新计划；{result.stop_reason or result.status}，反馈已保留")
        result = replace(result, verified=None if submitted else False,
                         receipt="[计划修订] " + message,
                         completion={"allowed": submitted, "codes": [] if submitted else [result.stop_reason or result.status],
                                     "messages": [message]})
        latest = self.session.latest_round()
        if latest and result.run_id and latest.get("run_id") == result.run_id:
            self.session.update_latest_round({key: getattr(result, key) for key in
                                             ("status", "stop_reason", "verified", "completion", "receipt")})
        return result

    def _plan_result(self, context, result, start_files_changed):
        """计划的最终回执在 harness 验收后生成，不能沿用 Agent 的提前结论。"""
        from core.task import CompletionDecision

        verification = context.get("verification")
        verified = verification["status"] if verification is not None and result.status == "done" else (
            False if result.status != "done" else None)
        decision = CompletionDecision(
            result.status == "done" and verified is not False,
            (result.stop_reason,) if result.status != "done" else (),
            (f"计划未完成：{result.stop_reason or result.status}",) if result.status != "done" else (),
        )
        files = self.agent.state.files_changed[start_files_changed:] if self.agent.state is not None else []
        label = "已验证" if verified is True else "未通过" if verified is False else "待确认（未配置可确认的计划验收）"
        lines = [f"[运行结束] status={result.status} steps={result.steps} tokens={result.usage.get('total_tokens', 0)}",
                 f"[计划验收] {label}" + (f" · {result.stop_reason}" if result.stop_reason else ""),
                 "步骤：" + "；".join(f"{sid}={step.status}" for sid, step in context["ledger"].steps.items()),
                 "本轮改动（工具记录）：" + (", ".join(files) or "无（未记录文件修改）")]
        if verification is None:
            lines.append("实际计划验收：未运行验收命令")
        else:
            commands = {item.id: item.command for item in context["plan"].acceptance().verification_plan}
            for vid, item in verification["results"].items():
                outcome = f"exit={item['exit_code']}" if "exit_code" in item else f"未执行：{item.get('reason', 'unknown')}"
                if item.get("timed_out"):
                    outcome += " · timed_out"
                lines.append(f"实际计划验收：{vid} {commands.get(vid, '?')[:500]} [{outcome}]")
        result = replace(result, receipt="\n".join(lines), verified=verified, completion=decision.to_dict())
        try:
            latest = self.session.latest_round()
            if latest and result.run_id and latest.get("run_id") == result.run_id:
                self.session.update_latest_round({key: getattr(result, key) for key in
                                                  ("status", "stop_reason", "verified", "completion", "receipt")})
        except OSError:
            pass
        return result

    def _record_acceptance_round(self, verification, *, plan_id=None, plan_version=None,
                                 goal_id=None) -> None:
        """把验收三态写进最近一轮 round record，供 /evidence 查看。"""
        try:
            commands = []
            for vid, item in (verification.get("results") or {}).items():
                if not isinstance(item, dict):
                    continue
                commands.append({
                    "id": vid, "ok": bool(item.get("ok")),
                    "attempts": item.get("attempts", 1),
                    "reason": item.get("reason") or item.get("error"),
                })
            self.session.update_latest_round({
                "acceptance": {
                    "status": verification.get("status"),
                    "codes": list(verification.get("codes") or []),
                    "plan_id": plan_id,
                    "plan_version": plan_version,
                    "goal_id": goal_id,
                    "commands": commands[:20],
                },
            })
        except Exception:
            pass

    async def _run_trusted_acceptance(self, acceptance):
        """执行可信验收命令；超时返回 None，否则返回三态结果。"""
        from app.runtime.verification import run_verification_plan
        from app.safety.sandbox import CommandCancelled

        request_id = self.active_id
        try:
            retries = int(os.getenv("VIBE_ACCEPTANCE_RETRIES", "0"))
        except ValueError:
            retries = 0
        try:
            timeout = float(os.getenv("VIBE_ACCEPTANCE_TIMEOUT_SECONDS", "300"))
        except ValueError:
            timeout = 300.0
        try:
            total_timeout = float(os.getenv("VIBE_ACCEPTANCE_TOTAL_TIMEOUT_SECONDS", "0"))
        except ValueError:
            total_timeout = 0.0
        if total_timeout <= 0:
            total_timeout = timeout * (max(0, min(retries, 3)) + 1) + 30.0
        try:
            verification = await asyncio.wait_for(
                run_verification_plan(
                    acceptance, self.agent.tools.root, self.agent.approval,
                    retries=max(0, min(retries, 3)),
                    timeout=max(1.0, min(timeout, 3600.0)),
                ),
                timeout=max(1.0, min(total_timeout, 7200.0)),
            )
        except (asyncio.TimeoutError, CommandCancelled):
            return None
        commands = {item.id: item.command for item in acceptance.verification_plan}
        for command_id, item in verification["results"].items():
            code = item.get("exit_code")
            status = ("timed_out" if item.get("timed_out") else
                      "succeeded" if type(code) is int and code == 0 else
                      "failed" if type(code) is int else "unknown")
            self.send("tool_result", request_id,
                      call={"id": f"acceptance-{uuid.uuid4().hex}", "name": "bash",
                            "args": {"command": commands[command_id], "cwd": ".", "verification": True}},
                      ok=item.get("ok") is True, output=str(item.get("output") or item.get("reason") or ""),
                      execution={"status": status, "exit_code": code if type(code) is int else None})
        return verification

    @staticmethod
    def _acceptance_failure_text(acceptance, verification, label: str) -> str:
        codes = ",".join(verification["codes"]) or "acceptance_failed"
        commands = {item.id: item.command for item in acceptance.verification_plan}
        details = []
        for vid, item in verification["results"].items():
            if item.get("ok"):
                continue
            reason = item.get("reason") or f"exit={item.get('exit_code')}"
            output = str(item.get("output") or "").strip()[-500:]
            details.append(f"- {vid}: {commands.get(vid, '?')} → {reason}"
                           + (f"\n  {output}" if output else ""))
        suffix = f"\n[{label}未通过：" + codes + "]"
        if details:
            suffix += "\n[失败命令]\n" + "\n".join(details[:5])
        return suffix

    async def _verify_goal_acceptance(self, run_task, result) -> AgentResult:
        """goal 完成门：已确认 criteria 的可信验收命令通过才视为 verified。"""
        from app.runtime.goals import GoalStore

        try:
            record = GoalStore(self.session.root).load()
        except Exception:
            return result
        if record is None or record.task_id != run_task.task_id:
            return result
        try:
            acceptance = record.acceptance()
        except Exception:
            return result
        if acceptance is None:
            return result
        verification = await self._run_trusted_acceptance(acceptance)
        if verification is None:
            return replace(
                result, status="stopped", stop_reason="acceptance_timeout",
                text=(result.text + "\n[目标验收超时]").strip(),
            )
        from app.runtime.tasks import TaskStore

        task_dir = TaskStore(self.session.root).task_dir(run_task.task_id)
        try:
            from app.runtime.acceptance import save_acceptance_result
            save_acceptance_result(task_dir, 1, verification)
        except Exception:
            pass
        self._record_acceptance_round(
            verification, goal_id=record.goal_id,
        )
        try:
            from app.runtime.notifications import notify
            notify(self.agent.tools.root, "goal_acceptance",
                   task_id=run_task.task_id, goal_id=record.goal_id,
                   status=verification["status"], codes=verification["codes"])
        except Exception:
            pass
        if verification["status"] is True:
            return replace(result, verified=True)
        if verification["status"] is not True:
            codes = ",".join(verification["codes"]) or "acceptance_failed"
            suffix = self._acceptance_failure_text(acceptance, verification, "目标验收")
            return replace(
                result, status="stopped", stop_reason=f"acceptance_{codes}",
                text=(result.text + suffix).strip(),
            )
        return result

    async def _verify_bound_plan(self, plan_context, result) -> AgentResult:
        """绑定计划的完成门：required 步骤必须 done，然后跑可信验收命令。"""
        from app.runtime.plan_execution import load_ledger

        try:
            ledger = load_ledger(plan_context["task_dir"], plan_context["plan"].version)
            if not ledger.plan_complete(plan_context["plan"]):
                return replace(
                    result, status="stopped", stop_reason="plan_incomplete",
                    text=(result.text + "\n[计划未完成：required 步骤仍有未 done 项]").strip(),
                )
            acceptance = plan_context["plan"].acceptance()
            if acceptance is None:
                return result
            verification = await self._run_trusted_acceptance(acceptance)
            plan_context["verification"] = verification
            if verification is None:
                return replace(
                    result, status="stopped", stop_reason="acceptance_timeout",
                    text=(result.text + "\n[计划验收超时]").strip(),
                )
            if verification is not None:
                try:
                    from app.runtime.acceptance import save_acceptance_result
                    save_acceptance_result(
                        plan_context["task_dir"], plan_context["plan"].version, verification,
                    )
                except Exception:
                    pass
                self._record_acceptance_round(
                    verification,
                    plan_id=plan_context["plan"].plan_id,
                    plan_version=plan_context["plan"].version,
                )
                try:
                    from app.runtime.notifications import notify
                    notify(
                        self.agent.tools.root, "plan_acceptance",
                        task_id=plan_context["plan"].task_id,
                        plan_id=plan_context["plan"].plan_id,
                        plan_version=plan_context["plan"].version,
                        status=verification["status"],
                        codes=verification["codes"],
                    )
                except Exception:
                    pass
            if verification is not None and verification["status"] is True:
                # 计划验收由 harness 执行可信命令，强于模型自证：视为已验证完成。
                result = replace(result, verified=True)
            if verification is not None and verification["status"] is not True:
                codes = ",".join(verification["codes"]) or "acceptance_failed"
                suffix = self._acceptance_failure_text(acceptance, verification, "计划验收")
                result = replace(
                    result, status="stopped", stop_reason=f"acceptance_{codes}",
                    text=(result.text + suffix).strip(),
                )
            return result
        except Exception as exc:
            return replace(result, status="stopped", verified=False, stop_reason="plan_verification_error",
                           text=(result.text + f"\n[计划验收无法核对：{type(exc).__name__}: {exc}]").strip())

    def _enter_plan_repair_mode(self, plan_context) -> None:
        """验收失败后放开计划完成门：允许按计划范围读取/修改，必要时执行命令。"""
        from app.runtime.modes import READ_ONLY_TOOLS

        plan = plan_context["plan"]
        edit_steps = [step for step in plan.steps if step.kind == "edit"]
        scoped = any(step.files for step in edit_steps)
        allowed = {str(item).replace("\\", "/").lstrip("./")
                   for step in edit_steps for item in step.files}
        allow_exec = any(step.kind in {"run", "verify"} for step in plan.steps)
        edit_tools = {"write_file", "edit_file", "apply_patch"}

        def repair_gate(call) -> str | None:
            if call.name in READ_ONLY_TOOLS:
                return None
            if call.name in edit_tools:
                if scoped and call.name == "apply_patch":
                    return ("plan_repair_file_scope: repair cannot scope apply_patch; "
                            "use write_file/edit_file")
                if scoped and call.name in {"write_file", "edit_file"}:
                    path = str(call.args.get("path") or "").replace("\\", "/").lstrip("./")
                    if path not in allowed:
                        return (f"plan_repair_file_scope: repair only allows {sorted(allowed)}; "
                                f"blocked {path!r}")
                return None
            if allow_exec and call.name in {"bash", "run_command"}:
                return None
            extra = "/exec " if allow_exec else ""
            return (f"plan_repair_tool_blocked: acceptance repair only allows read/edit{extra}tools; "
                    f"blocked {call.name}")

        self.agent.tool_gate = repair_gate

    def _mark_plan_stale_for_steer(self) -> None:
        context = self.plan_context
        if not context:
            return
        from app.runtime.plan import mark_plan_stale
        plan = context["plan"]
        try:
            mark_plan_stale(context["task_dir"], plan)
            TaskStore = __import__("app.runtime.tasks", fromlist=["TaskStore"]).TaskStore
            TaskStore(self.session.root).append_event(
                plan.task_id, "plan_stale", data={"reason": "steering", "plan_id": plan.plan_id},
            )
        except Exception:
            pass
        plan.approval = {**plan.approval, "stale": True}
        if self.agent.approval is not None:
            self.agent.approval.execution_gate = lambda _call: "plan_stale"

    def accept(self, frame):
        if frame is _OVERSIZED:
            self.error(None, "frame_too_large", f"input exceeds {MAX_FRAME_BYTES} bytes")
            return
        request_id = None
        writer = None
        try:
            if isinstance(frame, (str, bytes)) and len(
                frame.encode("utf-8") if isinstance(frame, str) else frame
            ) > MAX_FRAME_BYTES:
                self.error(None, "frame_too_large", f"input exceeds {MAX_FRAME_BYTES} bytes")
                return
            if isinstance(frame, bytes):
                frame = frame.decode("utf-8")
            request = json.loads(frame)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
            raw_id = request.get("id")
            if isinstance(raw_id, str) and 0 < len(raw_id) <= 128 and raw_id.strip():
                request_id = raw_id
            if type(request.get("v")) is not int or request["v"] != 1 or request_id is None:
                raise ValueError("expected v=1 and a nonempty id of at most 128 characters")
            if request_id in self.seen:
                self.error(request_id, "duplicate_id", "request id was already used")
                return
            self.seen.add(request_id)
            kind = request.get("type")
            fields = {
                "run": {"text"}, "cancel": {"target"},
                "steer": {"target", "text"},
                "command": {"command"},
                "approval": {"approval_id", "decision"}, "shutdown": set(),
            }
            if not isinstance(kind, str) or kind not in fields:
                raise ValueError("unknown request type")
            if kind == "command" and request.get("command") in {"skills", "autonomy", "plan", "explore", "notifications", "trace", "priority", "inspections", "tasks", "sessions", "permissions", "search", "rounds"} and "args" in request:
                fields[kind] = fields[kind] | {"args"}
            allowed = {"v", "type", "id"} | fields[kind]
            if kind == "run" and "task" in request:
                allowed.add("task")
            if set(request) != allowed:
                raise ValueError("unexpected or missing request fields")
            task_meta = request.get("task") if kind == "run" else None
            if task_meta is not None and not isinstance(task_meta, dict):
                raise ValueError("run.task must be an object")
            if kind in {"run", "command"}:
                if kind == "run" and (not isinstance(request["text"], str) or not request["text"].strip()):
                    raise ValueError("run text must be a nonempty string")
                if kind == "command" and (not isinstance(request["command"], str) or request["command"] not in COMMANDS):
                    raise ValueError("unknown command")
                args = request.get('args')
                starts_run = (kind == 'run' or request.get('command') == 'resume'
                              or (request.get('command') == 'plan' and isinstance(args, dict)
                                  and args.get('action') in {'approve-execute', 'request-changes', 'resume-revision'}))
                if starts_run and not (self.active is not None and not self.active.done()):
                    writer = SessionWriter(self.session.root)
                    writer.__enter__()
                    self.session_writer = writer
                    from app.runtime.supervise import WorkerSupervisor
                    if os.getenv('VIBE_WORKER') != '1' and WorkerSupervisor(self.session.root).recovery_blocked():
                        raise SessionBusy('此会话有活动或无法核对的后台任务；请从任务入口接入或核对状态后重试。')
                    self.session.repair_incomplete_tool_calls()
                    self.refresh_session_context()
                if kind == 'command' and request['command'] == 'sessions':
                    from app.runtime.session_navigation import session_catalog
                    try:
                        page = session_catalog(self.session, self.config.workspace, request.get('args', {}))
                    except OSError as exc:
                        self.error(request_id, 'session_catalog_unavailable', f'会话目录暂不可读：{exc}'[:1000])
                        return
                    self.send('session_catalog', request_id, **page)
                    return
                if kind == "command" and request["command"] == "permissions" and "args" in request:
                    from app.runtime.permission_view import (
                        PermissionGrantNotFound, PermissionRuleNotRevocable, PermissionRuleNotFound,
                        permission_overview, revoke_grant, revoke_rule,
                    )
                    args = request["args"]
                    if not isinstance(args, dict):
                        raise ValueError("permissions args must be an object")
                    action = args.get("action")
                    if action == "list" and set(args) == {"action"}:
                        overview = permission_overview(self.agent, self.config.workspace)
                    elif action == "revoke" and set(args) == {"action", "grant_id"}:
                        grant_id = args.get("grant_id")
                        if not isinstance(grant_id, str) or not 1 <= len(grant_id) <= 128:
                            raise ValueError("permissions revoke requires a grant_id of 1-128 characters")
                        try:
                            overview = revoke_grant(self.agent, self.config.workspace, grant_id)
                        except PermissionGrantNotFound as exc:
                            self.error(request_id, "permission_grant_not_found", str(exc)[:1000])
                            return
                    elif action == "revoke-rule" and set(args) == {"action", "rule_id"}:
                        rule_id = args.get("rule_id")
                        if not isinstance(rule_id, str) or not 1 <= len(rule_id) <= 128:
                            raise ValueError("permissions revoke-rule requires a rule_id of 1-128 characters")
                        try:
                            overview = revoke_rule(self.agent, self.config.workspace, rule_id)
                        except PermissionRuleNotFound as exc:
                            self.error(request_id, "permission_rule_not_found", str(exc)[:1000])
                            return
                        except PermissionRuleNotRevocable as exc:
                            self.error(request_id, "permission_rule_not_revocable", str(exc)[:1000])
                            return
                    else:
                        raise ValueError("permissions accepts action=list | revoke(grant_id) | revoke-rule(rule_id)")
                    self.send("permission_overview", request_id, **overview,
                              workspace=str(self.config.workspace.resolve()),
                              session_id=self.session.root.name)
                    return
                if kind == "command" and request["command"] == "search":
                    from app.runtime.history_search import history_search
                    if "args" not in request:
                        raise ValueError("search requires args")
                    page = history_search(self.session, self.config.workspace, request["args"])
                    self.send("history_search", request_id, **page)
                    return
                if kind == "command" and request["command"] == "rounds":
                    from app.runtime.round_ledger import session_rounds
                    args = request.get("args", {})
                    if not isinstance(args, dict) or set(args):
                        raise ValueError("rounds accepts no args")
                    view = session_rounds(self.session, workspace=self.config.workspace)
                    self.send("rounds_ledger", request_id, **view)
                    return
                if kind == "command" and request["command"] == "tasks" and "args" in request:
                    from app.runtime.task_navigation import task_catalog
                    try:
                        page = task_catalog(self.session.root, self.config.workspace, request['args'])
                    except OSError as exc:
                        self.error(request_id, 'task_catalog_unavailable', f'任务目录暂不可读：{exc}'[:1000])
                        return
                    self.send('task_catalog', request_id, **page)
                    return
                if kind == "command" and request["command"] == "inspections":
                    from app.runtime.inspection import InspectionStore
                    args = request.get('args', {})
                    if not isinstance(args, dict) or set(args) - {'cursor', 'task_id'} or any(not isinstance(value, str) for value in args.values()):
                        raise ValueError('inspections accepts only cursor and task_id')
                    inspection_store = InspectionStore(self.session.root, self.config.workspace)
                    try:
                        page = inspection_store.page(before=args.get('cursor'), task_id=args.get('task_id'), limit=3)
                    except OSError as exc:
                        self.error(request_id, 'inspection_unavailable', f'历史记录暂不可读：{exc}'[:1000])
                        return
                    self.send('inspection_history', request_id, workspace=inspection_store.workspace,
                              session_id=self.session.root.name,
                              **({'task_id': args['task_id']} if 'task_id' in args else {}), **page)
                    return
                if kind == "command" and request["command"] in {"skills", "plan", "trace", "notifications", "explore", "priority"} and "args" in request:
                    args = request["args"]
                    if not isinstance(args, dict):
                        raise ValueError("command args must be an object")
                    if request["command"] == "plan":
                        if args.get("action") in {"request-changes", "query-revision", "resume-revision", "list-revisions"}:
                            self.revise_plan(request_id, args)
                            return
                        if args.get("action") in ("review", "approve-execute") or "target" in args:
                            self.review_plan(request_id, args)
                            return
                        if set(args) - {"action", "objective", "feedback", "step_id", "title", "kind", "files", "depends_on", "version_a", "version_b"} or args.get("action", "show") not in {"show", "list", "step", "edit-step", "diff", "acceptance", "card", "approve", "reject", "revise", "request-changes", "dismiss", "markdown", "mode-on", "mode-off", "mode-status", "prepare-background"}:
                            raise ValueError("plan args allowed: action=show|list|step|edit-step|diff|acceptance|card|approve|reject|revise|request-changes|dismiss|markdown|mode-on|mode-off|mode-status|prepare-background")
                        if args.get("action") == "request-changes" and (not isinstance(args.get("feedback"), str) or not args["feedback"].strip()):
                            raise ValueError("plan request-changes requires nonempty feedback")
                        if args.get("action") in {"dismiss", "markdown", "mode-on", "mode-off", "mode-status"} and set(args) != {"action"}:
                            raise ValueError(f"plan {args.get('action')} accepts only action")
                        if args.get("action") == "revise" and (not isinstance(args.get("objective"), str) or not args["objective"].strip()):
                            raise ValueError("plan revise requires a nonempty objective")
                        if args.get("action") == "step" and (not isinstance(args.get("step_id"), str) or not args["step_id"].strip()):
                            raise ValueError("plan step requires a nonempty step_id")
                        if args.get("action") == "edit-step":
                            if not isinstance(args.get("step_id"), str) or not args["step_id"].strip():
                                raise ValueError("plan edit-step requires a nonempty step_id")
                            has_title = args.get("title") is not None
                            has_kind = args.get("kind") is not None
                            has_files = args.get("files") is not None
                            has_depends = args.get("depends_on") is not None
                            if has_title and (not isinstance(args.get("title"), str) or not args["title"].strip()):
                                raise ValueError("plan edit-step title must be a nonempty string")
                            if has_kind and args.get("kind") not in {"read", "edit", "run", "verify"}:
                                raise ValueError("plan edit-step kind must be read|edit|run|verify")
                            if has_depends:
                                depends_value = args.get("depends_on")
                                if (not isinstance(depends_value, list) or len(depends_value) > 20
                                        or any(not isinstance(item, str) or not item.strip() for item in depends_value)):
                                    raise ValueError("plan edit-step depends_on must be a list of step ids")
                            if has_files:
                                files_value = args.get("files")
                                if (not isinstance(files_value, list) or not files_value
                                        or len(files_value) > 20
                                        or any(not isinstance(item, str) or not item.strip() for item in files_value)):
                                    raise ValueError("plan edit-step files must be a nonempty list of paths")
                            if not has_title and not has_kind and not has_files and not has_depends:
                                raise ValueError("plan edit-step requires title, kind, files and/or depends_on")
                        if args.get("action") == "diff":
                            for key in ("version_a", "version_b"):
                                if not isinstance(args.get(key), int) or isinstance(args.get(key), bool) or args[key] < 1:
                                    raise ValueError(f"plan diff requires positive integer {key}")
                    elif request["command"] == "notifications":
                        if set(args) - {"kind"} or ("kind" in args and (not isinstance(args.get("kind"), str) or not args["kind"].strip())):
                            raise ValueError("notifications args must contain only a nonempty kind")
                    elif request["command"] == "explore":
                        if set(args) != {"prompt"} or not isinstance(args.get("prompt"), str) or not args["prompt"].strip():
                            raise ValueError("explore args must contain only a nonempty prompt")
                    elif request["command"] == "priority":
                        if set(args) != {"task_id", "priority"}:
                            raise ValueError("priority args must contain exactly task_id and priority")
                        if not isinstance(args.get("task_id"), str) or not args["task_id"].strip():
                            raise ValueError("priority requires a nonempty task_id")
                        if (not isinstance(args.get("priority"), int)
                                or isinstance(args.get("priority"), bool)
                                or not 0 <= args["priority"] <= 100):
                            raise ValueError("priority must be an integer between 0 and 100")
                    elif request["command"] == "trace":
                        if set(args) - {"hours"}:
                            raise ValueError("trace args must contain only hours")
                        hours_value = args.get("hours")
                        if hours_value is not None and (
                            not isinstance(hours_value, (int, float)) or isinstance(hours_value, bool)
                            or not 0 < float(hours_value) <= 8760
                        ):
                            raise ValueError("trace hours must be between 0 and 8760")
                    else:
                        if set(args) - {"action", "name"}:
                            raise ValueError("skills args must contain only action and name")
                        action = args.get("action", "list")
                        if not isinstance(action, str) or action not in {"list", "refresh", "activate", "unload"}:
                            raise ValueError("unknown skills action")
                        if action in {"activate", "unload"}:
                            if not isinstance(args.get("name"), str) or not args["name"].strip():
                                raise ValueError("skills activate/unload requires a nonempty name")
                        elif "name" in args:
                            raise ValueError("skills list/refresh does not accept name")
                if kind == "command" and request["command"] == "autonomy":
                    args = request.get("args")
                    if not isinstance(args, dict) or set(args) != {"level"}:
                        raise ValueError("autonomy args must contain only level")
                    if args["level"] not in permissions.AUTONOMY_LEVELS:
                        raise ValueError("unknown autonomy level")
                if kind == "command" and request["command"] == "plan" and request.get("args", {}).get("action") in {"mode-on", "mode-off", "mode-status"}:
                    try:
                        text = self.plan_mode_command(request["args"]["action"])
                        self.send("command_result", request_id, command="plan", text=text)
                    except OSError as exc:
                        self.error(request_id, "plan_mode_error", str(exc))
                    return
                if self.active is not None and not self.active.done():
                    self.error(request_id, "busy", "another run is active")
                    return
                if kind == "run" and task_meta is None and self.plan_mode.status()["pending"]:
                    try:
                        self.plan_mode.commit_pending()
                        self.send_plan_mode()
                    except OSError as exc:
                        self.error(request_id, "default_mode_unavailable", f"默认模式尚未生效，未启动任务：{exc}")
                        return
                if kind == "run" and task_meta is None and self.plan_mode.status()["active"]:
                    if self.task_runtime is None:
                        self.error(request_id, "plan_mode_requires_tasks",
                                   "session plan mode requires VIBE_TASK_RUNTIME")
                        return
                    plan_text = str(request["text"])
                    task_meta = {"kind": "run", "mode": "plan", "objective": plan_text[:500]}
                run_task = None
                if kind == "run" and task_meta is not None:
                    if self.task_runtime is None:
                        self.error(request_id, "task_runtime_disabled",
                                   "task metadata requires VIBE_TASK_RUNTIME")
                        return
                    try:
                        if task_meta.get("task_id"):
                            record = self.task_runtime.store.get(task_meta["task_id"])
                            if record.mode == "plan" and "creation" in record.extra:
                                self.error(request_id, "revision_resume_required", "修订任务需通过带已观察段号的 resume-revision 续做")
                                return
                        run_task = self.task_runtime.prepare_run(request_id, task_meta)
                    except TaskRuntimeDisabled as exc:
                        self.error(request_id, "task_runtime_disabled", str(exc))
                        return
                    except TaskBudgetExceeded as exc:
                        self.error(request_id, "task_budget_exceeded", str(exc))
                        return
                    except (TaskRuntimeError, TaskError) as exc:
                        self.error(request_id, "task_error", f"{type(exc).__name__}: {exc}")
                        return
                self.active_id = request_id
                self.active_kind = "run" if kind == "run" else "command"
                self.cancel = asyncio.Event()
                if run_task is not None and self.task_runtime is not None:
                    self.send("task_state", request_id,
                              task=self.task_runtime.payload(run_task.record))
                self.send("status", request_id, phase="running")
                self.active = asyncio.create_task(self.session_operation(
                    self.run(request_id, request["text"], run_task) if kind == "run"
                    else self.command(request_id, request["command"], request.get("args")), writer,
                ))
            elif kind == "cancel":
                if not isinstance(request["target"], str) or not request["target"]:
                    raise ValueError("cancel target must be a run id")
                if self.active_id != request["target"] or self.active is None or self.active.done():
                    self.error(request_id, "run_mismatch", "target is not the active run")
                    return
                self.stopping()
                self.send("ack", request_id, action="cancel")
            elif kind == "steer":
                if not isinstance(request["target"], str) or not request["target"]:
                    raise ValueError("steer target must be a run id")
                if not isinstance(request["text"], str) or not request["text"].strip():
                    raise ValueError("steer text must be a nonempty string")
                if (
                    self.active_id != request["target"] or self.active is None
                    or self.active.done() or self.cancel.is_set()
                ):
                    self.error(request_id, "run_mismatch", "target is not the active run")
                    return
                if not self.agent.steer(request["text"]):
                    self.error(request_id, "run_mismatch", "target is not accepting steering")
                    return
                self._mark_plan_stale_for_steer()
                self.send("ack", request_id, action="steer")
            elif kind == "approval":
                if (
                    not isinstance(request["approval_id"], str) or not request["approval_id"]
                    or request["decision"] not in ("once", "session", "workspace", "deny")
                ):
                    raise ValueError("expected approval_id and decision once, session, workspace or deny")
                if (
                    self.pending is None or self.pending[0] != request["approval_id"]
                    or self.pending[1].done() or self.cancel.is_set()
                ):
                    self.error(request_id, "approval_mismatch", "approval is absent or already resolved")
                    return
                if request["decision"] not in self.pending[2]:
                    raise ValueError("approval scope is not available for this tool")
                self.pending[1].set_result(request["decision"])
                self.send("ack", request_id, action="approval")
            else:
                self.closed_id = request_id
                self.stop.set()
        except SessionBusy as exc:
            self.error(request_id, 'session_busy', str(exc))
        except OSError as exc:
            self.error(request_id, 'session_unavailable', f'无法安全接入会话，未启动：{exc}')
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            self.error(request_id, "invalid_request", str(exc))
        finally:
            if writer is not None and self.active_id != request_id:
                writer.close()
                if self.session_writer is writer:
                    self.session_writer = None


async def serve(
    agent: Agent,
    config: Config,
    session: SessionStore | None = None,
    *,
    requests: AsyncIterable[str | bytes] | None = None,
    emit: Callable[[dict], None] | None = None,
    resumed: bool = False,
    mcp: McpManager | None = None,
) -> None:
    """单连接 JSONL 适配器；执行、工具和审批仍由 Python 持有。"""
    session = session or SessionStore.new(
        config.workspace, config.model,
        purpose="test" if isinstance(agent.provider, FakeProvider) else "user",
    )
    if getattr(agent, "on_round", None) is None:
        # 会话轮次记录由 stdio 后端保证：/evidence 与验收三态都依赖最近一轮。
        agent.on_round = session.save_round
    if emit is None:
        stream = sys.stdout
        emit = lambda event: print(json.dumps(event, ensure_ascii=False), file=stream, flush=True)
    runtime_enabled = task_runtime_enabled(session.purpose)
    task_runtime = (
        TaskRuntime(TaskStore(session.root), workspace=config.workspace, enabled=True)
        if runtime_enabled else None
    )
    interrupted = []
    if task_runtime is not None:
        try:
            with SessionWriter(session.root):
                interrupted = task_runtime.recover()
        except SessionBusy:
            pass
    connection = _Connection(agent, config, session, emit, mcp=mcp, task_runtime=task_runtime)
    register_delegate_tool(agent, session.root)
    if agent.approval is None:
        agent.approval = ApprovalGate(audit_path=session.audit_path)
    previous_handler = agent.approval.handler
    agent.approval.handler = connection.approve
    agent.approval.audit_path = session.audit_path
    loop = asyncio.get_running_loop()
    signals = {}
    for sig in (signal.SIGINT, signal.SIGTERM, *([signal.SIGHUP] if hasattr(signal, "SIGHUP") else [])):
        try:
            previous = signal.getsignal(sig)
            loop.add_signal_handler(sig, connection.stop.set)
            signals[sig] = previous
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    frames = (requests if requests is not None else _stdin_frames()).__aiter__()
    reading = None
    stopped = asyncio.create_task(connection.stop.wait())
    capabilities = ["run", "cancel", "steer", "approval_once", "approval_scopes", "session_recording",
                    "metrics", "history_restore", "commands", "resume", "inspection-history", "task-navigation", "session-navigation", "permission-panel", "history-search", "rounds-ledger"]
    from app.runtime import plan as plan_artifacts
    if plan_artifacts.fcntl is not None:
        capabilities.append("plan-review")
        if task_runtime is not None and agent.tools_enabled:
            capabilities.append("plan-revision")
            capabilities.append("plan-revision-history")
    if task_runtime is not None:
        capabilities.append("tasks")
        capabilities.append("modes")
        capabilities.append("plan-mode-control")
    if agent.tools_enabled:
        capabilities.append("native_tools")
    provider = (
        "fake" if isinstance(agent.provider, FakeProvider)
        else "openai" if isinstance(agent.provider, OpenAICompatProvider)
        else type(agent.provider).__name__
    )
    try:
        from app.runtime.inspection import capture_file_source
        from app.runtime.session_navigation import session_identity
        try:
            identity = session_identity(session, config.workspace, session.purpose)
        except (OSError, ValueError, TypeError):
            identity = None
        connection.send("ready", workspace=str(config.workspace.resolve()), model=config.model, file_source=capture_file_source(agent.tools.root),
                        session_id=session.id, session_path=str(session.root.resolve()),
                    **({'session_identity': identity} if identity is not None else {}),
                    provider=provider, capabilities=capabilities,
                    tasks=_task_hints(session.root), resumed=resumed,
                    session_purpose=session.purpose,
                    autonomy=agent.autonomy,
                    autonomy_label=permissions.AUTONOMY_LABELS.get(
                        agent.autonomy, agent.autonomy),
                    skills=(agent.tools.skill_entries() if agent.tools_enabled else []))
        if task_runtime is not None:
            try:
                connection.plan_mode.commit_pending()
            except OSError as exc:
                connection.send("notice", None, message=f"默认模式尚未生效：{exc}；请核对 /plan status。")
        connection.send_plan_mode()
        for record in interrupted:
            connection.send("notice", None,
                            message=f"检测到中断任务 {record.task_id}（未自动重放）；可用 /goal resume 或重新开始。")
        if resumed:
            connection.restore()
        await connection.publish_loaded_context()
        if mcp is not None and mcp.servers and agent.tools_enabled:
            connection.seen.add("startup")
            connection.active_id = "startup"
            connection.active_phase = "starting"
            connection.send("status", "startup", phase="starting")
            connection.active = asyncio.create_task(connection.initialize())
        else:
            connection.send("status", phase="idle")
        while not connection.stop.is_set():
            reading = asyncio.create_task(anext(frames))
            finished, _ = await asyncio.wait({reading, stopped}, return_when=asyncio.FIRST_COMPLETED)
            if stopped in finished:
                break
            try:
                frame = reading.result()
            except StopAsyncIteration:
                break
            connection.accept(frame)
    finally:
        connection.stop.set()
        connection.stopping()
        for task in (reading, stopped):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in (reading, stopped) if t is not None), return_exceptions=True)
        if connection.active is not None:
            try:
                await asyncio.wait_for(asyncio.shield(connection.active), 3)
            except asyncio.TimeoutError:
                connection.active.cancel()
                await asyncio.gather(connection.active, return_exceptions=True)
            except Exception as exc:
                connection.error(connection.active_id, "backend_error", f"{type(exc).__name__}: {exc}")
        if hasattr(frames, "aclose"):
            await frames.aclose()
        if mcp is not None:
            try:
                await mcp.close()
            except Exception as exc:
                connection.send("notice", message=f"MCP 关闭失败：{type(exc).__name__}: {exc}"[:16000])
        agent.approval.handler = previous_handler
        for sig, previous in signals.items():
            loop.remove_signal_handler(sig)
            signal.signal(sig, previous)
        connection.send("closed", connection.closed_id)


def run_stdio(agent: Agent, config: Config, session: SessionStore | None = None,
              *, resumed: bool = False, mcp: McpManager | None = None) -> None:
    stream = sys.stdout

    def emit(event):
        print(json.dumps(event, ensure_ascii=False), file=stream, flush=True)

    with contextlib.redirect_stdout(sys.stderr):
        asyncio.run(serve(agent, config, session, emit=emit, resumed=resumed, mcp=mcp))
