from __future__ import annotations

import asyncio
import contextlib
import json
import math
import signal
import sys
import time
import uuid
from collections.abc import AsyncIterable, Callable
from dataclasses import asdict, replace

from app.agent import Agent, AgentResult
from app.config import Config
from app.project_profile import load_project_background
from app.user_profile import load_user_profile, select_user_preferences
from app.context_snapshot_display import snapshot_for_display
from app.mcp import McpManager
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.providers.openai_compat import OpenAICompatProvider
from app.session import SessionStore
from core.models import ToolCall

MAX_FRAME_BYTES = 1024 * 1024
_OVERSIZED = object()
COMMANDS = frozenset({"resume", "permissions", "permissions_reset", "cost", "tools", "diff", "mcp", "skills"})
RESUME_INSTRUCTION = (
    "继续上次暂停的任务，开启新一轮预算。先核对已保存的进度和未执行操作；"
    "对于结果未知的写入或命令，先检查实际文件与执行结果，禁止直接重放。"
    "再完成剩余工作并验证结果。"
)


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


class _Connection:
    def __init__(self, agent: Agent, config: Config, session: SessionStore, emit, mcp=None):
        self.agent = agent
        self.config = config
        self.session = session
        self.emit = emit
        self.mcp = mcp
        self.seen: set[str] = set()
        self.active_id: str | None = None
        self.active: asyncio.Task | None = None
        self.cancel = asyncio.Event()
        self.stop = asyncio.Event()
        self.closed_id: str | None = None
        self.pending: tuple[str, asyncio.Future, list[str]] | None = None
        self.active_phase = "running"
        self.output_open = True
        self.has_started_run = False

    def send(self, kind: str, request_id: str | None = None, **data):
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
                  call=asdict(call), preview=preview, decisions=decisions)
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

    async def command(self, request_id, command, args=None):
        skills = None
        if command == "resume" and self.agent.history:
            await self.run(request_id, RESUME_INSTRUCTION)
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
                text = ("已撤销当前工作区的本进程授权。\n" if command.endswith("reset") else "")
                text += (f"workspace 授权：{'已开启' if rules['workspace_allowed'] else '未开启'}；"
                         f"session 精确规则：{rules['session_rules']} 条。重启后重新审批。")
            elif command == "cost":
                summary = self.session.cost_summary()
                text = (f"session {self.session.id} · runs {summary['runs']} · "
                        f"tokens {summary['total_tokens']:,} "
                        f"(in {summary['prompt_tokens']:,} / out {summary['completion_tokens']:,}) · "
                        f"cost ${summary['cost_usd']:.6f} · tools {summary['tool_calls']} · "
                        f"last {summary['last_status'] or '未运行'}")
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
            else:
                call = ToolCall("bash", {"command": "git diff --stat && git diff --color=never", "timeout": 10},
                                f"{request_id}:diff")
                decision = await self.agent.approval.request(
                    call, self.agent.tools.preview(call), self.agent.tools.root,
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
            self.send("command_result", request_id, **payload)
        except asyncio.CancelledError:
            self.send("command_result", request_id, command=command, text="命令已取消；已开始操作的结果需要核对。")
        except Exception as exc:
            self.send("command_result", request_id, command=command,
                      text=f"命令失败：{type(exc).__name__}: {exc}"[:16000])
        finally:
            self.active_id = None
            if not self.stop.is_set():
                if command == "skills" and not self.has_started_run:
                    await self.publish_loaded_context()
                self.send("status", phase="idle")

    async def run(self, request_id: str, text: str):
        self.has_started_run = True
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

        def tool_result(call, ok, output):
            nonlocal observed_tools
            observed_tools += 1
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
            trace_identity.update({key: event[key] for key in ("run_id", "model") if key in event})
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
            result = await self.agent.run(
                text,
                on_text=lambda delta: self.send("text_delta", request_id, delta=delta),
                on_tool=lambda call: self.send("tool_call", request_id, call=asdict(call)),
                on_result=tool_result,
                on_execution=lambda call, execution: executions.__setitem__(id(call), execution),
                on_context=record_context,
                on_state=record_state,
                on_message=persist_message,
                on_history=self.session.save_context,
                trace=record_trace,
                cancel=self.cancel,
            )
            completed = True
        except asyncio.CancelledError:
            result = AgentResult("cancelled", 0, "cancelled", dict(observed_usage), metrics["cost_usd"],
                                 trace_identity.get("run_id", ""))
        except Exception as exc:
            self.error(request_id, "session_error" if isinstance(exc, OSError) else "agent_error",
                       f"{type(exc).__name__}: {exc}")
            result = AgentResult(f"error: {type(exc).__name__}: {exc}", 0, "error", dict(observed_usage),
                                 metrics["cost_usd"], trace_identity.get("run_id", ""))
        finally:
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
            ending.update(status=result.status, stop_reason=result.stop_reason)
            if result.status == "error":
                ending["error"] = result.text
            try:
                self.session.append_trace(ending)
            except OSError as exc:
                self.error(request_id, "session_error", f"trace save failed: {exc}")
                result = replace(result, text=f"error: trace save failed: {exc}", status="error", stop_reason="")
            if self.agent.state is not None:
                metrics.update(done=len(self.agent.state.done), planned=len(self.agent.state.plan))
            publish_metrics(force=True)
            self.send("done", request_id, result=asdict(result))
            self.active_id = None
            if not self.stop.is_set():
                self.send("status", phase="idle")

    def accept(self, frame):
        if frame is _OVERSIZED:
            self.error(None, "frame_too_large", f"input exceeds {MAX_FRAME_BYTES} bytes")
            return
        request_id = None
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
            if kind == "command" and request.get("command") == "skills" and "args" in request:
                fields[kind] = fields[kind] | {"args"}
            if set(request) != {"v", "type", "id"} | fields[kind]:
                raise ValueError("unexpected or missing request fields")
            if kind in {"run", "command"}:
                if kind == "run" and (not isinstance(request["text"], str) or not request["text"].strip()):
                    raise ValueError("run text must be a nonempty string")
                if kind == "command" and (not isinstance(request["command"], str) or request["command"] not in COMMANDS):
                    raise ValueError("unknown command")
                if kind == "command" and request["command"] == "skills" and "args" in request:
                    args = request["args"]
                    if not isinstance(args, dict) or set(args) - {"action", "name"}:
                        raise ValueError("skills args must contain only action and name")
                    action = args.get("action", "list")
                    if not isinstance(action, str) or action not in {"list", "refresh", "activate", "unload"}:
                        raise ValueError("unknown skills action")
                    if action in {"activate", "unload"}:
                        if not isinstance(args.get("name"), str) or not args["name"].strip():
                            raise ValueError("skills activate/unload requires a nonempty name")
                    elif "name" in args:
                        raise ValueError("skills list/refresh does not accept name")
                if self.active is not None and not self.active.done():
                    self.error(request_id, "busy", "another run is active")
                    return
                self.active_id = request_id
                self.cancel = asyncio.Event()
                self.send("status", request_id, phase="running")
                self.active = asyncio.create_task(
                    self.run(request_id, request["text"]) if kind == "run"
                    else self.command(request_id, request["command"], request.get("args"))
                )
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
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            self.error(request_id, "invalid_request", str(exc))


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
    if emit is None:
        stream = sys.stdout
        emit = lambda event: print(json.dumps(event, ensure_ascii=False), file=stream, flush=True)
    connection = _Connection(agent, config, session, emit, mcp=mcp)
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
                    "metrics", "history_restore", "commands", "resume"]
    if agent.tools_enabled:
        capabilities.append("native_tools")
    provider = (
        "fake" if isinstance(agent.provider, FakeProvider)
        else "openai" if isinstance(agent.provider, OpenAICompatProvider)
        else type(agent.provider).__name__
    )
    try:
        connection.send("ready", workspace=str(config.workspace.resolve()), model=config.model,
                        session_id=session.id, session_path=str(session.root.resolve()),
                    provider=provider, capabilities=capabilities, resumed=resumed,
                    session_purpose=session.purpose,
                    skills=(agent.tools.skill_entries() if agent.tools_enabled else []))
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
