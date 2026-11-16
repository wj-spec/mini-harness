"""仅配置启动状态：处理模型配置，不创建 Agent、任务或会话。

缺模型配置时，`app --stdio` 进入这个状态：只提供配置概览、审批保存、
显式检测和进入项目；run/resume/后台任务与 MCP 启动一律拒绝。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable, Iterable, Mapping
from urllib.parse import urlsplit

from app.runtime.user_config import config_path, startup_environment
from app.runtime.model_config import DEFAULT_BASE_URL, DEFAULT_MODEL, KEY_NAMES, MODEL_NAMES, configured_key, is_placeholder_key, load_environment, usable_key
from app.runtime.model_settings import ModelSettings, ModelSettingsError
from app.safety.policy import ApprovalDecision, ApprovalGate
from core.contracts import ToolCall

MAX_FRAME_BYTES = 1024 * 1024
BOOTSTRAP_CAPABILITIES = ("config-bootstrap", "model-settings", "commands")
CONFIG_COMMANDS = {"model", "bootstrap"}
REJECTED_TYPES = {"run", "steer"}


class _Oversized:
    pass


_OVERSIZED = _Oversized()


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


class Bootstrap:
    def __init__(self, config, emit, *, environ: Mapping[str, str] | None = None,
                 exec_: Callable[[str, list[str], Mapping[str, str]], None] = os.execvpe):
        self.config = config
        self.emit_event = emit
        self.environ = dict(os.environ if environ is None else environ)
        self.exec_ = exec_
        self.workspace = Path(config.workspace).resolve()
        self.seen: set[str] = set()
        self.pending: tuple[str, asyncio.Future, list[str]] | None = None
        self.stop = asyncio.Event()
        self.closed_id: str | None = None
        self.task: asyncio.Task | None = None
        self.task_id: str | None = None
        self.problem = self.environ.get("VIBE_CONFIG_PROBLEM", "").strip()
        self.gate = ApprovalGate(self._approve)
        self.model_settings = ModelSettings(
            config, path=self.config_path(), environ=self.prepared_environ(), approval=self.gate,
        )

    def prepared_environ(self) -> dict[str, str]:
        """界面上的“来源”只认可用值：空/占位 Key 不显示为进程环境覆盖。"""
        env = {name: value for name, value in self.environ.items()
               if name not in KEY_NAMES or usable_key(value)}
        try:
            inherited = json.loads(env.get("VIBE_MODEL_ENV_KEYS", "null"))
        except (ValueError, TypeError):
            inherited = None
        if isinstance(inherited, list):
            env["VIBE_MODEL_ENV_KEYS"] = json.dumps([
                name for name in inherited if isinstance(name, str)
                and (name not in KEY_NAMES or usable_key(env.get(name, "")))
            ])
        return env

    def config_path(self) -> Path:
        return config_path(self.environ)

    def send(self, kind: str, request_id: str | None = None, **data) -> None:
        event = {"v": 1, "type": kind, "id": request_id, **data}
        try:
            size = len(json.dumps(event, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (UnicodeError, ValueError, TypeError):
            self.stop.set()
            self.emit_event({"v": 1, "type": "error", "id": request_id,
                             "code": "invalid_output", "message": "配置事件不是有效 JSON"})
            return
        if size > MAX_FRAME_BYTES:
            self.stop.set()
            self.emit_event({"v": 1, "type": "error", "id": request_id,
                             "code": "frame_too_large", "message": "配置事件超过 1 MiB 帧上限"})
            return
        try:
            self.emit_event(event)
        except (BrokenPipeError, OSError):
            self.stop.set()

    def error(self, request_id, code, message, **extra) -> None:
        self.send("error", request_id, code=code, message=message, **extra)

    async def _approve(self, call: ToolCall, preview: str) -> ApprovalDecision:
        approval_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        decisions = ["once", "deny"]
        self.pending = (approval_id, future, decisions)
        self.send("approval_request", "bootstrap", approval_id=approval_id,
                  call=asdict(call), preview=preview, decisions=decisions)
        decision = "deny"
        try:
            decision = await future
        except asyncio.CancelledError:
            decision = "deny"
            raise
        finally:
            self.pending = None
            self.send("approval_resolved", "bootstrap", approval_id=approval_id, decision=decision)
        return ApprovalDecision(decision == "once", decision)

    def ready(self) -> dict:
        raw = (self.config.api_key or "").strip()
        reason = "" if usable_key(raw) else "placeholder_key" if raw else "missing_key"
        return {"workspace": str(self.workspace), "model": self.config.model, "session_id": "",
                "session_path": "", "provider": self.config.provider, "resumed": False,
                "session_purpose": "user", "capabilities": list(BOOTSTRAP_CAPABILITIES),
                "bootstrap": {"configured": False, "reason": reason,
                              "config_path": str(self.config_path()),
                              "problem": self.environ.get("VIBE_CONFIG_PROBLEM", ""),
                              "defaults": {"base_url": DEFAULT_BASE_URL, "model": DEFAULT_MODEL}}}

    async def serve(self, requests: Iterable[str | bytes] | None = None) -> None:
        self.send("ready", **self.ready())
        self.send("status", phase="idle")
        frames = (requests if requests is not None else _stdin_frames()).__aiter__()
        loop = asyncio.get_running_loop()
        signals = {}
        for sig in (signal.SIGINT, signal.SIGTERM, *([signal.SIGHUP] if hasattr(signal, "SIGHUP") else [])):
            try:
                previous = signal.getsignal(sig)
                loop.add_signal_handler(sig, self.stop.set)
                signals[sig] = previous
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        stopped = asyncio.create_task(self.stop.wait())
        reading = None
        try:
            while not self.stop.is_set():
                reading = asyncio.create_task(anext(frames))
                finished, _ = await asyncio.wait({reading, stopped}, return_when=asyncio.FIRST_COMPLETED)
                if stopped in finished:
                    if not reading.done():
                        reading.cancel()
                    break
                try:
                    frame = reading.result()
                except StopAsyncIteration:
                    break
                await self.accept(frame)
        finally:
            for pending in (reading, stopped):
                if pending is not None and not pending.done():
                    pending.cancel()
            await asyncio.gather(*(item for item in (reading, stopped) if item is not None), return_exceptions=True)
            task, self.task = self.task, None
            self.task_id = None
            if task is not None and not task.done():
                if self.stop.is_set():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if hasattr(frames, "aclose"):
                try:
                    await frames.aclose()
                except (RuntimeError, ValueError):
                    pass
            for sig, previous in signals.items():
                try:
                    loop.remove_signal_handler(sig)
                    signal.signal(sig, previous)
                except (NotImplementedError, RuntimeError, ValueError):
                    pass
            self.send("closed", self.closed_id)

    async def accept(self, frame) -> None:
        if frame is _OVERSIZED:
            self.error(None, "frame_too_large", "input exceeds 1 MiB")
            return
        if isinstance(frame, bytes):
            try:
                frame = frame.decode("utf-8")
            except UnicodeDecodeError:
                self.error(None, "protocol_error", "请求不是有效 UTF-8")
                return
        request_id = None
        try:
            request = json.loads(frame)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            raw_id = request.get("id")
            if isinstance(raw_id, str) and 0 < len(raw_id.strip()) <= 128:
                request_id = raw_id
            if type(request.get("v")) is not int or request.get("v") != 1 or request_id is None:
                raise ValueError("expected v=1 and a nonempty id of at most 128 characters")
            if request_id in self.seen:
                self.error(request_id, "duplicate_id", "request id was already used")
                return
            self.seen.add(request_id)
            kind = request.get("type")
            allowed = {"command": {"command"}, "approval": {"approval_id", "decision"},
                       "shutdown": set(), "run": {"text"}, "cancel": {"target"}, "steer": {"target", "text"}}
            if not isinstance(kind, str) or kind not in allowed:
                raise ValueError("unknown request type")
            extra = set()
            if kind == "run" and "task" in request:
                extra = {"task"}
            elif kind == "command" and "args" in request:
                extra = {"args"}
            if set(request) != {"v", "type", "id"} | allowed[kind] | extra:
                raise ValueError("unexpected or missing request fields")
            if kind == "cancel":
                self.cancel_command(request_id, request.get("target"))
                return
            if kind in REJECTED_TYPES:
                self.error(request_id, "bootstrap_unavailable",
                           "模型尚未配置；当前只能保存、检测配置与进入项目，任务运行不可用。")
                return
            if kind == "shutdown":
                self.closed_id = request_id
                self.stop.set()
                return
            if kind == "approval":
                self.resolve_approval(request_id, request.get("approval_id"), request.get("decision"))
                return
            if request.get("command") not in CONFIG_COMMANDS:
                self.error(request_id, "bootstrap_unavailable",
                           "模型尚未配置；该操作需要进入项目后使用。")
                return
            if self.task is not None and not self.task.done():
                self.error(request_id, "bootstrap_busy", "已有配置操作处理中；请等待回执。")
                return
            self.task_id = request_id
            self.task = asyncio.create_task(self.handle(request))
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            self.error(request_id, "protocol_error", str(exc)[:500])

    def cancel_command(self, request_id: str, target) -> None:
        """取消正在进行的配置操作；已保存的配置不撤回。"""
        if (self.task is None or self.task.done() or not isinstance(target, str)
                or target != self.task_id):
            self.error(request_id, "cancel_mismatch", "没有匹配的待取消配置操作")
            return
        if self.pending is not None and not self.pending[1].done():
            self.pending[1].set_result("deny")
        self.task.cancel()
        self.send("error", target, code="cancelled", message="已取消当前配置操作；已保存的配置保留。")

    def resolve_approval(self, request_id: str, approval_id, decision) -> None:
        if decision not in ("once", "deny"):
            self.error(request_id, "approval_mismatch", "expected decision once or deny")
            return
        if (self.pending is None or self.pending[0] != approval_id or self.pending[1].done()
                or decision not in self.pending[2]):
            self.error(request_id, "approval_mismatch", "approval is absent or already resolved")
            return
        self.pending[1].set_result(decision)
        self.send("ack", request_id, action="approval")

    async def handle(self, request: dict) -> None:
        request_id = request["id"]
        try:
            if request["command"] == "model":
                await self.handle_model(request_id, request.get("args"))
            else:
                await self.handle_bootstrap(request_id, request.get("args"))
        except ModelSettingsError as exc:
            self.error(request_id, "model_settings_error", str(exc),
                       **({"field": exc.field} if exc.field else {}))
        except (OSError, ValueError, TypeError) as exc:
            self.error(request_id, "model_settings_error", f"配置操作失败：{exc}"[:500])
        except Exception as exc:
            # 不把原始服务错误或文件内容直接回显给界面。
            print(f"bootstrap: {type(exc).__name__}", file=sys.stderr)
            self.error(request_id, "bootstrap_error", "配置操作失败；请重试，或用 ./start.sh --configure 在终端配置")

    async def handle_model(self, request_id: str, args) -> None:
        if not isinstance(args, dict):
            raise ModelSettingsError("模型设置参数无效。")
        allowed = {"action", "model", "base_url", "api_key", "trust_env", "revision"}
        if set(args) - allowed:
            raise ModelSettingsError("模型配置字段无效。")
        action = args.get("action")
        if action == "list":
            if set(args) != {"action"}:
                raise ModelSettingsError("模型设置参数无效。")
            self.reply_settings(request_id, "")
            return
        if self.problem:
            raise ModelSettingsError("配置文件无法解析；程序不会覆盖原文件。请按提示修复后重启。")
        if action == "apply":
            raise ModelSettingsError("配置准备阶段不能切换模型；进入项目后生效。")
        if action not in {"save", "test"}:
            raise ModelSettingsError("模型操作无效。")
        if action == "save":
            text = await self.model_settings.save(args)
            self.refresh_saved(args)
        else:
            text = await self.model_settings.probe(args)
        self.reply_settings(request_id, text)

    def refresh_saved(self, args) -> None:
        """保存成功后只更新界面/校验依据；不创建运行环境。"""
        candidate = self.model_settings.candidate(args)
        self.config = replace(
            self.config, provider="openai", model=candidate["model"], base_url=candidate["base_url"],
            api_key=candidate["api_key"], trust_env=candidate["trust_env"],
        )
        env = self.prepared_environ()
        sources = dict(self.model_settings.loaded_sources)
        sources.update({name: "用户配置" if self.model_settings.path.suffix == ".toml" else ".env" for name in MODEL_NAMES})
        env["VIBE_CONFIG_SOURCES"] = json.dumps(sources, ensure_ascii=False)
        self.model_settings = ModelSettings(
            self.config, path=self.model_settings.path, environ=env, approval=self.gate,
        )

    def reply_settings(self, request_id: str, message: str) -> None:
        self.send("model_settings", request_id, **self.model_settings.overview(),
                  message=message, workspace=str(self.workspace), session_id="")

    async def handle_bootstrap(self, request_id: str, args) -> None:
        if self.problem:
            raise ModelSettingsError("配置文件无法解析；程序不会覆盖原文件。请按提示修复后重启。")
        if args != {"action": "activate"}:
            raise ModelSettingsError("配置准备阶段只支持进入项目。")
        if self.pending is not None:
            raise ModelSettingsError("请先处理当前审批，再进入项目。")
        path = self.model_settings.path
        try:
            saved = load_environment(path, {})
        except (OSError, ValueError) as exc:
            raise ModelSettingsError(f"配置文件无法解析：{exc}") from None
        key = configured_key(saved)
        if not key:
            raise ModelSettingsError("尚未保存可用的 API Key；请先保存配置。")
        env = self.activation_env(saved)
        model = env.get("VIBE_MODEL", "").strip()
        base_url = (env.get("VIBE_BASE_URL") or env.get("OPENAI_BASE_URL") or "").strip().rstrip("/")
        if not model or not valid_endpoint(base_url):
            raise ModelSettingsError("模型名或服务地址无效；请返回修改后重试。")
        argv = list(getattr(sys, "orig_argv", None) or [sys.executable, "-m", "app", "--stdio"])
        self.send("bootstrap_activation", request_id,
                  text="配置有效，正在初始化真实运行环境…")
        try:
            self.exec_(argv[0], argv, env)
        except OSError as exc:
            self.error(request_id, "bootstrap_activate", f"无法进入项目：{exc}"[:500])
            return
        # 测试注入的 exec 会返回；真实 exec 不会回到这里。
        self.stop.set()

    def activation_env(self, saved: Mapping[str, str]) -> dict[str, str]:
        """准备 exec 用的环境：占位/空值让位给刚保存的文件值，其余仍按环境优先。"""
        env = dict(self.environ)
        explicit = self.model_settings.env_keys
        if "VIBE_MODEL_ENV_KEYS" in env:
            for name in MODEL_NAMES - explicit:
                env.pop(name, None)
        for name in MODEL_NAMES:
            value = env.get(name)
            if value is None:
                continue
            if not value.strip() or (name in KEY_NAMES and is_placeholder_key(value)):
                env.pop(name, None)
        env["VIBE_CONFIG_FILE"] = str(self.model_settings.path)
        return startup_environment(Path(__file__).resolve().parents[2], self.workspace, env)


def valid_endpoint(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        parsed.port
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not (
            parsed.username or parsed.password or parsed.query or parsed.fragment)
    except ValueError:
        return False


async def serve(config, *, requests=None, emit=None, environ=None, exec_=os.execvpe) -> None:
    if emit is None:
        stream = sys.stdout
        emit = lambda event: print(json.dumps(event, ensure_ascii=False), file=stream, flush=True)
    state = Bootstrap(config, emit, environ=environ, exec_=exec_)
    await state.serve(requests)


def run_bootstrap(config, *, environ=None) -> None:
    stream = sys.stdout

    def emit(event):
        print(json.dumps(event, ensure_ascii=False), file=stream, flush=True)

    with contextlib.redirect_stdout(sys.stderr):
        asyncio.run(serve(config, emit=emit, environ=environ))
