"""共享模型配置读写；支持用户 TOML 与旧 .env，不执行 shell。"""
from __future__ import annotations

import os
import re
import shlex
import tempfile
from pathlib import Path
from typing import Mapping

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
KEY_NAMES = ("VIBE_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")
BASE_NAMES = ("VIBE_BASE_URL", "OPENAI_BASE_URL")
# 模板/示例占位值不算配置；与 scripts/doctor.py 的同名常量由测试保证一致。
API_KEY_PLACEHOLDERS = (
    "sk-your", "your-key", "your_api_key", "changeme", "xxx",
    "example", "dummy", "placeholder",
)
MODEL_NAMES = {*KEY_NAMES, *BASE_NAMES, "VIBE_MODEL", "VIBE_PROVIDER", "VIBE_TRUST_ENV"}
GOAL_NAMES = {"VIBE_GOAL_MAX_SEGMENTS", "VIBE_GOAL_SECONDS", "VIBE_GOAL_MAX_TOKENS", "VIBE_GOAL_MAX_COST_USD"}
LIMIT_NAMES = {"VIBE_MAX_STEPS", "VIBE_MAX_RUN_SECONDS", "VIBE_MAX_RUN_TOKENS", "VIBE_MAX_RUN_COST_USD"}
_ASSIGNMENT = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=(.*)$")


def is_placeholder_key(value: str) -> bool:
    """模板 / 示例里的占位 key 不算已配置（大小写不敏感）。"""
    lowered = (value or "").strip().lower()
    return any(marker in lowered for marker in API_KEY_PLACEHOLDERS)


def usable_key(value: str) -> str:
    """返回可用的 Key；空值或占位值视为未配置。"""
    text = (value or "").strip()
    return "" if not text or is_placeholder_key(text) else text


def configured_key(env: Mapping[str, str]) -> str:
    """按 Config 优先级取第一个可用的 Key；占位值继续向后找。"""
    for name in KEY_NAMES:
        key = usable_key(env.get(name, ""))
        if key:
            return key
    return ""


def load_environment(path: Path, environ: Mapping[str, str]) -> dict[str, str]:
    if path.suffix == ".toml":
        from app.runtime.user_config import read_user_environment, merge, connection, KEYS
        saved = read_user_environment(path)
        result = merge(saved, environ)
        if connection(result) != connection(saved) and not any(k in environ for k in KEYS):
            for key in KEYS:
                result.pop(key, None)
        return result
    result = dict(environ)
    if not path.exists():
        return result
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ASSIGNMENT.fullmatch(line)
        if not match:
            raise ValueError(f".env 第 {number} 行不是 KEY=value；不会执行 shell 语句。")
        name, value = match.groups()
        if not (name.startswith("VIBE_") or name in {*KEY_NAMES, *BASE_NAMES}):
            continue
        try:
            parts = shlex.split(value, comments=True, posix=True)
        except ValueError:
            raise ValueError(f".env 第 {number} 行引号不完整。") from None
        if len(parts) > 1:
            raise ValueError(f".env 第 {number} 行的值含空格，请加引号。")
        values[name] = parts[0] if parts else ""
    for group in (KEY_NAMES, BASE_NAMES):
        if any(name in environ for name in group):
            for name in group:
                values.pop(name, None)
    for name, value in values.items():
        result.setdefault(name, value)
    return result


def save_model_config(path: Path, values: Mapping[str, str]) -> None:
    """调用方先过 ApprovalGate；只替换模型项，原子保存且仅当前用户可读写。"""
    if path.is_symlink():
        raise ValueError(".env 是符号链接，请手动管理配置。")
    if not values.keys() <= MODEL_NAMES:
        raise ValueError("只能保存模型配置项。")
    if any("\n" in value or "\r" in value or "\0" in value for value in values.values()):
        raise ValueError("模型配置必须是单行文本。")
    _save_values(path, values, MODEL_NAMES if "VIBE_TRUST_ENV" in values else MODEL_NAMES - {"VIBE_TRUST_ENV"})


def save_limit_config(path: Path, values: Mapping[str, str]) -> None:
    if path.is_symlink() or set(values) != LIMIT_NAMES:
        raise ValueError("限制配置目标或字段无效。")
    if any("\n" in value or "\r" in value or "\0" in value for value in values.values()):
        raise ValueError("限制必须为单行数值。")
    _save_values(path, values, LIMIT_NAMES)


def save_goal_config(path: Path, values: Mapping[str, str]) -> None:
    if path.is_symlink() or set(values) != GOAL_NAMES:
        raise ValueError("目标预算配置目标或字段无效。")
    if any("\n" in value or "\r" in value or "\0" in value for value in values.values()):
        raise ValueError("目标预算必须为单行数值。")
    _save_values(path, values, GOAL_NAMES)


def _save_values(path: Path, values: Mapping[str, str], replace_keys: set[str]) -> None:
    if path.suffix == ".toml":
        from app.runtime.user_config import save_user_values
        save_user_values(path, values, replace_keys)
        return
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = []
    for line in old.splitlines():
        match = _ASSIGNMENT.fullmatch(line.strip())
        if match and match.group(1) in replace_keys:
            continue
        lines.append(line)
    if lines and lines[-1]:
        lines.append("")
    lines.extend(f"{name}={shlex.quote(value)}" for name, value in values.items())
    content = "\n".join(lines) + "\n"
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".env.", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def save_mcp_config(path: Path, content: str) -> None:
    if path.is_symlink() or any(c in content for c in ("\n", "\r", "\0")):
        raise ValueError("MCP 配置目标或内容无效。")
    _save_values(path, {"VIBE_MCP_SERVERS": content}, {"VIBE_MCP_SERVERS"})
