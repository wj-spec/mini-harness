"""用户配置、项目覆盖和独立凭据；不扫描项目 .env。"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import tomllib
import uuid
from pathlib import Path
from typing import Mapping

FIELDS = {"model": "VIBE_MODEL", "provider": "VIBE_PROVIDER", "base_url": "VIBE_BASE_URL",
          "trust_env": "VIBE_TRUST_ENV"}
KEYS = ("VIBE_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")
BASES = ("VIBE_BASE_URL", "OPENAI_BASE_URL")


def config_path(env: Mapping[str, str]) -> Path:
    if env.get("VIBE_CONFIG_FILE"):
        return Path(env["VIBE_CONFIG_FILE"]).expanduser().absolute()
    home = Path(env.get("HOME") or Path.home())
    directory = env.get("XDG_CONFIG_HOME") or os.environ.get("XDG_CONFIG_HOME")
    base = Path(directory).expanduser() if directory else home / ".config"
    if not base.is_absolute():
        base = home / ".config"
    return base / "mini-harness" / "config.toml"


def credential_path(path: Path) -> Path:
    return path.parent / "credentials.json"


def read_document(path: Path, *, project=False) -> dict:
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError):
        raise ValueError(f"配置文件无法解析：{path}（TOML 格式错误）。") from None
    allowed = set(FIELDS) if project else {*FIELDS, "credential_id", "environment"}
    if set(data) - allowed:
        raise ValueError(f"配置含不支持的字段：{path}；项目配置只允许模型、服务与代理选项。")
    for key, value in data.items():
        if key == "environment":
            if not isinstance(value, dict) or any(not k.startswith("VIBE_") or k in KEYS or k in FIELDS.values()
                                                  or not isinstance(v, str) for k, v in value.items()):
                raise ValueError(f"高级配置字段无效：{path}。")
        elif key == "trust_env":
            if type(value) is not bool:
                raise ValueError(f"代理选项必须是布尔值：{path}。")
        elif not isinstance(value, str) or any(ord(c) < 32 for c in value):
            raise ValueError(f"配置字段必须是单行文本：{path}。")
    return data


def read_credentials(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(not isinstance(v, dict) or set(v) != {"provider", "base_url", "api_key"}
                                            or any(not isinstance(x, str) for x in v.values()) for v in data.values()):
            raise ValueError()
        return data
    except (ValueError, UnicodeError):
        raise ValueError(f"凭据文件无法解析：{path}；原文件未修改。") from None


def connection(values: Mapping[str, str]) -> tuple[str, str]:
    return values.get("VIBE_PROVIDER", "openai"), (values.get("VIBE_BASE_URL") or values.get("OPENAI_BASE_URL")
                                                   or "https://api.deepseek.com/v1").rstrip("/")


def document_environment(data: dict) -> dict[str, str]:
    values = dict(data.get("environment", {}))
    values.update({env: ("1" if data[key] else "0") if key == "trust_env" else data[key]
                   for key, env in FIELDS.items() if key in data})
    return values


def read_user_environment(path: Path) -> dict[str, str]:
    data = read_document(path)
    values = document_environment(data)
    credentials = read_credentials(credential_path(path))
    if data.get("credential_id"):
        values["VIBE_API_KEY"] = ""
    record = credentials.get(data.get("credential_id"))
    if record and (record["provider"], record["base_url"].rstrip("/")) == connection(values):
        values["VIBE_API_KEY"] = record["api_key"]
    return values


def merge(lower: dict, upper: Mapping[str, str]) -> dict:
    result = dict(lower)
    for group in (KEYS, BASES):
        if any(name in upper for name in group):
            for name in group:
                result.pop(name, None)
    result.update(upper)
    return result


def startup_environment(root: Path, workspace: Path, shell: Mapping[str, str]) -> dict[str, str]:
    from app.runtime.model_config import load_environment, MODEL_NAMES
    path = config_path(shell)
    label = "用户配置" if path.suffix == ".toml" else ".env"
    values, sources = {}, {}
    if not shell.get("VIBE_CONFIG_FILE") and (root / ".env").exists():
        values = load_environment(root / ".env", {})
        sources = {name: "旧安装配置" for name in values}
    saved = load_environment(path, {})
    old_connection = connection(values)
    values = merge(values, saved)
    if connection(values) != old_connection and not any(name in saved for name in KEYS):
        for name in KEYS:
            values.pop(name, None)
    sources.update({name: label for name in saved})
    original_connection = connection(values)
    project = workspace / ".mini-harness" / "config.toml"
    overrides = document_environment(read_document(project, project=True))
    values = merge(values, overrides)
    sources.update({name: "项目配置" for name in overrides})
    values = merge(values, shell)
    sources.update({name: "进程环境" for name in shell})
    if connection(values) != original_connection and not any(name in shell for name in KEYS):
        for name in KEYS:
            values.pop(name, None)
            sources.pop(name, None)
    values["VIBE_CONFIG_FILE"] = str(path)
    values["VIBE_CONFIG_LOADED"] = "1"
    values["VIBE_CONFIG_SOURCES"] = json.dumps({name: source for name, source in sources.items() if name in values}, ensure_ascii=False)
    values["VIBE_SETTINGS_ENV_KEYS"] = json.dumps(sorted(name for name in shell if name.startswith("VIBE_")))
    values["VIBE_MODEL_ENV_KEYS"] = json.dumps(sorted(name for name in shell if name in MODEL_NAMES))
    return values


def atomic_private_write(path: Path, text: str) -> None:
    if path.is_symlink():
        raise ValueError(f"配置是符号链接，请手动管理：{path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_user_values(path: Path, values: Mapping[str, str], replace_keys: set[str]) -> None:
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.parent / ".config.lock"
    if path.is_symlink() or credential_path(path).is_symlink() or lock_path.is_symlink():
        raise ValueError("配置或凭据是符号链接，请手动管理。")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_document(path)
        env = document_environment(data)
        for key in replace_keys:
            env.pop(key, None)
        env.update(values)
        secrets = read_credentials(credential_path(path))
        key = next((values[k] for k in KEYS if values.get(k)), None)
        if key is not None:
            provider, endpoint = connection(env)
            record = {"provider": provider, "base_url": endpoint, "api_key": key}
            identity = next((k for k, v in secrets.items() if v == record), uuid.uuid4().hex)
            secrets[identity] = record
            # 配置最后提交；失败时原配置仍引用原凭据。
            atomic_private_write(credential_path(path), json.dumps(secrets, ensure_ascii=False, indent=2) + "\n")
            data["credential_id"] = identity
        lines = []
        for field, name in FIELDS.items():
            if name in env:
                value = str(env[name].lower() in {"1", "true", "yes"}).lower() if field == "trust_env" else json.dumps(env[name], ensure_ascii=False)
                lines.append(f"{field} = {value}")
        if data.get("credential_id"):
            lines.append("credential_id = " + json.dumps(data["credential_id"]))
        other = {k: v for k, v in env.items() if k not in {*KEYS, *BASES, *FIELDS.values()}}
        if other:
            lines.append("\n[environment]")
            lines.extend(f"{json.dumps(k)} = {json.dumps(v, ensure_ascii=False)}" for k, v in sorted(other.items()))
        atomic_private_write(path, "\n".join(lines) + "\n")


def configuration_revision(path: Path) -> str:
    if path.suffix != ".toml":
        return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()
    paths = [path, credential_path(path)]
    digest = hashlib.sha256()
    for item in paths:
        data = item.read_bytes() if item.exists() else b""
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()
