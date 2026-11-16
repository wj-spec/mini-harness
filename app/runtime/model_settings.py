"""模型设置边界：来源、凭据、保存、检测与恢复核对。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.runtime.model_config import KEY_NAMES, BASE_NAMES, load_environment, save_model_config, usable_key
from app.providers.openai_compat import chat_completions_url
from app.runtime.user_config import config_path, credential_path, configuration_revision
from app.safety.policy import ApprovalGate
from core.contracts import ToolCall

FIELDS = {"model": ("VIBE_MODEL",), "base_url": BASE_NAMES,
          "api_key": KEY_NAMES, "trust_env": ("VIBE_TRUST_ENV",),
          "provider": ("VIBE_PROVIDER",)}


class ModelSettingsError(ValueError):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


def safe_url(value):
    try:
        url = urlsplit(value)
        return f"{url.scheme}://{url.hostname or ''}{':' + str(url.port) if url.port else ''}{url.path}"
    except ValueError:
        return "地址格式无效"


class ModelSettings:
    def __init__(self, config, agent=None, *, path=None, environ=None, approval=None):
        self.config, self.agent = config, agent
        # 仅配置状态没有运行中的 Agent；保存/检测只需要一个审批闸。
        self.approval = approval if approval is not None else getattr(agent, "approval", None)
        if self.approval is None:
            self.approval = ApprovalGate()  # 无审批处理器时 fail closed
        self.env = dict(os.environ if environ is None else environ)
        self.path = Path(path) if path else config_path(self.env)
        try:
            self.loaded_sources = json.loads(self.env.get("VIBE_CONFIG_SOURCES", "{}"))
        except ValueError:
            self.loaded_sources = {}
        try:
            inherited = json.loads(self.env.get("VIBE_MODEL_ENV_KEYS", "null"))
            self.env_keys = set(inherited) if isinstance(inherited, list) and all(isinstance(x, str) for x in inherited) else set(self.env)
        except (ValueError, TypeError):
            self.env_keys = set(self.env)
        self.applied = False

    def revision(self):
        try:
            return configuration_revision(self.path)
        except OSError:
            raise ModelSettingsError("配置文件不可读取；当前模型保持不变。") from None

    def sources(self):
        cli = getattr(self.config, "model_cli_fields", ())
        try:
            saved = load_environment(self.path, {})
        except (OSError, ValueError):
            saved = {}
        result = {}
        for field, names in FIELDS.items():
            if field in cli:
                result[field] = "CLI"
            elif any(key in self.env_keys for key in names):
                result[field] = "进程环境"
            elif self.applied:
                result[field] = "本次选择"
            elif any(name in self.loaded_sources for name in names):
                result[field] = next(self.loaded_sources[name] for name in names if name in self.loaded_sources)
            elif self.path.suffix != ".toml" and any(name in saved for name in names):
                result[field] = ".env"
            else:
                result[field] = "默认值"
        return result

    def overview(self):
        try:
            saved = load_environment(self.path, {})
            saved_model = saved.get("VIBE_MODEL", "")
        except (OSError, ValueError):
            saved_model = "文件无法解析"
        return {"model": self.config.model, "base_url": safe_url(self.config.base_url),
                "trust_env": self.config.trust_env, "key_present": bool(usable_key(self.config.api_key)),
                "provider": self.provider_name(), "sources": self.sources(),
                "path": str(self.path), "revision": self.revision(),
                "saved_model": saved_model, "applied": self.applied}

    def provider_name(self):
        provider = getattr(self.agent, "provider", None)
        return "fake" if type(provider).__name__ == "FakeProvider" else self.config.provider

    def candidate(self, values):
        if not isinstance(values, dict) or set(values) - {"action", "model", "base_url", "api_key", "trust_env", "revision"}:
            raise ModelSettingsError("模型配置字段无效。")
        for field in ("model", "base_url", "api_key"):
            value = values.get(field)
            if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ModelSettingsError("模型配置必须是长度有限的单行文本。", field)
        if type(values.get("trust_env")) is not bool:
            raise ModelSettingsError("代理选项必须为开或关。", "trust_env")
        model, base = values["model"].strip(), values["base_url"].strip().rstrip("/")
        try:
            parsed = urlsplit(base)
            valid = parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
            parsed.port
        except ValueError:
            valid = False
        if not model:
            raise ModelSettingsError("请填写模型名。", "model")
        if not valid:
            raise ModelSettingsError("服务地址需要是 http(s) URL，不能包含凭据、查询或片段。", "base_url")
        key = values["api_key"].strip()
        if key and not usable_key(key):
            raise ModelSettingsError("请填写真实 API Key；占位值不会保存。", "api_key")
        if not key and base != self.config.base_url.rstrip("/"):
            raise ModelSettingsError("服务地址变化，请重新填写该服务的 API Key。", "api_key")
        key = key or usable_key(self.config.api_key)
        if not key:
            raise ModelSettingsError("请填写 API Key。", "api_key")
        return {"model": model, "base_url": base, "api_key": key, "trust_env": values["trust_env"]}

    def effective(self, values):
        result = self.candidate(values)
        sources = self.sources()
        for field in result:
            if sources[field] in {"CLI", "进程环境"}:
                result[field] = getattr(self.config, field)
        if result["base_url"] != self.config.base_url and sources["api_key"] == "进程环境":
            raise ModelSettingsError("进程环境固定了 Key；请先取消覆盖再切换服务地址。")
        return result

    async def permit(self, name, args, preview):
        decision = await self.approval.request(ToolCall(name, args), preview, self.config.workspace)
        if not decision.allowed:
            raise ModelSettingsError("操作未获允许；当前配置保持不变。")

    async def save(self, values):
        candidate = self.candidate(values)
        expected = values.get("revision")
        def check():
            if self.path.is_symlink():
                raise ModelSettingsError("配置文件是符号链接，请手动管理。")
            if not isinstance(expected, str) or expected != self.revision():
                raise ModelSettingsError("配置文件已变化，请刷新后重新核对；草稿保留。")
        check()
        targets = [str(self.path)]
        if self.path.suffix == ".toml":
            targets.append(str(credential_path(self.path)))
        await self.permit("write_file", {"path": str(self.path), "paths": targets, "keys": ["VIBE_MODEL", "VIBE_BASE_URL", "VIBE_API_KEY", "VIBE_TRUST_ENV"]},
                          f"保存用户配置（所有项目通用）到 {', '.join(targets)}；模型 {candidate['model']}；服务 {candidate['base_url']}；Key 隐藏。当前模型不会切换。")
        if self.path.suffix == ".toml":
            await self.permit("write_file", {"path": str(credential_path(self.path))},
                              f"保存独立凭据到 {credential_path(self.path)}；仅当前用户可读写，绑定当前服务，Key 隐藏。")
        check()
        try:
            save_model_config(self.path, {"VIBE_PROVIDER": "openai", "VIBE_MODEL": candidate["model"],
                "VIBE_BASE_URL": candidate["base_url"], "VIBE_API_KEY": candidate["api_key"],
                "VIBE_TRUST_ENV": "1" if candidate["trust_env"] else "0"})
        except (OSError, ValueError):
            raise ModelSettingsError("保存失败；原配置与当前模型保持不变，请检查文件权限。") from None
        return "已保存；进入项目后生效。" if self.agent is None else "已保存；当前模型未切换。"

    async def probe(self, values, *, transport=None):
        candidate = self.candidate(values)
        await self.permit("model_connection_test", {"url": candidate["base_url"], "model": candidate["model"]},
                          "连接检测可能计费：向所填服务发送固定 Hi 消息，最多请求 1 个输出 token；不发送会话或代码。")
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=candidate["trust_env"], transport=transport, follow_redirects=False) as client:
                response = await client.post(chat_completions_url(candidate["base_url"]),
                    headers={"Authorization": "Bearer " + candidate["api_key"]},
                    json={"model": candidate["model"], "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1, "stream": False})
            if response.status_code != 200:
                hint = {
                    401: "凭据无效或缺少访问权限；请核对 Key。",
                    403: "访问被拒绝；请核对 Key 权限。",
                    404: "服务或模型不存在；请核对地址与模型名。",
                    429: "请求过多；请稍后重试，不必立即更换 Key。",
                }.get(response.status_code, "")
                return f"检测未通过：HTTP {response.status_code}；{hint}未保存或应用。"
            data = response.json()
            if (not isinstance(data, dict) or not isinstance(data.get("choices"), list) or not data["choices"]
                    or not isinstance(data["choices"][0], dict) or not isinstance(data["choices"][0].get("message"), dict)
                    or not isinstance(data["choices"][0]["message"].get("content"), str)):
                return "服务返回格式不符合预期；未保存或应用。"
            return "连接检测通过；只验证最小请求，不代表工具调用或模型能力完整支持。"
        except (httpx.HTTPError, ValueError):
            return "连接检测失败：网络、超时或响应格式异常；未保存或应用。"

    def snapshot(self):
        return {"provider": self.provider_name(), "model": self.config.model,
                "base_url": safe_url(self.config.base_url), "trust_env": self.config.trust_env,
                "credential_ref": "runtime-environment"}

    def check_snapshot(self, snapshot):
        if snapshot is not None and snapshot != self.snapshot():
            expected = ""
            if isinstance(snapshot, dict):
                expected = f"原模型：{str(snapshot.get('model', '?'))[:160]}；服务：{safe_url(str(snapshot.get('base_url', '')))}；环境代理：{'开' if snapshot.get('trust_env') else '关'}。"
            raise ModelSettingsError("原任务模型配置与当前不同；" + expected + "请在 /model 恢复后续做，或输入新任务。")

    def apply(self, values):
        from app.providers.openai_compat import OpenAICompatProvider
        from app.runtime.summarize import LLMSummarizer
        if self.agent is None:
            raise ModelSettingsError("配置准备阶段不能切换模型；进入项目后生效。")
        if not isinstance(self.agent.provider, OpenAICompatProvider) or getattr(self.agent, "llm_auditor", None):
            raise ModelSettingsError("Demo、自定义 provider 或额外审计客户端需保存后重启生效；当前模型未切换。")
        candidate = self.effective(values)
        provider = OpenAICompatProvider(**candidate, timeout=self.config.llm_timeout, stream_usage=self.config.stream_usage,
            debug_path=self.agent.provider.debug_path, max_output_tokens=self.agent.provider.max_output_tokens)
        summarizer = LLMSummarizer(model=self.config.summary_model or candidate["model"],
            api_key=candidate["api_key"], base_url=candidate["base_url"],
            max_chars=self.config.summary_max_chars, trust_env=candidate["trust_env"])
        self.agent.provider = provider
        if self.agent.context is not None:
            self.agent.context.summarizer = summarizer
        for field, value in candidate.items():
            setattr(self.config, field, value)
        self.applied = True
        overridden = [field for field in candidate if self.sources()[field] in {"CLI", "进程环境"}]
        note = " 仍受 CLI/环境覆盖：" + "、".join(overridden) + "。" if overridden else ""
        return "已应用到后续新任务；未保存则重启失效。旧任务续做仍需匹配原配置，后台进程不变。" + note
