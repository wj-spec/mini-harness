"""Skill：可复用的操作手册，渐进式披露。

发现顺序：显式配置、项目目录、本机目录；同名时先发现者优先。

文件格式：开头几行 `key: value` 头信息（name / description），空行后是正文。
"""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

MAX_SKILL_CHARS = 64_000
MAX_SKILL_FILE_BYTES = 256 * 1024
MAX_SUMMARY_CHARS = 600
SKILL_FILE = "SKILL.md"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
HEADER_KEYS = ("name", "description")
PROJECT_SKILL_DIRS = (".vibe/skills", ".agents/skills", ".claude/skills", "skills")
LOCAL_SKILL_DIRS = (".vibe/skills", ".agents/skills", ".codex/skills", ".claude/skills")
SKILL_CATEGORY_DEPTH = 3
SKILL_RESOURCE_DIRS = {"references", "scripts", "assets", "resources", "node_modules", "__pycache__", "venv"}


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    path: Path

    def summary(self, limit: int = MAX_SUMMARY_CHARS) -> str:
        text = " ".join(self.description.split())
        if len(text) > limit:
            text = text[: limit - 3] + "..."
        return text

    def render(self) -> str:
        """正文完整渲染；激活管理单独检查长度。"""
        body = self.body.strip()
        header = f"# skill: {self.name}\n{self.description.strip()}\n\n" if self.description else f"# skill: {self.name}\n\n"
        return header + body


def parse_skill(text: str, *, name: str, path: Path = Path("SKILL.md")) -> Skill | None:
    """解析单个 skill 文本；缺 name 时用文件名，正文为空则返回 None。"""
    lines = text.splitlines()
    meta: dict[str, str] = {}
    index = 0
    if lines and lines[0].strip() == "---":
        index = 1
        while index < len(lines) and lines[index].strip() != "---":
            key, _, value = lines[index].partition(":")
            key = key.strip().lower()
            if key in HEADER_KEYS:
                if key in meta:
                    return None
                value = value.strip()
                if value in {"|", "|-", "|+", ">", ">-", ">+"}:
                    if key != "description":
                        return None
                    block: list[str] = []
                    index += 1
                    while index < len(lines) and (not lines[index].strip() or lines[index][:1].isspace()):
                        block.append(lines[index].strip())
                        index += 1
                    if not any(block):
                        return None
                    meta[key] = (" ".join(block) if value.startswith(">") else "\n".join(block)).strip()
                    continue
                meta[key] = value.strip("\"'")
            index += 1
        if index == len(lines):
            return None
        index += 1
    else:
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                break
            key, sep, value = line.partition(":")
            if not sep or key.strip().lower() not in HEADER_KEYS:
                break
            meta[key.strip().lower()] = value.strip().strip("\"'")
            index += 1

    body = "\n".join(lines[index:]).strip()
    if not body:
        return None
    resolved = (meta.get("name") or name).strip()
    if not NAME_RE.match(resolved):
        return None
    description = meta.get("description", "").strip()
    if not description:
        # 退回正文首段，保证摘要里至少有点信息
        description = " ".join(part for part in body.splitlines() if part.strip())[:200]
    return Skill(name=resolved, description=description, body=body, path=path)


def load_skill(path: str | Path) -> Skill | None:
    file = Path(path)
    try:
        return _read_skill_file(file)
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _read_limited(file: Path) -> str:
    metadata = file.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("expected a regular file")
    if metadata.st_size > MAX_SKILL_FILE_BYTES:
        raise ValueError(f"file size exceeds limit {MAX_SKILL_FILE_BYTES} bytes")
    with file.open("rb") as stream:
        data = stream.read(MAX_SKILL_FILE_BYTES + 1)
    if len(data) > MAX_SKILL_FILE_BYTES:
        raise ValueError(f"file size exceeds limit {MAX_SKILL_FILE_BYTES} bytes")
    return data.decode("utf-8")


def _read_skill_file(file: Path, *, fallback_name: str = "") -> Skill:
    text = _read_limited(file)
    stem = fallback_name or (file.parent.name if file.name == SKILL_FILE else file.stem)
    skill = parse_skill(text, name=stem, path=file)
    if skill is None:
        raise ValueError("invalid skill metadata or empty body")
    return skill


def discover_skills(workspace: str | Path) -> list[Skill]:
    """发现项目及本机技能，正文只在激活后进入模型上下文。"""
    root = Path(workspace).resolve()
    skills, _ = _scan_skills(root, [])
    return skills


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _scan_skills(workspace: Path, paths: list[str], *, discover: bool = True) -> tuple[list[Skill], list[str]]:
    found: dict[str, Skill] = {}
    seen_files: set[Path] = set()
    errors: list[str] = []
    sources: list[tuple[Path, Path | None]] = []
    for source in paths:
        candidate = Path(source).expanduser()
        if not candidate.is_absolute():
            candidate = workspace / candidate
        sources.append((candidate, None))
    if discover:
        home = Path(os.environ.get("VIBE_SKILLS_HOME") or Path.home()).expanduser().resolve()
        sources.extend((workspace / relative, workspace) for relative in PROJECT_SKILL_DIRS)
        sources.extend((home / relative, home) for relative in LOCAL_SKILL_DIRS)

    roots: list[tuple[Path, Path, bool]] = []
    seen_roots: set[Path] = set()
    for candidate, scope in sources:
        try:
            root = candidate.resolve()
            if scope is not None and not _within(root, scope):
                errors.append(f"skill source escapes workspace or local root: {candidate}; add an explicit paths entry to allow it")
                continue
            if not root.exists():
                if scope is None:
                    errors.append(f"skill source does not exist: {candidate}")
                elif candidate.is_symlink():
                    errors.append(f"broken skill source symlink: {candidate}")
                continue
            if root.is_file():
                if candidate.suffix != ".md":
                    errors.append(f"skill source file must be Markdown: {candidate}")
                    continue
                is_directory = False
            elif root.is_dir():
                is_directory = True
            else:
                errors.append(f"invalid skill source: {candidate}")
                continue
            if root in seen_roots:
                continue
            seen_roots.add(root)
            roots.append((candidate, root, is_directory))
        except (OSError, RuntimeError) as error:
            errors.append(f"cannot scan skill source {candidate}: {error}")

    boundaries = [root for _, root, is_directory in roots if is_directory]
    file_roots = {root for _, root, is_directory in roots if not is_directory}

    def allowed(path: Path) -> bool:
        return path in file_roots or any(_within(path, boundary) for boundary in boundaries)

    def scan_directory(directory: Path, depth: int, ancestors: frozenset[Path]) -> list[Path]:
        try:
            resolved = directory.resolve()
            if not allowed(resolved):
                errors.append(f"skill path escapes source root: {directory}")
                return []
            if resolved in ancestors:
                return []
            entrypoint = directory / SKILL_FILE
            if entrypoint.exists() or entrypoint.is_symlink():
                return [entrypoint]
            if depth >= SKILL_CATEGORY_DEPTH:
                return []
            entries = sorted(directory.iterdir())
            files: list[Path] = []
            for entry in entries:
                if entry.is_dir():
                    if entry.name in SKILL_RESOURCE_DIRS or (entry.name.startswith(".") and entry.name != ".system"):
                        continue
                    files.extend(scan_directory(entry, depth + 1, ancestors | {resolved}))
                elif depth == 0 and entry.name.endswith(".md"):
                    files.append(entry)
            return files
        except (OSError, RuntimeError) as error:
            errors.append(f"cannot scan skill source {directory}: {error}")
            return []

    for candidate, _, is_directory in roots:
        files = scan_directory(candidate, 0, frozenset()) if is_directory else [candidate]
        for file in files:
            try:
                resolved = file.resolve()
                if not allowed(resolved):
                    errors.append(f"skill path escapes source root: {file}")
                    continue
                if not resolved.exists():
                    if file.is_symlink():
                        errors.append(f"broken skill symlink: {file}")
                    continue
                if resolved in seen_files:
                    continue
                fallback_name = file.parent.name if file.name == SKILL_FILE else file.stem
                skill = _read_skill_file(resolved, fallback_name=fallback_name)
            except (OSError, UnicodeDecodeError, ValueError, RuntimeError) as error:
                errors.append(f"cannot load skill {file}: {error}")
                continue
            seen_files.add(resolved)
            previous = found.get(skill.name)
            if previous is not None:
                if previous.path != skill.path:
                    errors.append(f"duplicate skill {skill.name}: keeping {previous.path}; ignored {skill.path}")
            else:
                found[skill.name] = skill
    return [found[name] for name in sorted(found)], errors


class SkillManager:
    """注册目录常驻；正文临时激活。activate 配置仅兼容启动预装。"""

    def __init__(self, workspace: str | Path, config_path: str | Path = "") -> None:
        self.workspace = Path(workspace).resolve()
        self._explicit_config = bool(config_path)
        config_file = Path(config_path).expanduser() if config_path else Path(".vibe/skills.json")
        self.config_path = config_file if config_file.is_absolute() else self.workspace / config_file
        self._available: dict[str, Skill] = {}
        self._active_names: list[str] = []
        self._errors: list[str] = []
        self._configured_activation: list[str] = []
        self.refresh()
        for name in self._configured_activation:
            try:
                self._activate_current(name)
            except (KeyError, ValueError):
                pass

    def _read_config(self) -> dict:
        try:
            if not self.config_path.exists():
                if self._explicit_config:
                    raise ValueError("file does not exist")
                config = {}
            else:
                config = json.loads(_read_limited(self.config_path))
            if not isinstance(config, dict):
                raise ValueError("expected an object")
            unknown = set(config) - {"paths", "activate", "disabled", "discover"}
            if unknown:
                raise ValueError(f"unknown keys: {', '.join(sorted(unknown))}")
            if not isinstance(config.get("discover", True), bool):
                raise ValueError("discover must be a boolean")
            for key in ("paths", "activate", "disabled"):
                values = config.get(key, [])
                if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                    raise ValueError(f"{key} must be a list of nonempty strings")
                if key != "paths" and any(not NAME_RE.fullmatch(value) for value in values):
                    raise ValueError(f"{key} contains an invalid skill name")
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise ValueError(f"invalid skill config {self.config_path}: {error}") from error
        return config

    @property
    def available(self) -> list[Skill]:
        return list(self._available.values())

    @property
    def active(self) -> list[Skill]:
        return [self._available[name] for name in self._active_names]

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    def refresh(self) -> list[Skill]:
        config = self._read_config()
        skills, errors = _scan_skills(self.workspace, config.get("paths", []), discover=config.get("discover", True))
        disabled = set(config.get("disabled", []))
        available = {skill.name: skill for skill in skills if skill.name not in disabled}
        active_names: list[str] = []
        for name in self._active_names:
            if name not in available:
                continue
            try:
                self._validate_activation(available[name])
            except ValueError as error:
                errors.append(str(error))
            else:
                active_names.append(name)
        self._available = available
        self._active_names = active_names
        self._errors = errors
        self._configured_activation = config.get("activate", [])
        for name in self._configured_activation:
            try:
                self._validate_activation(self._lookup(name))
            except (KeyError, ValueError) as error:
                self._errors.append(f"static activation {name}: {error}")
        return self.available

    @staticmethod
    def _validate_activation(skill: Skill) -> None:
        if len(skill.render()) > MAX_SKILL_CHARS or len(skill.body.strip()) > MAX_SKILL_CHARS:
            raise ValueError(f"skill {skill.name} exceeds activation limit {MAX_SKILL_CHARS} characters (including metadata)")

    def _lookup(self, name: str) -> Skill:
        resolved = name.strip()
        skill = self._available.get(resolved)
        if skill is None:
            names = ", ".join(self._available) or "none"
            raise KeyError(f"unknown skill {resolved!r}; available: {names}")
        return skill

    def _activate_current(self, name: str) -> Skill:
        skill = self._lookup(name)
        self._validate_activation(skill)
        if skill.name not in self._active_names:
            self._active_names.append(skill.name)
        return skill

    def activate(self, name: str) -> Skill:
        self.refresh()
        return self._activate_current(name)

    def deactivate(self, name: str) -> bool:
        skill = self._lookup(name)
        if skill.name not in self._active_names:
            return False
        self._active_names.remove(skill.name)
        return True

    def clear_temporary(self) -> list[str]:
        names = list(self._active_names)
        self._active_names.clear()
        return names

    def render_catalog(self) -> str:
        if not self._available:
            return ""
        lines = ["[SKILLS] 常驻注册目录（名称、用途、路径）；正文按需临时激活，使用结束后卸载。"]
        for skill in self.available:
            status = "active" if skill.name in self._active_names else "inactive"
            lines.append(f"- {skill.name}: {skill.summary()} [{status}] source: {skill.path}")
        return "\n".join(lines)

    def render_active(self) -> str:
        if not self._active_names:
            return ""
        return "[ACTIVE SKILLS]\n\n" + "\n\n".join(skill.render() for skill in self.active)

    def format_status(self) -> str:
        if self.available:
            lines = [f"skills: {len(self.available)} registered, {len(self.active)} temporary_active"]
            for skill in self.available:
                status = "active" if skill.name in self._active_names else "inactive"
                lines.append(f"- {skill.name}: {skill.summary()} [{status}] source: {skill.path}")
        else:
            lines = ["(no skills; configure paths or add .vibe/skills/<name>/SKILL.md)"]
        if self.errors:
            lines.append("errors:")
            lines.extend(f"- {error}" for error in self.errors)
        return "\n".join(lines)


def render_skills(skills: list[Skill], limit: int = MAX_SUMMARY_CHARS) -> str:
    """兼容的摘要入口；正文由激活技能上下文提供。"""
    if not skills:
        return ""
    lines = [
        "[SKILLS] 可复用的操作手册（只有摘要）。任务匹配时先调用 load_skill 激活，再按活动技能正文执行；结束后调用 unload_skill 卸载。",
    ]
    for skill in skills:
        lines.append(f"- {skill.name}: {skill.summary(limit)}")
    return "\n".join(lines)
