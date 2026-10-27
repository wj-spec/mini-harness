"""跑分 adapter 的纯逻辑：模型解析、环境映射、telemetry 读取。

不 import harbor，便于离线单元测试；bench/adapter.py 负责 Harbor 类。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

PROVIDERS = {"openai", "deepseek"}
DEFAULT_MODELS = {"openai": "gpt-4.1", "deepseek": "deepseek-chat"}
DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
}
DEFAULT_API_KEY_ENVS = {"openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}


def split_model_name(model_name: str | None) -> tuple[str | None, str]:
    """把 `provider/model` 拆开；没有前缀时返回 (None, model)。"""
    name = (model_name or "").strip()
    if not name:
        return None, ""
    prefix, sep, rest = name.partition("/")
    if sep and prefix in PROVIDERS and rest:
        return prefix, rest
    return None, name


def resolve_agent_settings(
    settings: dict[str, str],
    *,
    model_name: str | None = None,
    provider: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    max_steps: int | None = None,
    max_run_seconds: float | None = None,
) -> dict:
    """从 Harbor 参数 + 宿主环境得到 provider/model/key/base_url 与子进程环境。"""
    settings = {key: value for key, value in settings.items() if isinstance(value, str)}
    prefix, bare = split_model_name(model_name)
    model = bare or settings.get("VIBE_BENCH_MODEL") or settings.get("VIBE_MODEL") or ""
    inferred = prefix or ("deepseek" if model.lower().startswith("deepseek") else "openai")
    provider = (provider or settings.get("VIBE_BENCH_PROVIDER") or inferred).lower()
    if provider not in PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    if prefix is not None and prefix != provider:
        raise ValueError("model prefix conflicts with provider")
    model = model or DEFAULT_MODELS[provider]
    if not model.strip():
        raise ValueError("model must not be empty")

    key_env = (
        api_key_env
        or settings.get("VIBE_BENCH_API_KEY_ENV")
        or DEFAULT_API_KEY_ENVS[provider]
    )
    api_key = settings.get("VIBE_BENCH_API_KEY") or settings.get(key_env)
    if not api_key:
        raise RuntimeError(f"set {key_env} (or VIBE_BENCH_API_KEY) before running the benchmark")

    resolved_base = (
        base_url
        or settings.get("VIBE_BENCH_BASE_URL")
        or settings.get("VIBE_BASE_URL")
        or (settings.get("OPENAI_BASE_URL") if provider == "openai" else "")
        or DEFAULT_BASE_URLS[provider]
    )
    steps = int(max_steps if max_steps is not None else settings.get("VIBE_BENCH_MAX_STEPS", 300))
    wall = float(
        max_run_seconds if max_run_seconds is not None else settings.get("VIBE_BENCH_MAX_RUN_SECONDS", 1500)
    )
    if steps <= 0 or wall <= 0:
        raise ValueError("max_steps and max_run_seconds must be positive")

    env = {
        "VIBE_PROVIDER": provider,
        "VIBE_MODEL": model,
        "VIBE_BASE_URL": resolved_base,
        "VIBE_MAX_STEPS": str(steps),
        "VIBE_MAX_RUN_SECONDS": str(wall),
        "VIBE_STREAM_USAGE": "1",
    }
    for source, target in (
        ("VIBE_BENCH_COMPACT_INTERVAL_TOKENS", "VIBE_COMPACT_INTERVAL_TOKENS"),
        ("VIBE_BENCH_MAX_RUN_TOKENS", "VIBE_MAX_RUN_TOKENS"),
        ("VIBE_BENCH_LLM_TIMEOUT", "VIBE_LLM_TIMEOUT"),
    ):
        value = settings.get(source)
        if value:
            env[target] = value
    return {
        "provider": provider,
        "model": model,
        "base_url": resolved_base,
        "api_key": api_key,
        "api_key_env": key_env,
        "max_steps": steps,
        "max_run_seconds": wall,
        "env": env,
    }


def read_telemetry(path: str | Path) -> dict:
    """读取并校验一个任务写入的 telemetry JSON。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("telemetry must be an object")
    if not isinstance(data.get("status"), str) or not data["status"]:
        raise ValueError("telemetry.status must be a non-empty string")
    if not isinstance(data.get("usage"), dict):
        raise ValueError("telemetry.usage must be an object")
    return data


def agent_version(repo_root: str | Path) -> str:
    """宿主仓库的短 commit；仅用于记录，不影响运行。"""
    override = os.getenv("VIBE_BENCH_AGENT_VERSION", "").strip()
    if override:
        return override
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        version = out.stdout.strip()
        return version if out.returncode == 0 and version else "unknown"
    except Exception:
        return "unknown"
