"""单段限制的默认值、保存和运行快照。"""
from __future__ import annotations

import json
import math
from contextlib import contextmanager

from app.runtime.model_config import LIMIT_NAMES, save_limit_config
from app.runtime.model_settings import ModelSettings, ModelSettingsError

FIELDS = {"max_steps": "VIBE_MAX_STEPS", "max_run_seconds": "VIBE_MAX_RUN_SECONDS",
          "max_run_tokens": "VIBE_MAX_RUN_TOKENS", "max_run_cost_usd": "VIBE_MAX_RUN_COST_USD"}


class LimitSettingsError(ModelSettingsError):
    pass


def validate_limits(values, *, snapshot=False):
    if not isinstance(values, dict) or set(values) != set(FIELDS):
        raise LimitSettingsError("请提供步数、秒数、token 和估算费用四项限制。")
    for field, value in values.items():
        if type(value) not in (int, float) or not 0 <= value <= 1_000_000_000_000 or not math.isfinite(value):
            raise LimitSettingsError("限制必须为有限非负数，且不超过 10¹²。")
        if field in {"max_steps", "max_run_tokens"} and (not isinstance(value, int) or isinstance(value, bool)):
            raise LimitSettingsError("步数和 token 必须为整数。")
    if not snapshot and values["max_run_seconds"] <= 0:
        raise LimitSettingsError("时长必须大于 0；0 不表示无限。")
    return dict(values)


class LimitSettings(ModelSettings):
    fields = FIELDS
    writer = staticmethod(save_limit_config)
    label = "单段默认限制"

    def __init__(self, config, agent, **kwargs):
        super().__init__(config, agent, **kwargs)
        try:
            names = json.loads(self.env.get("VIBE_SETTINGS_ENV_KEYS", "null"))
            self.env_keys = set(names) if isinstance(names, list) and all(isinstance(x, str) for x in names) else set(self.env)
        except (ValueError, TypeError):
            self.env_keys = set(self.env)
        self.initial = {key: getattr(agent, key) for key in FIELDS}
        self.active_limits = None

    def values(self):
        return {key: getattr(self.config, key) for key in FIELDS}

    def defaults(self):
        return self.values() if self.applied else dict(self.initial)

    def sources(self):
        return {key: "CLI" if key in getattr(self.config, "limit_cli_fields", ()) else
                "进程环境" if env in self.env_keys else "本次选择" if self.applied else
                self.loaded_sources.get(env, "用户配置" if self.path.suffix == ".toml" else ".env") if self.env.get("VIBE_CONFIG_FILE") and env in self.env else "默认值"
                for key, env in FIELDS.items()}

    def overview(self, mcp=None):
        connected = sum(1 for client in getattr(mcp, "clients", {}).values()
                        if not getattr(client, "broken", None) and getattr(client, "client", True) is not None)
        return {"limits": self.values(), "sources": self.sources(), "path": str(self.path),
                "revision": self.revision(), "advanced": {
                    "mcp_servers": len(getattr(mcp, "servers", {})), "mcp_connected": connected,
                    "sandbox": bool(self.config.sandbox), "sandbox_network": bool(self.config.sandbox_network),
                    "notifications": bool(self.env.get("VIBE_NOTIFY_WEBHOOK")),
                    "priced": bool(self.config.input_price_per_1m or self.config.output_price_per_1m),
                }}

    def candidate(self, args):
        if not isinstance(args, dict) or set(args) - {"action", "limits", "revision"}:
            raise LimitSettingsError("设置参数无效。")
        return validate_limits(args.get("limits"))

    def apply(self, args):
        candidate = self.candidate(args)
        sources = self.sources()
        blocked = []
        for key, value in candidate.items():
            if sources[key] in {"CLI", "进程环境"}:
                blocked.append(key)
            else:
                setattr(self.config, key, value)
        self.applied = True
        return "已应用为后续新任务的单段默认；旧任务保持快照，后台进程不变。" + (
            " 仍受 CLI/环境覆盖：" + "、".join(blocked) if blocked else "")

    async def save(self, args):
        candidate = self.candidate(args)
        revision = args.get("revision")
        def check():
            if self.path.is_symlink():
                raise LimitSettingsError("配置文件是符号链接，请手动管理。")
            if not isinstance(revision, str) or revision != self.revision():
                raise LimitSettingsError("配置文件已变化，请刷新后核对；编辑保留。")
        check()
        await self.permit("write_file", {"path": str(self.path), "keys": sorted(self.fields.values())},
                          "保存" + self.label + "到 " + str(self.path) + "\n" + json.dumps(candidate, ensure_ascii=False)
                          + "\n当前任务和默认值不会自动改变。")
        check()
        try:
            self.writer(self.path, {self.fields[k]: str(v) for k, v in candidate.items()})
        except (ValueError, OSError):
            raise LimitSettingsError("保存失败；原文件和当前默认值保持不变。") from None
        return "已保存" + self.label + "；当前默认未应用，重启或单独应用后生效。"

    @staticmethod
    def task_snapshot(path, task_id):
        if not path.exists():
            return None
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("task_id") == task_id and "run_limits" in event:
                return validate_limits(event["run_limits"], snapshot=True)
        return None

    @contextmanager
    def for_run(self, limits):
        chosen = dict(limits)
        before = {key: getattr(self.agent, key) for key in FIELDS}
        previous = self.active_limits
        try:
            self.active_limits = chosen
            for key, value in chosen.items():
                setattr(self.agent, key, value)
            yield
        finally:
            for key, value in before.items():
                setattr(self.agent, key, value)
            self.active_limits = previous
