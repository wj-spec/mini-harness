"""FastMCP 客户端适配：配置、审批、工具映射与连接生命周期。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import signal
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.policy import ApprovalGate
from app.tools import ToolRegistry, ToolSpec
from core.models import ToolCall

MAX_RESULT_CHARS = 20_000
MAX_STDERR_CHARS = 4_000
STARTUP_TIMEOUT = 15.0

# SDK 创建独立进程组；记录组号以清理已先行退出的 launcher 的后代。
_RECORD_GROUP_AND_EXEC = (
    "import os,sys\n"
    "with open(sys.argv[1], 'x') as f: f.write(str(os.getpgrp()))\n"
    "os.execvpe(sys.argv[2], sys.argv[2:], os.environ)\n"
)


class McpError(RuntimeError):
    """连接、配置或协议层失败。"""


class McpToolError(RuntimeError):
    """远端工具执行失败。"""


@dataclass
class McpServer:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    timeout: float = 60.0
    risk: str = "exec"
    allowed_tools: tuple[str, ...] = ()
    tool_risks: dict[str, str] = field(default_factory=dict)
    allowed_origins: tuple[str, ...] = ()


def _valid_origin(value: str) -> bool:
    from urllib.parse import urlsplit

    parts = urlsplit(value)
    return parts.scheme in {"http", "https"} and bool(parts.netloc) and parts.path in {"", "/"} and not parts.query and not parts.fragment


def _check_origin(tool: str, url: str, allowed_origins: tuple[str, ...]) -> None:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise McpToolError(f"{tool}: blocked non-http origin: {url[:200]}")
    origin = f"{parts.scheme}://{parts.netloc}".rstrip("/")
    if origin not in allowed_origins:
        raise McpToolError(f"{tool}: origin not allowed: {origin}; allowed: {', '.join(allowed_origins)}")


def parse_servers(raw: dict[str, Any] | None) -> dict[str, McpServer]:
    if raw is not None and not isinstance(raw, dict):
        raise ValueError("mcp servers must be an object")
    servers = {}
    for name, item in (raw or {}).items():
        if not isinstance(name, str) or not name.strip() or not isinstance(item, dict):
            raise ValueError("mcp server must have a name and an object config")
        command = item.get("command")
        args, env = item.get("args", []), item.get("env", {})
        cwd, risk = item.get("cwd"), item.get("risk", "exec")
        allowed_tools = item.get("allowed_tools", [])
        tool_risks = item.get("tool_risks", {})
        allowed_origins = item.get("allowed_origins", [])
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"mcp server {name!r} missing command")
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ValueError(f"mcp server {name!r} args must be a list of strings")
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise ValueError(f"mcp server {name!r} env must be a string mapping")
        if cwd is not None and not isinstance(cwd, str):
            raise ValueError(f"mcp server {name!r} cwd must be a string")
        try:
            timeout = float(item.get("timeout", 60))
        except (TypeError, ValueError):
            raise ValueError(f"mcp server {name!r} timeout must be positive and finite") from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(f"mcp server {name!r} timeout must be positive and finite")
        if not isinstance(risk, str) or risk not in {"read", "write", "exec"}:
            raise ValueError(f"mcp server {name!r} risk must be read, write or exec")
        if not isinstance(allowed_tools, list) or not all(
            isinstance(tool, str) and tool.strip() for tool in allowed_tools
        ):
            raise ValueError(f"mcp server {name!r} allowed_tools must be a list of names")
        normalized_allowed = tuple(dict.fromkeys(tool.strip() for tool in allowed_tools))
        if not isinstance(tool_risks, dict) or not all(
            isinstance(tool, str) and tool.strip() and isinstance(value, str)
            and value in {"read", "write", "exec"}
            for tool, value in tool_risks.items()
        ):
            raise ValueError(f"mcp server {name!r} tool_risks must map tool names to read/write/exec")
        if normalized_allowed and set(tool_risks) - set(normalized_allowed):
            raise ValueError(f"mcp server {name!r} tool_risks contains tools outside allowed_tools")
        if not isinstance(allowed_origins, list) or not all(
            isinstance(origin, str) and origin.strip() for origin in allowed_origins
        ):
            raise ValueError(f"mcp server {name!r} allowed_origins must be a list of origins")
        normalized_origins = tuple(dict.fromkeys(origin.strip().rstrip("/") for origin in allowed_origins))
        if any(not _valid_origin(origin) for origin in normalized_origins):
            raise ValueError(f"mcp server {name!r} allowed_origins must be http(s) origins")
        servers[name] = McpServer(
            name, command, list(args), dict(env), cwd, timeout, risk,
            normalized_allowed, dict(tool_risks), normalized_origins,
        )
    return servers


def load_servers(config_file: str | Path | None, inline: str | None) -> dict[str, Any]:
    if config_file:
        text = Path(config_file).read_text(encoding="utf-8")
    elif inline:
        text = inline
    else:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("mcp config must be a JSON object")
    for key in ("servers", "mcpServers"):
        if key in data:
            if not isinstance(data[key], dict):
                raise ValueError(f"mcp {key} must be an object")
            return data[key]
    return data


def _slug(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name) or "x"


def _render_result(result: Any) -> str:
    if hasattr(result, "model_dump"):
        result = result.model_dump(by_alias=True, exclude_none=True)
    parts = []
    for item in result.get("content") or []:
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        elif item.get("type") == "resource":
            resource = item.get("resource") or {}
            parts.append(str(resource.get("text") or resource.get("uri") or ""))
        else:
            parts.append(json.dumps(item, ensure_ascii=False))
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
    text = "\n".join(p for p in parts if p) or "(empty)"
    return text[:MAX_RESULT_CHARS] + "\n...[truncated]" if len(text) > MAX_RESULT_CHARS else text


class _Connection:
    def __init__(self, server: McpServer, root: Path):
        self.server, self.root = server, root
        self.client = None
        self.transport = None
        self.broken: str | None = None
        stem = root / ".vibe" / "mcp" / f"{_slug(server.name)[:40]}-{uuid.uuid4().hex}"
        self.log_path = stem.with_suffix(".stderr.log")
        self.group_path = stem.with_suffix(".pgid")

    async def open(self) -> list[Any]:
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        command, args = self.server.command, self.server.args
        if os.name == "posix":
            args = ["-c", _RECORD_GROUP_AND_EXEC, str(self.group_path), command, *args]
            command = sys.executable
        self.transport = StdioTransport(
            command=command, args=args, env=self.server.env,
            cwd=str((self.root / (self.server.cwd or ".")).resolve()),
            keep_alive=False, log_file=self.log_path,
        )
        self.client = Client(self.transport, name=f"vibe:{self.server.name}",
                             timeout=self.server.timeout, init_timeout=STARTUP_TIMEOUT)
        async with asyncio.timeout(STARTUP_TIMEOUT):
            await self.client.__aenter__()
            return await self.client.list_tools()

    async def call(self, name: str, arguments: dict) -> str:
        if self.broken is not None or self.client is None:
            raise McpError(f"server {self.server.name} unavailable: {self.broken or 'closed'}")
        request = asyncio.create_task(self.client.call_tool_mcp(name, arguments, timeout=self.server.timeout))
        try:
            done, _ = await asyncio.wait({request}, timeout=self.server.timeout)
            if not done:
                raise TimeoutError(f"MCP tool timeout after {self.server.timeout}s")
            result = request.result()
        except (asyncio.CancelledError, TimeoutError):
            self.broken = "timeout or cancelled"
            # 先解除阻塞，避免 SDK 的取消通知也堵在同一条管道上。
            await self._stop_group()
            try:
                await self.close()
            except Exception as exc:
                self.broken = f"timeout or cancelled; cleanup failed: {exc}"
            raise
        except Exception as exc:
            raise McpError(f"server {self.server.name}: {exc}") from exc
        finally:
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)
        text = _render_result(result)
        if result.is_error:
            raise McpToolError(text)
        return text

    def _signal_group(self, sig: int) -> None:
        if os.name != "posix" or not self.group_path.exists():
            return
        group = int(self.group_path.read_text())
        if group <= 1 or group == os.getpgrp():
            raise McpError("invalid MCP process group")
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            pass

    async def _stop_group(self) -> None:
        if os.name == "posix" and self.group_path.exists():
            self._signal_group(signal.SIGTERM)
            await asyncio.sleep(0.05)
            self._signal_group(signal.SIGKILL)

    async def close(self) -> None:
        self.broken = "closed"
        try:
            if self.client is not None:
                await self.client.close()
        finally:
            try:
                if self.transport is not None:
                    await self.transport.close()
            finally:
                try:
                    await self._stop_group()
                finally:
                    self.group_path.unlink(missing_ok=True)
                    self.client = None

    def diagnostic(self) -> str:
        if not self.log_path.exists():
            return ""
        with self.log_path.open("rb") as log:
            log.seek(max(0, self.log_path.stat().st_size - MAX_STDERR_CHARS))
            return log.read(MAX_STDERR_CHARS).decode("utf-8", errors="replace")


class McpManager:
    """在 TUI 的事件循环中注册、调用并关闭外部服务。"""

    def __init__(self, servers: dict[str, McpServer], root: str | Path):
        self.servers, self.root = servers, Path(root).resolve()
        self.clients: dict[str, _Connection] = {}
        self.tools: dict[str, tuple[str, str]] = {}
        self.errors: list[str] = []

    @classmethod
    def from_config(cls, raw: dict[str, Any] | None, root: str | Path) -> McpManager:
        return cls(parse_servers(raw), root)

    @classmethod
    def from_env(cls, config_file: str, inline: str, root: str | Path) -> McpManager:
        return cls(parse_servers(load_servers(config_file or None, inline or None)), root)

    async def register_into(self, registry: ToolRegistry, approval: ApprovalGate | None = None) -> list[str]:
        try:
            return await self._register_into(registry, approval)
        except BaseException:
            try:
                await self.close()
            except Exception as exc:
                self.errors.append(f"startup cleanup failed: {exc}")
            raise

    async def _register_into(self, registry: ToolRegistry, approval: ApprovalGate | None) -> list[str]:
        pending = []
        for name, server in self.servers.items():
            if name in self.clients:
                continue
            cwd = str((self.root / (server.cwd or ".")).resolve())
            env_hash = hashlib.sha256(json.dumps(server.env, sort_keys=True).encode()).hexdigest()
            call = ToolCall("mcp_start", {
                "server": name, "command": server.command, "args": server.args,
                "cwd": cwd, "env_keys": sorted(server.env), "env_hash": env_hash,
            })
            preview = (
                f"MCP server: {name}\ncommand: {json.dumps([server.command, *server.args], ensure_ascii=False)}"
                f"\ncwd: {cwd}\nenv keys: {', '.join(sorted(server.env)) or '(none)'}"
                f"\nallowed tools: {', '.join(server.allowed_tools) or '(all)'}"
                "\n\n[risk] exec — 启动外部服务，需要审批；工具调用另行审批。"
            )
            if approval is None or not (await approval.request(call, preview, self.root)).allowed:
                self.errors.append(f"{name}: startup denied (approval_required)")
                continue
            connection = _Connection(server, self.root)
            try:
                remote_tools = await connection.open()
                names = [tool.name for tool in remote_tools]
                if any(not n.strip() for n in names) or len(set(names)) != len(names):
                    raise McpError("invalid or duplicate remote tool names")
                if server.allowed_tools:
                    by_name = {tool.name: tool for tool in remote_tools}
                    missing = [name for name in server.allowed_tools if name not in by_name]
                    if missing:
                        raise McpError(f"missing allowed MCP tools: {', '.join(missing)}")
                    remote_tools = [by_name[name] for name in server.allowed_tools]
            except BaseException as exc:
                try:
                    await connection.close()
                except Exception as cleanup_error:
                    self.errors.append(f"{name}: cleanup failed: {cleanup_error}")
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                self.errors.append(f"{name}: {type(exc).__name__}: {exc} {connection.diagnostic()}".strip())
                continue
            self.clients[name] = connection
            pending.extend((name, connection, tool) for tool in remote_tools)

        bases = Counter(f"mcp_{_slug(name)}_{_slug(tool.name)}" for name, _, tool in pending)
        used = set(registry.names())
        for name, connection, tool in pending:
            base = f"mcp_{_slug(name)}_{_slug(tool.name)}"
            exposed = base
            ambiguous = "_" in name or "_" in tool.name
            if ambiguous or bases[base] > 1 or len(base) > 64 or _slug(name) != name or _slug(tool.name) != tool.name or base in used:
                digest = hashlib.sha256(json.dumps([name, tool.name]).encode()).hexdigest()[:12]
                exposed = f"{base[:51]}_{digest}"
            if exposed in used:
                raise McpError(f"duplicate exposed MCP tool: {exposed}")
            used.add(exposed)

            registry.register(ToolSpec(
                exposed, tool.description or f"MCP {name}:{tool.name}",
                tool.input_schema, self._make_fn(connection, tool.name, connection.server.allowed_origins), timeout=connection.server.timeout,
                risk=connection.server.tool_risks.get(tool.name, connection.server.risk),
            ))
            self.tools[exposed] = (name, tool.name)
        return sorted(self.tools)

    @staticmethod
    def _make_fn(connection: _Connection, name: str, allowed_origins: tuple[str, ...] = ()):
        async def invoke(**kwargs):
            if allowed_origins and isinstance(kwargs.get("url"), str):
                _check_origin(name, str(kwargs["url"]), allowed_origins)
            return await connection.call(name, kwargs)

        return invoke

    async def close(self) -> None:
        clients, self.clients = self.clients, {}
        results = await asyncio.gather(*(client.close() for client in clients.values()), return_exceptions=True)
        for name, result in zip(clients, results):
            if isinstance(result, BaseException):
                self.errors.append(f"{name}: cleanup failed: {result}")
                raise McpError(self.errors[-1]) from result
