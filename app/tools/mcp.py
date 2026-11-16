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

from app.safety.policy import ApprovalGate
from app.browser.policy import BROWSER_READS, BROWSER_WRITES, BrowserPolicy, local_origin, normalized_origin
from app.tools.registry import ToolRegistry, ToolSpec
from core.contracts import ToolCall

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
    browser_actions: bool = False


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
        browser_actions = item.get("browser_actions", False)
        if not isinstance(browser_actions, bool):
            raise ValueError(f"mcp server {name!r} browser_actions must be boolean")
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
        if browser_actions:
            if not normalized_origins or not all(local_origin(origin) for origin in normalized_origins):
                raise ValueError("controlled browser requires explicit loopback origins")
            normalized_origins = tuple(dict.fromkeys(normalized_origin(origin) for origin in normalized_origins))
            if (not normalized_allowed or "browser_snapshot" not in normalized_allowed
                    or set(normalized_allowed) - BROWSER_READS - BROWSER_WRITES):
                raise ValueError("controlled browser requires a restricted browser tool allowlist")
        servers[name] = McpServer(
            name, command, list(args), dict(env), cwd, timeout, risk,
            normalized_allowed, dict(tool_risks), normalized_origins, browser_actions,
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
        self.browser = BrowserPolicy(root, server.allowed_origins) if server.browser_actions else None
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
        env = dict(self.server.env)
        if self.browser is not None:
            env["VIBE_BROWSER_ORIGINS"] = json.dumps(self.server.allowed_origins)
        self.transport = StdioTransport(
            command=command, args=args, env=env,
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
        self.retired: list[McpManager] = []

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
                "allowed_tools": list(server.allowed_tools),
                "allowed_origins": list(server.allowed_origins), "browser_actions": server.browser_actions,
            })
            preview = (
                f"MCP server: {name}\ncommand: {json.dumps([server.command, *server.args], ensure_ascii=False)}"
                f"\ncwd: {cwd}\nenv keys: {', '.join(sorted(server.env)) or '(none)'}"
                f"\nallowed tools: {', '.join(server.allowed_tools) or '(all)'}"
                f"\nallowed origins: {', '.join(server.allowed_origins) or '(unrestricted)'}"
                f"\ncontrolled browser actions: {server.browser_actions}"
                f"\n范围：仅此服务；其他服务与后台进程不变。"
                "\n\n[risk] exec — 启动外部服务，需要审批；工具调用另行审批。"
                "\n[审批层] T2 本地执行"
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

            browser = getattr(connection, "browser", None)
            controlled_write = browser is not None and tool.name in BROWSER_WRITES
            if controlled_write and approval is not None:
                approval.require_once(exposed, browser.audit)
            registry.register(ToolSpec(
                exposed, tool.description or f"MCP {name}:{tool.name}",
                tool.input_schema, self._make_fn(connection, tool.name, connection.server.allowed_origins), timeout=connection.server.timeout,
                risk="write" if controlled_write else connection.server.tool_risks.get(tool.name, connection.server.risk),
                preview_fn=(lambda call, b=browser, remote=tool.name: b.preview(remote, call)) if controlled_write else None,
            ))
            self.tools[exposed] = (name, tool.name)
        return sorted(self.tools)

    @staticmethod
    def _make_fn(connection: _Connection, name: str, allowed_origins: tuple[str, ...] = ()):
        async def invoke(**kwargs):
            browser = getattr(connection, "browser", None)
            if browser is None and allowed_origins and isinstance(kwargs.get("url"), str):
                _check_origin(name, str(kwargs["url"]), allowed_origins)
            if browser is None:
                return await connection.call(name, kwargs)
            if name in BROWSER_WRITES:
                browser.validate(name, kwargs)
            refresh = name in BROWSER_WRITES or name in {"browser_snapshot", "browser_close"}
            if refresh:
                browser.invalidate()
            try:
                output = await connection.call(name, kwargs)
                if refresh and name != "browser_close":
                    browser.observe(output)
                return "[untrusted browser data]\n" + output
            except BaseException:
                browser.invalidate()
                raise

        return invoke

    async def reconfigure(self, servers, registry, approval, *, only=None):
        """调用方保证空闲；所有新连接成功后才替换工具。"""
        if only is not None:
            if only not in servers:
                raise McpError("所选服务已不在配置中；请刷新或断开该连接。")
            servers = {only: servers[only]}
        if not servers:
            text = await self.disconnect(registry, approval)
            self.servers = {}
            return text
        old_tools = {tool: owner for tool, owner in self.tools.items() if only is None or owner[0] == only}
        staged = _StagedRegistry(set(registry.names()) - set(old_tools))
        gate = _StagedApproval(approval)
        candidate = McpManager(servers, self.root)
        try:
            await candidate.register_into(staged, gate)
            if set(candidate.clients) != set(servers):
                if gate.denied:
                    raise McpError("连接未获允许；所选服务保持未连接，其他服务与后台进程不变。")
                raise McpError("新连接未全部建立；原连接和工具保留。")
        except BaseException:
            try:
                await candidate.close()
            except Exception:
                self.retired.append(candidate)
            raise
        previous = McpManager({name: value for name, value in self.servers.items()
                               if only is None or name == only}, self.root)
        previous.clients = {name: value for name, value in self.clients.items() if only is None or name == only}
        previous.tools = old_tools
        for name in old_tools:
            registry.unregister(name)
        if approval is not None:
            approval.forget_tools(set(old_tools))
        for spec in staged.specs.values():
            registry.register(spec)
        gate.commit()
        self.servers = {**{name: value for name, value in self.servers.items() if only is not None and name != only}, **servers}
        self.clients = {**{name: value for name, value in self.clients.items() if only is not None and name != only}, **candidate.clients}
        self.tools = {**{name: value for name, value in self.tools.items() if name not in old_tools}, **candidate.tools}
        self.errors = []
        self.retired.append(previous)
        try:
            await previous.close()
            self.retired.remove(previous)
        except Exception:
            return "已重载 MCP；旧进程清理未完成，退出时将重试。"
        return f"已重载 MCP {only or '全部服务'}；后续调用使用新连接，后台进程不变。"

    async def disconnect(self, registry, approval, *, only=None):
        known = set(self.servers) | set(self.clients)
        if only is not None and only not in known:
            raise McpError("所选服务不存在；请刷新核对。")
        names = known if only is None else {only}
        call = ToolCall("mcp_disconnect", {"servers": sorted(names)})
        if approval is None or not (await approval.request(call,
                f"断开前台 MCP {only or '全部服务'} 并移除对应工具；其他服务与后台进程不变。", self.root)).allowed:
            raise McpError("断开未获允许；原连接保留。")
        old_tools = {tool for tool, owner in self.tools.items() if owner[0] in names}
        for name in old_tools:
            registry.unregister(name)
            self.tools.pop(name, None)
        approval.forget_tools(old_tools)
        if only is None:
            await self.close()
        else:
            previous = McpManager({}, self.root)
            if only in self.clients:
                previous.clients[only] = self.clients.pop(only)
            self.retired.append(previous)
            try:
                await previous.close()
                self.retired.remove(previous)
            except Exception:
                return "已移除所选 MCP 工具；进程清理未完成，退出时重试。"
        return f"已断开 MCP {only or '全部服务'}；重载可重新连接。"

    async def close(self) -> None:
        retired = list(self.retired)
        outcomes = await asyncio.gather(*(previous.close() for previous in retired), return_exceptions=True)
        self.retired = [previous for previous, outcome in zip(retired, outcomes) if isinstance(outcome, BaseException)]
        clients = dict(self.clients)
        results = await asyncio.gather(*(client.close() for client in clients.values()), return_exceptions=True)
        failed = bool(self.retired)
        for name, result in zip(clients, results):
            if isinstance(result, BaseException):
                self.errors.append(f"{name}: cleanup failed")
                failed = True
            else:
                self.clients.pop(name, None)
        if failed:
            raise McpError("MCP 进程清理未完成。")

class _StagedRegistry:
    def __init__(self, reserved):
        self.reserved, self.specs = reserved, {}

    def names(self):
        return self.reserved | set(self.specs)

    def register(self, spec):
        self.specs[spec.name] = spec


class _StagedApproval:
    def __init__(self, gate):
        self.gate, self.hooks, self.denied = gate, [], False

    async def request(self, *args):
        if self.gate is None:
            from app.safety.policy import ApprovalDecision
            return ApprovalDecision(False, "deny")
        call, preview, root = args
        decision = await self.gate.request(call, preview + "\n新连接全部成功后替换前台连接；失败保留原连接。", root)
        if not decision.allowed:
            self.denied = True
        return decision

    def require_once(self, name, audit):
        self.hooks.append((name, audit))

    def commit(self):
        for name, audit in self.hooks:
            self.gate.require_once(name, audit)
