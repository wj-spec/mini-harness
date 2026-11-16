"""MCP 配置编辑；保存与连接生命周期分离。"""
from __future__ import annotations

from dataclasses import asdict
import math
import hashlib
import json
import os
import tempfile
from pathlib import Path

from app.runtime.model_config import load_environment, save_mcp_config
from app.runtime.model_settings import ModelSettings, ModelSettingsError
from app.tools.mcp import parse_servers


ADVANCED = {"cwd", "timeout", "risk", "allowed_tools", "tool_risks", "allowed_origins", "browser_actions"}


def advanced_view(server):
    data = asdict(server)
    return {key: (data[key] or "") if key == "cwd" else list(data[key]) if isinstance(data[key], tuple) else data[key]
            for key in ADVANCED}


class McpSettingsError(ModelSettingsError):
    pass


class McpSettings(ModelSettings):
    def __init__(self, config, agent, **kwargs):
        super().__init__(config, agent, **kwargs)
        self.file_source = bool(config.mcp_config)
        if self.file_source:
            self.path = Path(config.mcp_config).absolute()
        try:
            names = json.loads(self.env.get("VIBE_SETTINGS_ENV_KEYS", "null"))
            inherited = set(names) if isinstance(names, list) and all(isinstance(n, str) for n in names) else set(self.env)
        except (ValueError, TypeError):
            inherited = set(self.env)
        self.overridden = not self.file_source and "VIBE_MCP_SERVERS" in inherited

    def document(self):
        try:
            if self.file_source:
                text = self.path.read_text(encoding="utf-8") if self.path.exists() else "{}"
            elif self.overridden:
                text = self.config.mcp_servers or "{}"
            else:
                saved = load_environment(self.path, {})
                if saved.get("VIBE_MCP_CONFIG"):
                    raise McpSettingsError(".env 中的 MCP 文件来源已变化，请重启后编辑。")
                text = saved.get("VIBE_MCP_SERVERS", self.config.mcp_servers) or "{}"
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError()
            key = next((k for k in ("servers", "mcpServers") if k in data), None)
            servers = data[key] if key else data
            parse_servers(servers)
            if not isinstance(servers, dict):
                raise ValueError()
            return data, servers
        except McpSettingsError:
            raise
        except (OSError, ValueError, TypeError):
            raise McpSettingsError("MCP 配置无法读取或格式无效；请检查原文件，内容不会回显。") from None

    def overview(self, manager=None):
        _, raw = self.document()
        servers = parse_servers(raw)
        clients = getattr(manager, "clients", {})
        views = []
        for name in dict.fromkeys([*servers, *clients]):
            client = clients.get(name)
            server = servers.get(name) or client.server
            active = getattr(client, "server", None)
            connected = client is not None and not getattr(client, "broken", None) and getattr(client, "client", True) is not None
            views.append({"name": name, "command": server.command, "args_count": len(server.args),
                          "env_keys": sorted(server.env), "connected": connected, "configured": name in servers,
                          "changed": connected and (name not in servers or active != server),
                          "advanced": advanced_view(server)})
        return {"path": str(self.path), "revision": self.revision(),
                "source": "JSON 文件" if self.file_source else "进程环境" if self.overridden else self.loaded_sources.get("VIBE_MCP_SERVERS", "用户配置" if self.path.suffix == ".toml" else ".env"),
                "editable": not self.overridden and not self.path.is_symlink(), "servers": views}

    @staticmethod
    def validate(args):
        if not isinstance(args, dict):
            raise McpSettingsError("MCP 操作参数无效。")
        action = args.get("action")
        if not isinstance(action, str):
            raise McpSettingsError("MCP 操作无效。")
        if action in {"reload", "disconnect"} and set(args) == {"action", "name"}:
            if not isinstance(args["name"], str) or not args["name"].strip() or len(args["name"]) > 4096:
                raise McpSettingsError("请明确选择服务。")
            return
        if action in {"list", "reload", "disconnect"} and set(args) == {"action"}:
            return
        allowed = {"action", "name", "revision"} | ({"command", "args", "env"} if action == "save" else set())
        if action == "save" and "advanced" in args:
            allowed.add("advanced")
        if action not in {"save", "delete"} or set(args) != allowed:
            raise McpSettingsError("MCP 编辑字段无效。")
        for key in allowed - {"action", "advanced"}:
            text = args[key]
            limit = 32768 if key in {"args", "env"} else 4096
            if not isinstance(text, str) or len(text) > limit or any(ord(c) < 32 or ord(c) == 127 for c in text):
                raise McpSettingsError("MCP 编辑值必须为长度有限的单行文本。")
        if "advanced" in args:
            data = args["advanced"]
            if not isinstance(data, dict) or set(data) != ADVANCED:
                raise McpSettingsError("高级配置字段不完整。")
            if (not isinstance(data["cwd"], str) or len(data["cwd"]) > 4096
                    or any(ord(c) < 32 or ord(c) == 127 for c in data["cwd"])
                    or type(data["timeout"]) not in (int, float) or not math.isfinite(data["timeout"])
                    or not 0 < data["timeout"] <= 86400):
                raise McpSettingsError("工作目录需为单行路径；超时需在 0 到 86400 秒之间。")
            try:
                parse_servers({"check": {"command": "placeholder", **data}})
            except (ValueError, TypeError):
                raise McpSettingsError("高级配置无效：请核对风险、工具白名单、来源和浏览器约束。") from None
        if not args["name"].strip() or (action == "save" and not args["command"].strip()):
            raise McpSettingsError("请填写服务名称与启动命令。")

    async def save(self, args):
        self.validate(args)
        if args["action"] not in {"save", "delete"}:
            raise McpSettingsError("MCP 保存操作无效。")
        def check():
            if self.overridden:
                raise McpSettingsError("MCP 由进程环境覆盖；请移除覆盖并重启后编辑。")
            if self.path.is_symlink():
                raise McpSettingsError("配置文件是符号链接，请手动管理。")
            if args["revision"] != self.revision():
                raise McpSettingsError("配置文件已变化，请刷新后核对；草稿保留。")
        check()
        data, servers = self.document()
        name = args["name"]
        if data is servers and name in {"servers", "mcpServers"} and args["action"] == "save":
            data = {"mcpServers": servers}
        if args["action"] == "delete":
            if name not in servers:
                raise McpSettingsError("服务已不存在，请刷新核对。")
            del servers[name]
        else:
            old = servers.get(name, {})
            candidate = dict(old)
            candidate["command"] = args["command"]
            if "advanced" in args:
                candidate.update(args["advanced"])
            try:
                candidate["args"] = json.loads(args["args"]) if args["args"] else old.get("args", [])
                candidate["env"] = json.loads(args["env"]) if args["env"] else old.get("env", {})
                parse_servers({name: candidate})
            except (ValueError, TypeError):
                raise McpSettingsError("参数需为 JSON 字符串数组，环境需为 JSON 字符串对象；请核对格式。") from None
            if (old.get("env") and not args["env"] and any(candidate.get(k) != old.get(k, []) for k in ("command", "args")) or
                    (old.get("env") and not args["env"] and (candidate.get("cwd") or "") != (old.get("cwd") or ""))):
                raise McpSettingsError("启动命令、参数或工作目录变化，请重新填写环境变量；输入 {} 可清空。")
            servers[name] = candidate
        content = json.dumps(data, ensure_ascii=False)
        await self.permit("write_file", {"path": str(self.path), "operation": args["action"],
                          "server": name, "content_hash": hashlib.sha256(content.encode()).hexdigest()},
                          f"保存 MCP 配置到 {self.path}；服务 {name}；操作 {args['action']}。参数和环境值隐藏。"
                          "保存不连接、不执行命令；当前工具保持不变。")
        check()
        try:
            if self.file_source:
                temporary = None
                try:
                    fd, temporary = tempfile.mkstemp(prefix=".mcp.", dir=self.path.parent)
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        os.fchmod(handle.fileno(), 0o600)
                        handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, self.path)
                    temporary = None
                finally:
                    if temporary:
                        Path(temporary).unlink(missing_ok=True)
            else:
                save_mcp_config(self.path, content)
                self.config.mcp_servers = content
        except (OSError, ValueError):
            raise McpSettingsError("保存失败；原文件与当前连接保持不变。") from None
        return "已保存 MCP 配置；当前连接未改变，重载后生效。"
