from __future__ import annotations

import asyncio
import json

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.worker import WorkerCancelled, WorkerFailed
from textual.widgets import Footer, Header, Input, RichLog, Static

from app.agent import Agent
from app.config import Config
from app.context_view import ContextHistory, context_detail, context_legend, context_stack, context_summary
from app.policy import ApprovalDecision
from app.sandbox import run_command
from core.models import Message, ToolCall


class ContextScreen(ModalScreen[None]):
    BINDINGS = [
        ("left", "previous", "上个快照"),
        ("right", "next", "下个快照"),
        ("escape", "close", "返回"),
        Binding("ctrl+c", "cancel", "取消任务", priority=True),
        Binding("ctrl+q", "quit_app", "退出", priority=True),
    ]

    CSS = """
    ContextScreen { align: center middle; }
    #context_dialog { width: 96%; height: 92%; border: round $primary; background: $surface; padding: 0 1; }
    #context_title, #context_hint { height: auto; }
    #context_scroll { height: 1fr; }
    #context_scroll:focus { border: tall $primary; }
    #context_body { height: auto; }
    #context_hint { color: $text-muted; }
    """

    def __init__(self, history: ContextHistory):
        super().__init__()
        self.history = history
        self.index = max(0, len(history.snapshots) - 1)
        self._following_latest = True
        self._selected_id = None

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("上下文 · 本轮回放（最多 100 张）", id="context_title"),
            VerticalScroll(Static("", id="context_body"), id="context_scroll"),
            Static("←/→ 切换 · ↑/↓ 滚动 · Esc 返回 · Ctrl+C 取消任务", id="context_hint"),
            id="context_dialog",
        )

    def on_mount(self) -> None:
        self.update_snapshot()
        self.query_one("#context_scroll", VerticalScroll).focus()

    def on_resize(self) -> None:
        if self.is_mounted:
            self.update_snapshot()

    def update_snapshot(self) -> None:
        snapshots = self.history.snapshots
        if self._following_latest:
            self.index = max(0, len(snapshots) - 1)
        elif self._selected_id is not None:
            self.index = next((i for i, item in enumerate(snapshots)
                               if item["snapshot_id"] == self._selected_id), 0)
        self.index = max(0, min(self.index, len(snapshots) - 1))
        self._selected_id = snapshots[self.index]["snapshot_id"] if snapshots else None
        loaded = bool(snapshots and snapshots[self.index]["phase"] == "loaded")
        self.query_one("#context_title", Static).update(
            "上下文 · 基础装载" if loaded else "上下文 · 本轮回放（最多 100 张）"
        )
        self.query_one("#context_body", Static).update(
            context_detail(snapshots, self.index, max(20, self.size.width - 8), dark=self.app.current_theme.dark)
        )

    def _select(self, direction: int) -> None:
        snapshots = self.history.snapshots
        self.index = max(0, min(self.index + direction, len(snapshots) - 1))
        self._following_latest = self.index == len(snapshots) - 1
        self._selected_id = snapshots[self.index]["snapshot_id"] if snapshots else None
        self.update_snapshot()
        self.query_one("#context_scroll", VerticalScroll).scroll_home(animate=False)

    def action_previous(self) -> None:
        self._select(-1)

    def action_next(self) -> None:
        self._select(1)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.app.action_cancel()

    async def action_quit_app(self) -> None:
        await self.app.action_quit()


class ApprovalScreen(ModalScreen[str]):
    BINDINGS = [
        ("y", "allow_once", "Allow once"),
        ("a", "allow_once", "Allow once"),
        ("s", "allow_session", "Allow session"),
        ("p", "allow_workspace", "Allow workspace"),
        ("n", "deny", "Deny"),
        ("d", "deny", "Deny"),
        ("escape", "deny", "Deny"),
        Binding("ctrl+c", "cancel", "Cancel task", priority=True),
        Binding("ctrl+q", "quit_app", "Quit", priority=True),
    ]

    CSS = """
    ApprovalScreen { align: center middle; }
    #approval { width: 86%; height: 80%; border: round $warning; background: $surface; padding: 1 2; }
    #preview { height: 1fr; }
    #approval_header { height: auto; max-height: 4; }
    #hint { height: auto; color: $text-muted; }
    """

    def __init__(self, tool_call: ToolCall, preview: str):
        super().__init__()
        self.tool_call = tool_call
        self.preview = preview

    def compose(self) -> ComposeResult:
        builtin = self.tool_call.name in {"write_file", "edit_file", "bash"}
        if self.tool_call.name in {"write_file", "edit_file"}:
            scope = "s = 本会话此工具与此文件"
        elif self.tool_call.name == "bash":
            scope = "s = 本会话此目录下相同命令"
        else:
            scope = "s = 本会话此工具与相同参数"
        hint = "y/a = 仅本次 · " + scope + " · n/d = 拒绝"
        if builtin:
            hint += "\np = 本进程工作区的写入和命令；命令可影响工作区外路径"
        hint += "\n等待审批不计时 · Ctrl+C 取消任务 · Ctrl+Q 退出"
        if self.tool_call.name in {"write_file", "edit_file"}:
            target = str(self.tool_call.args.get("path", ""))
        else:
            target = str(self.tool_call.args)
        if len(target) > 240:
            target = target[:240] + "…（完整内容见下方预览）"
        yield Vertical(
            Static(
                f"[bold]{self.tool_call.name}[/] {escape(target)}",
                markup=True, id="approval_header",
            ),
            VerticalScroll(Static(self.preview, markup=False), id="preview"),
            Static(hint, id="hint", markup=False),
            id="approval",
        )

    def action_allow_once(self) -> None:
        self.dismiss("once")

    def action_allow_session(self) -> None:
        self.dismiss("session")

    def action_allow_workspace(self) -> None:
        if self.tool_call.name in {"write_file", "edit_file", "bash"}:
            self.dismiss("workspace")

    def action_cancel(self) -> None:
        self.app.action_cancel()

    async def action_quit_app(self) -> None:
        await self.app.action_quit()

    def action_deny(self) -> None:
        self.dismiss("deny")


class VibeApp(App):
    TITLE = "vibe"
    SUB_TITLE = "local coding agent"

    CSS = """
    Screen { layout: vertical; }
    #log { height: 1fr; border: round $primary; padding: 0 1; }
    #stream { height: auto; max-height: 10; padding: 0 1; }
    #prompt { height: 3; }
    #status { height: auto; min-height: 2; max-height: 9; background: $boost; color: $text-muted; padding: 0 1; }
    """

    BINDINGS = [
        ("ctrl+c", "cancel", "Cancel"),
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+o", "context", "Context"),
    ]

    def __init__(self, config: Config, agent: Agent, session=None, resumed: bool = False, mcp=None):
        super().__init__()
        self.config = config
        self.agent = agent
        self.session = session
        if session is not None and session.purpose == "test":
            self.title = "测试会话 · vibe"
        self.resumed = resumed
        self.cancel_event = asyncio.Event()
        self.busy = False
        self.current_text = ""
        self.last_state = None
        self.last_context = None
        self.context_history = ContextHistory()
        self._run_generation = 0
        self._loaded_context_pending = False
        self._loaded_context_error = False
        self.run_budget = None
        self.mcp = mcp
        self._mcp_starting = bool(mcp is not None and mcp.servers and agent.tools_enabled)
        self._mcp_worker = None
        self._mcp_closed = False
        self._mcp_shutdown_error = None
        self._quitting = False
        if getattr(agent, "approval", None) is not None:
            agent.approval.handler = self.request_approval

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="log", wrap=True, markup=True)
        yield Static("", id="stream", markup=False)
        yield Input(placeholder="Ask about this repo…", id="prompt")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#status", Static).update(self._status_renderable())
        self.query_one("#prompt", Input).focus()
        self.query_one("#log", RichLog).write(
            "[dim]/cost 看花费；/context 或 Ctrl+O 看上下文；/permissions 查看授权；/resume 继续已暂停任务。审批：y 单次、s 精确规则、p 工作区、n 拒绝。[/]"
        )
        if getattr(self.agent, "project_instructions", None):
            self.query_one("#log", RichLog).write(
                f"[dim]AGENTS.md loaded ({len(self.agent.project_instructions)} chars)[/]"
            )
        if getattr(self.agent, "tools", None) is not None and getattr(self.agent.tools, "skills", None):
            names = ", ".join(skill.name for skill in self.agent.tools.skills)
            self.query_one("#log", RichLog).write(f"[dim]skills: {names}[/]")
        if getattr(self.agent, "approval", None) is None:
            self.query_one("#log", RichLog).write(
                "[red]approval gate missing：写工具会 fail closed（approval_required）[/]"
            )
        if self._mcp_starting:
            self.busy = True
            self.query_one("#prompt", Input).disabled = True
            self._mcp_worker = self.initialize_mcp()
        else:
            self._restore_session()
        self.refresh_loaded_context()

    @work(group="loaded-context", exclusive=True)
    async def refresh_loaded_context(self) -> None:
        if self.agent.context is None or self._run_generation or self._quitting:
            return
        generation = self._run_generation
        self._loaded_context_pending = True
        self._loaded_context_error = False
        self.query_one("#status", Static).update(self._status_renderable())
        try:
            key = self.session.id if self.session is not None else f"tui-{id(self)}"
            snapshot = await self.agent.loaded_context_snapshot(key)
            if generation == self._run_generation and not self._quitting and snapshot is not None:
                self._record_trace(snapshot)
        except Exception as exc:
            if generation == self._run_generation and not self._quitting:
                self._loaded_context_error = True
                self.query_one("#log", RichLog).write(
                    f"[red]基础上下文统计失败：{escape(str(exc))}[/]"
                )
        finally:
            self._loaded_context_pending = False
            if self.is_mounted and not self._quitting:
                self.query_one("#status", Static).update(self._status_renderable())

    @work(group="mcp-startup", exclusive=True)
    async def initialize_mcp(self) -> None:
        log = self.query_one("#log", RichLog)
        try:
            loaded = await self.mcp.register_into(self.agent.tools, self.agent.approval)
            for message in self.mcp.errors:
                log.write(f"[yellow]warning: mcp server failed: {escape(message)}[/]")
            if loaded:
                log.write(f"[dim]mcp tools: {len(loaded)} from {len(self.mcp.clients)} server(s)[/]")
        except Exception as exc:
            log.write(f"[red]MCP startup failed: {escape(str(exc))}[/]")
        finally:
            self._mcp_starting = False
            self.busy = False
            prompt = self.query_one("#prompt", Input)
            if prompt.is_mounted and not self._quitting:
                prompt.disabled = False
                prompt.focus()
            if not self._quitting:
                self.refresh_loaded_context()
        if not self._quitting and not self.cancel_event.is_set():
            self._restore_session()

    def _restore_session(self) -> None:
        if self.session is not None:
            log = self.query_one("#log", RichLog)
            log.write(f"[dim]session {self.session.id} · {self.session.root}[/]")
            if self.resumed:
                self._restore_context_snapshots()
                history = self.session.load_context()
                latest_run = self.session.latest_run()
                self.last_state = self.session.load_state()
                if latest_run is not None:
                    self._update_budget(latest_run)
                log.write(f"[dim]resumed {len(history)} messages[/]")
                for m in history[-6:]:
                    if m.role == "user":
                        log.write(f"[bold cyan]>[/] {escape(m.content[:300])}")
                    elif m.role == "assistant" and m.content:
                        log.write(f"[bold]assistant[/] {escape(m.content[:300])}")
                    elif m.role == "tool":
                        log.write(f"[dim]tool {m.name or ''}: {escape(m.content[:160])}[/]")
                if latest_run and latest_run.get("status") == "stopped":
                    reason = latest_run.get("stop_reason") or "stopped（旧记录未注明原因）"
                    log.write(f"[yellow]任务已暂停：{escape(reason)}。[/]")
                    if self.last_state is not None:
                        done = "；".join(self.last_state.done) or "暂无已记录项"
                        pending = "；".join(self.last_state.open) or "暂无已记录项"
                        log.write(f"[dim]已完成：{escape(done)}；待继续：{escape(pending)}[/]")
                    self._show_resume_hint(reason)
                elif latest_run and latest_run.get("status") == "cancelled":
                    log.write("[yellow]任务已取消；结果未知的操作需要先核对，取消不代表撤销。[/]")
                    self._show_resume_hint("cancelled")
                elif self._needs_resume(history):
                    log.write("[yellow]resuming interrupted turn…[/]")
                    self.handle_turn(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.busy:
            return
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        if text in {"/cost", "/costs"}:
            self.show_cost()
            return
        if text == "/context":
            self.action_context()
            return
        if text == "/diff":
            self.show_diff()
            return
        if text.split()[0] in {"/skill", "/skills"}:
            parts = text.split()
            action = parts[1] if len(parts) > 1 else "list"
            if ((action in {"list", "refresh"} and len(parts) <= 2)
                    or (action in {"activate", "unload"} and len(parts) == 3)):
                self.show_skills(action, parts[2] if len(parts) == 3 else None)
            else:
                self.query_one("#log", RichLog).write(
                    "[yellow]用法：/skills；/skills refresh；/skills activate NAME；/skills unload NAME[/]"
                )
            return
        if text in {"/permissions", "/permissions reset"}:
            self.show_permissions(reset=text.endswith(" reset"))
            return
        if text == "/resume":
            if not self.agent.history:
                self.query_one("#log", RichLog).write("[yellow]没有可继续的任务。[/]")
                return
            self.query_one("#log", RichLog).write(
                "[yellow]继续任务，开启新一轮预算。[/]"
            )
            self.handle_turn(
                "继续上次暂停的任务。先核对已保存的进度和未执行操作，避免重复执行；"
                "再完成剩余工作并验证结果。"
            )
            return
        if text == "/help":
            self.query_one("#log", RichLog).write(
                "[dim]/cost 看 token / 花费；/context 或 Ctrl+O 看上下文回放；/diff 看 git diff；/skills 列出技能，refresh 重扫，activate NAME 激活，unload NAME 卸载；/permissions 查看授权，reset 撤销；/resume 继续暂停任务；其它输入交给 agent[/]"
            )
            return
        self.handle_turn(text)

    @staticmethod
    def _needs_resume(history) -> bool:
        if not history:
            return False
        last = history[-1]
        return not (
            last.role == "assistant"
            and last.content.strip()
            and not last.tool_calls
        )

    @work(exclusive=True)
    async def handle_turn(self, text: str | None) -> None:
        self.busy = True
        self.cancel_event.clear()
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = True
        log = self.query_one("#log", RichLog)
        if text is not None:
            log.write(f"[bold cyan]>[/] {escape(text)}")
        self.current_text = ""
        stream = self.query_one("#stream", Static)
        stream.update("")

        try:
            result = await self.agent.run(
                text,
                on_text=self._on_text,
                on_tool=self._on_tool,
                on_result=self._on_result,
                on_context=self._on_context,
                on_state=self._on_state,
                on_message=self._record_message,
                on_history=self._record_history,
                trace=self._record_trace,
                cancel=self.cancel_event,
            )
            if result.status == "done":
                if result.text:
                    log.write(f"[bold]assistant[/] {escape(result.text)}")
            elif result.status == "cancelled":
                log.write("[yellow]cancelled[/]")
            elif result.status == "error":
                log.write(f"[red]{escape(result.text)}[/]")
            else:
                log.write(f"[yellow]{escape(result.text)}[/]")
                if result.status == "stopped":
                    self._show_resume_hint(result.stop_reason)
        except Exception as exc:
            log.write(f"[red]harness error: {type(exc).__name__}: {escape(str(exc))}[/]")
        finally:
            self.busy = False
            if prompt.is_mounted:
                prompt.disabled = False
                prompt.focus()
            if stream.is_mounted:
                stream.update("")

    async def request_approval(self, call: ToolCall, preview: str) -> ApprovalDecision:
        screen = ApprovalScreen(call, preview)
        try:
            scope = await self.push_screen_wait(screen)
        finally:
            if self.screen is screen:
                await screen.dismiss("cancelled")
        allowed = scope in {"once", "session", "workspace"}
        return ApprovalDecision(allowed, scope or "deny")

    def show_permissions(self, reset: bool = False) -> None:
        log = self.query_one("#log", RichLog)
        gate = self.agent.approval
        if gate is None:
            log.write("[yellow]审批闸未配置。[/]")
            return
        if reset:
            gate.clear(self.agent.tools.root)
            log.write("[dim]已撤销当前工作区的本进程授权。[/]")
        permissions = gate.permissions(self.agent.tools.root)
        enabled = "已开启" if permissions["workspace_allowed"] else "未开启"
        log.write(f"[dim]本进程工作区授权：{enabled}；精确规则 {permissions['session_rules']} 条。重启后重新审批。[/]")

    def _on_text(self, delta: str) -> None:
        self.current_text += delta
        self.query_one("#stream", Static).update(self.current_text)

    def _on_tool(self, call: ToolCall) -> None:
        self.query_one("#log", RichLog).write(
            f"[cyan]→ {call.name}[/] {escape(str(call.args))}"
        )
        self.current_text = ""
        self.query_one("#stream", Static).update("")

    def _on_result(self, call: ToolCall, ok: bool, output: str) -> None:
        color = "green" if ok else "red"
        self.query_one("#log", RichLog).write(
            f"[{color}]{'ok' if ok else 'error'}[/] {escape(output[:400])}"
        )

    def _on_context(self, result) -> None:
        if "history_before" in result.sections:
            before = result.sections["history_before"]
            after = result.sections["history_after"]
            recent = result.sections.get("recent_groups", 0)
            self.query_one("#log", RichLog).write(
                f"[dim]history 压缩：约 {before:,} → {after:,} tokens，保留近期 {recent} 组；原文已留存。[/]"
            )
        else:
            self.last_context = result
        self.query_one("#status", Static).update(self._status_renderable())

    def _record_history(self, messages: list[Message]) -> None:
        if self.session is not None:
            try:
                self.session.save_context(messages)
            except OSError as exc:
                self.query_one("#log", RichLog).write(f"[red]context save failed: {exc}[/]")

    def _on_state(self, state) -> None:
        self.last_state = state
        if self.session is not None:
            try:
                self.session.save_state(state)
            except OSError as exc:
                self.query_one("#log", RichLog).write(f"[red]session save failed: {exc}[/]")
        self.query_one("#status", Static).update(self._status_renderable())

    def show_diff(self) -> None:
        log = self.query_one("#log", RichLog)
        try:
            code, output, timed_out = run_command(
                "git diff --stat && git diff --color=never",
                self.config.workspace,
                timeout=10,
            )
        except Exception as exc:
            log.write(f"[red]diff failed: {type(exc).__name__}: {exc}[/]")
            return
        if timed_out:
            log.write("[red]diff timed out[/]")
            return
        if not output.strip():
            log.write("[dim]no diff[/]")
            return
        log.write(output[-4000:])

    def show_skills(self, action: str = "list", name: str | None = None) -> None:
        log = self.query_one("#log", RichLog)
        tools = getattr(self.agent, "tools", None)
        if tools is None or not self.agent.tools_enabled:
            log.write("[yellow]skills 不可用：当前工具已禁用[/]")
            return
        try:
            log.write(escape(self.agent.manage_skills(action=action, name=name)))
        except Exception as exc:
            log.write(f"[red]skills 失败：{escape(str(exc))}[/]")
        finally:
            self.refresh_loaded_context()

    def show_cost(self) -> None:
        log = self.query_one("#log", RichLog)
        if self.run_budget is not None:
            budget = self.run_budget
            prompt = budget.get("prompt_tokens")
            completion = budget.get("completion_tokens")
            incoming = f"{prompt:,}" if prompt is not None else "?"
            outgoing = f"{completion:,}" if completion is not None else "?"
            mark = "约 " if budget.get("estimated") else ""
            log.write(
                f"[bold]本轮[/] tokens {mark}{budget.get('used_tokens', 0):,} "
                f"(入 {incoming} / 出 {outgoing})"
            )
        if self.session is None:
            if self.run_budget is None:
                log.write("[yellow]no session[/]")
            return
        summary = self.session.cost_summary()
        if summary["runs"] == 0:
            log.write("[dim]no completed runs in this session yet[/]")
            return
        log.write(
            f"[bold]session[/] {self.session.id}  "
            f"runs {summary['runs']}  "
            f"tokens {summary['total_tokens']:,} "
            f"(in {summary['prompt_tokens']:,} / out {summary['completion_tokens']:,})  "
            f"cost ${summary['cost_usd']:.6f}  "
            f"tools {summary['tool_calls']}  "
            f"last {summary['last_status']}"
        )

    def _record_trace(self, event: dict) -> None:
        if event.get("type") == "run_start":
            self.last_context = None
            self._run_generation += 1
        if self.context_history.accept(event):
            self.query_one("#status", Static).update(self._status_renderable())
            if isinstance(self.screen, ContextScreen):
                self.screen.update_snapshot()
        self._update_budget(event)
        if event.get("type") == "no_progress":
            reasons = {
                "repeated_result": "重复取得相同结果",
                "tool_error": "连续工具失败",
                "state_only": "仅更新计划或笔记",
                "permission_denied": "操作未获授权",
            }
            reason = reasons.get(event.get("reason"), "连续没有新证据")
            action = "已提示调整方法" if event.get("action") == "warn" else "停止工具执行并收尾"
            self.query_one("#log", RichLog).write(f"[yellow]{reason}，{action}。[/]")
        if self.session is None:
            return
        try:
            self.session.append_trace(event)
        except OSError as exc:
            self.query_one("#log", RichLog).write(f"[red]trace append failed: {exc}[/]")

    def _restore_context_snapshots(self) -> None:
        try:
            with self.session.trace_path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        self.context_history.accept(event)
        except FileNotFoundError:
            return
        except OSError as exc:
            self.query_one("#log", RichLog).write(f"[red]context replay failed: {exc}[/]")
        self.context_history.run_active = False
        self.query_one("#status", Static).update(self._status_renderable())

    def action_context(self) -> None:
        if isinstance(self.screen, (ApprovalScreen, ContextScreen)):
            return
        self.push_screen(ContextScreen(self.context_history))

    def _update_budget(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "run_start":
            limit = event.get("limit_tokens", self.config.max_run_tokens)
            self.run_budget = {
                "used_tokens": 0, "limit_tokens": limit,
                "remaining_tokens": limit, "prompt_tokens": 0, "completion_tokens": 0,
                "estimated": False, "phase": "running",
            }
        elif event_type == "budget_update":
            self.run_budget = dict(event)
        elif event_type == "run_end":
            limit = event.get("limit_tokens", self.config.max_run_tokens)
            used = event.get("total_tokens", 0)
            self.run_budget = {
                "used_tokens": used, "limit_tokens": limit,
                "remaining_tokens": event.get("remaining_tokens", max(0, limit - used)),
                "prompt_tokens": event.get("prompt_tokens"),
                "completion_tokens": event.get("completion_tokens"),
                "estimated": event.get("usage_estimated", False),
                "phase": "stopped" if event.get("status") == "stopped" else "done",
            }
        else:
            return
        self.query_one("#status", Static).update(self._status_renderable())

    def _show_resume_hint(self, reason: str = "") -> None:
        saved = "进度已保存在本会话。" if self.session is not None else "任务已暂停。"
        if reason == "no_progress":
            self.query_one("#log", RichLog).write(
                f"[yellow]{saved}请补充信息或调整方法后继续。[/]"
            )
            return
        self.query_one("#log", RichLog).write(
            f"[yellow]{saved}输入 /resume 开启新一轮预算继续，或输入新指令；重新打开会话不会自动续跑。[/]"
        )

    def _record_message(self, message: Message) -> None:
        if self.session is None:
            return
        try:
            self.session.append_message(message)
        except OSError as exc:
            self.query_one("#log", RichLog).write(f"[red]session append failed: {exc}[/]")

    def _status_text(self, result=None) -> str:
        return self._status_renderable(result).plain

    def _status_renderable(self, result=None) -> Text:
        result = result if result is not None else self.last_context
        builder = self.agent.context
        budget = builder.display_budget if builder is not None else "?"
        width = max(1, self.size.width - 2)
        if self.context_history.snapshots:
            current = self.context_history.snapshots[-1]
            usage = Text(context_summary(current, width, include_sections=False))
            usage.append("\n已用构成 ")
            usage.append(context_stack(current, max(0, width - 9), composition=True,
                                       dark=self.current_theme.dark))
            usage.append("\n")
            usage.append(context_legend(current, width=width, dark=self.current_theme.dark))
        elif self._loaded_context_error and not self._run_generation:
            usage = Text("ctx 基础装载统计失败")
        elif self._loaded_context_pending and not self._run_generation:
            usage = Text("ctx 基础装载统计中…")
        elif result is None:
            usage = Text(f"ctx -/{budget}")
        else:
            mark = " over" if result.over_budget else ""
            history = result.sections.get("history")
            history_cap = result.caps.get("history")
            extra = f" · hist {history}/{history_cap}" if history is not None else ""
            usage = Text(f"ctx {result.tokens}/{budget}{mark}{extra}")
        run_usage = ""
        if self.run_budget is not None:
            used = self.run_budget.get("used_tokens", 0)
            limit = self.run_budget.get("limit_tokens", 0)
            remaining = self.run_budget.get("remaining_tokens", 0)
            mark = "约 " if self.run_budget.get("estimated") else ""
            ceiling = f"{limit:,} · 剩余 {remaining:,}" if limit > 0 else "不限"
            phase = {"compacting": " · 压缩中，将自动继续", "finalizing": " · 收尾中", "stopped": " · 已暂停"}.get(
                self.run_budget.get("phase"), ""
            )
            run_usage = f"任务累计 {mark}{used:,}/{ceiling}{phase}"
            if width < 60 and limit > 0:
                run_usage = f"任务累计 {mark}{used:,}/{limit:,}\n剩余 {remaining:,}{phase}"
        todo = ""
        if self.last_state is not None:
            total = len(self.last_state.done) + len(self.last_state.open)
            todo = f" · todo {len(self.last_state.done)}/{total}"
        project = self.config.workspace.name or str(self.config.workspace)
        result_text = Text()
        if run_usage:
            result_text.append(run_usage + "\n")
        result_text.append(usage)
        result_text.append(todo + "\n")
        footer = Text(f"{self.config.model} · {project} · Ctrl+C 取消  Ctrl+Q 退出")
        footer.truncate(width, overflow="ellipsis")
        result_text.append(footer)
        return Text("\n").join(result_text.wrap(self.console, width, overflow="fold"))

    def watch_theme(self) -> None:
        self._refresh_context_display()

    def on_resize(self) -> None:
        self._refresh_context_display()

    def _refresh_context_display(self) -> None:
        if not self.screen_stack:
            return
        for status in self.query("#status"):
            status.update(self._status_renderable())
        if isinstance(self.screen, ContextScreen):
            self.screen.update_snapshot()

    def action_cancel(self) -> None:
        if not self.busy:
            return
        self.cancel_event.set()
        if isinstance(self.screen, ApprovalScreen):
            self.screen.dismiss("cancelled")
        if self._mcp_starting and self._mcp_worker is not None:
            self._mcp_worker.cancel()
        self.query_one("#log", RichLog).write("[yellow]cancelling…[/]")

    async def _close_mcp(self) -> None:
        if self.mcp is None or self._mcp_closed:
            return
        self._mcp_closed = True
        try:
            await self.mcp.close()
        except Exception as exc:
            self._mcp_shutdown_error = str(exc)
            self.log.error(f"MCP cleanup failed: {exc}")
            for log in self.query("#log"):
                if log.is_mounted:
                    log.write(f"[red]MCP cleanup failed: {escape(str(exc))}[/]")

    async def _stop_mcp_startup(self) -> None:
        worker = self._mcp_worker
        if worker is not None and not worker.is_finished:
            worker.cancel()
            try:
                await worker.wait()
            except (WorkerCancelled, WorkerFailed):
                pass

    async def on_unmount(self) -> None:
        self.cancel_event.set()
        await self._stop_mcp_startup()
        await self._close_mcp()

    async def action_quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.cancel_event.set()
        if isinstance(self.screen, ApprovalScreen):
            self.screen.dismiss("cancelled")
        await self._stop_mcp_startup()
        await self._close_mcp()
        self.exit()
