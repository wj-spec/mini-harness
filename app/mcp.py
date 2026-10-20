"""MCP (Model Context Protocol) 客户端：接入外部 stdio 工具服务。

只用标准库：换行分隔的 JSON-RPC 2.0，同步收发，方便在 to_thread 里调用。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.tools import ToolRegistry, ToolSpec

PROTOCOL_VERSION = "2024-11-05"
MAX_RESULT_CHARS = 20_000
MAX_STDERR_CHARS = 4_000


class McpError(RuntimeError):
    """协议层或传输层失败。"""


class McpToolError(RuntimeError):
    """远端工具执行失败（isError=true）。"""


@dataclass
class McpServer:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    timeout: float = 60.0
    risk: str = "exec"


def parse_servers(raw: dict[str, Any] | None) -> dict[str, McpServer]:
    """把配置字典转成 McpServer；格式错误直接报错，避免静默失效。"""
    servers: dict[str, McpServer] = {}
    for name, item in (raw or {}).items():
        if not isinstance(item, dict):
            raise ValueError(f"mcp server {name!r} must be an object")
        command = item.get("command")
        if not isinstance(command, str) or not command:
            raise ValueError(f"mcp server {name!r} missing command")
        servers[name] = McpServer(
            name=name,
            command=command,
            args=[str(a) for a in item.get("args", [])],
            env={str(k): str(v) for k, v in (item.get("env") or {}).items()},
            cwd=item.get("cwd"),
            timeout=float(item.get("timeout", 60.0)),
            risk=str(item.get("risk", "exec")),
        )
    return servers


def load_servers(config_file: str | Path | None, inline: str | None) -> dict[str, Any]:
    """文件优先，其次行内 JSON。"""
    if config_file:
        text = Path(config_file).read_text(encoding="utf-8")
    elif inline:
        text = inline
    else:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("mcp config must be a JSON object")
    if "servers" in data and isinstance(data["servers"], dict):
        data = data["servers"]
    return data


class StdioClient:
    """单个 MCP server 的同步客户端。"""

    def __init__(self, server: McpServer, root: Path):
        self.server = server
        self.root = Path(root).resolve()
        self.proc: subprocess.Popen | None = None
        self._inbox: queue.Queue = queue.Queue()
        self._stderr: list[str] = []
        self._send_lock = threading.Lock()
        self._next_id = 0
        self._broken: str | None = None

    # --- 生命周期 ---
    def start(self) -> None:
        cwd = self.server.cwd
        env = dict(os.environ)
        env.update(self.server.env)
        try:
            self.proc = subprocess.Popen(
                [self.server.command, *self.server.args],
                cwd=str(self.root / cwd) if cwd else str(self.root),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise McpError(f"spawn failed: {type(exc).__name__}: {exc}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._inbox.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        self._inbox.put(None)

    def _read_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        for line in self.proc.stderr:
            self._stderr.append(line.rstrip())
            if len(self._stderr) > 50:
                del self._stderr[0]

    def _diag(self) -> str:
        text = "\n".join(self._stderr)[-MAX_STDERR_CHARS:]
        return f" | stderr: {text}" if text else ""

    def close(self) -> None:
        proc = self.proc
        if proc is None:
            return
        self._broken = "closed"
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            pass
        self.proc = None

    # --- 协议 ---
    def _send(self, payload: dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise McpError("server not started")
        line = json.dumps(payload, ensure_ascii=False)
        with self._send_lock:
            try:
                self.proc.stdin.write(line + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, ValueError, OSError) as exc:
                self._broken = f"write failed: {exc}"
                raise McpError(f"server {self.server.name} not writable{self._diag()}") from exc

    def _notify(self, method: str, params: dict | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def _request(self, method: str, params: dict | None, timeout: float) -> dict:
        if self._broken is not None:
            raise McpError(f"server {self.server.name} unavailable: {self._broken}{self._diag()}")
        self._next_id += 1
        request_id = self._next_id
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

        deadline = _now() + timeout
        while True:
            remaining = deadline - _now()
            if remaining <= 0:
                self._broken = f"timeout waiting for {method}"
                raise McpError(f"{method} timed out after {timeout}s{self._diag()}")
            try:
                message = self._inbox.get(timeout=remaining)
            except queue.Empty:
                continue
            if message is None:
                self._broken = "server exited"
                code = self.proc.poll() if self.proc else None
                raise McpError(
                    f"server {self.server.name} exited (code={code}){self._diag()}"
                )
            if "id" not in message:
                continue  # 通知/日志，忽略
            if message["id"] != request_id:
                continue  # 旧请求的迟到响应
            if "error" in message:
                error = message["error"] or {}
                raise McpError(
                    f"{method} failed: {error.get('code')} {error.get('message')}"
                )
            return message.get("result") or {}

    def initialize(self, timeout: float = 15.0) -> dict:
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "vibe", "version": "0.1.0"},
            },
            timeout,
        )
        self._notify("notifications/initialized")
        return result

    def list_tools(self, timeout: float = 15.0) -> list[dict]:
        tools: list[dict] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params, timeout)
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict, timeout: float) -> tuple[bool, str]:
        result = self._request(
            "tools/call", {"name": name, "arguments": arguments}, timeout
        )
        return not result.get("isError"), _render_result(result)


def _now() -> float:
    return time.monotonic()


def _render_result(result: dict) -> str:
    parts: list[str] = []
    for item in result.get("content") or []:
        kind = item.get("type")
        if kind == "text":
            parts.append(str(item.get("text", "")))
        elif kind == "resource":
            resource = item.get("resource") or {}
            parts.append(str(resource.get("text") or resource.get("uri") or ""))
        else:
            parts.append(json.dumps(item, ensure_ascii=False))
    if not parts:
        structured = result.get("structuredContent")
        parts.append(json.dumps(structured, ensure_ascii=False) if structured else "(empty)")
    text = "\n".join(p for p in parts if p != "")
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n...[truncated]"
    return text or "(empty)"


def _slug(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return cleaned or "x"


class McpManager:
    """管理多个 MCP server，并把它们的工具注册进 ToolRegistry。"""

    def __init__(self, servers: dict[str, McpServer], root: str | Path):
        self.servers = servers
        self.root = Path(root).resolve()
        self.clients: dict[str, StdioClient] = {}
        self.tools: dict[str, tuple[str, str]] = {}
        self.errors: list[str] = []

    @classmethod
    def from_config(cls, raw: dict[str, Any] | None, root: str | Path) -> "McpManager":
        return cls(parse_servers(raw), root)

    @classmethod
    def from_env(cls, config_file: str, inline: str, root: str | Path) -> "McpManager":
        return cls(parse_servers(load_servers(config_file or None, inline or None)), root)

    def register_into(self, registry: ToolRegistry) -> list[str]:
        for name, server in self.servers.items():
            client = StdioClient(server, self.root)
            try:
                client.start()
                client.initialize()
                remote_tools = client.list_tools()
            except Exception as exc:
                self.errors.append(f"{name}: {type(exc).__name__}: {exc}")
                client.close()
                continue
            self.clients[name] = client
            for tool in remote_tools:
                remote_name = str(tool.get("name") or "").strip()
                if not remote_name:
                    continue
                exposed = f"mcp_{_slug(name)}_{_slug(remote_name)}"
                registry.register(
                    ToolSpec(
                        name=exposed,
                        description=str(tool.get("description") or f"MCP {name}:{remote_name}"),
                        parameters=_schema(tool.get("inputSchema")),
                        fn=self._make_fn(client, remote_name),
                        timeout=server.timeout,
                        risk=server.risk,
                    )
                )
                self.tools[exposed] = (name, remote_name)
        return sorted(self.tools)

    @staticmethod
    def _make_fn(client: StdioClient, remote_name: str):
        def fn(**kwargs):
            ok, text = client.call_tool(remote_name, kwargs, client.server.timeout)
            if not ok:
                raise McpToolError(text)
            return text

        return fn

    def close(self) -> None:
        for client in self.clients.values():
            client.close()
        self.clients.clear()


def _schema(value: Any) -> dict:
    if isinstance(value, dict) and value.get("type") == "object":
        return value
    return {"type": "object", "properties": {}}
