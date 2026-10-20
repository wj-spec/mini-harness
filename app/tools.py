from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from core.context import ContextBuilder
from core.models import ToolCall
from core.state import TaskState
from app.diff import unified_diff
from app.sandbox import check_command, run_command

IGNORE_DIRS = {
    ".git",
    ".pylibs",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "dist",
    "build",
}


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., str]
    timeout: float = 10.0
    risk: str = "read"
    retryable: bool = False


class ToolRegistry:
    """D1 只读工具：read_file / list_files。写和执行在 D3/D4。"""

    def __init__(
        self,
        workspace: str | Path,
        context: ContextBuilder | None = None,
        state: TaskState | None = None,
    ):
        self.root = Path(workspace).resolve()
        self.context = context
        self.state = state
        self._read_hashes: dict[Path, str] = {}
        self._tools: dict[str, ToolSpec] = {
            "read_file": ToolSpec(
                name="read_file",
                description="Read a text file inside the workspace. Returns numbered lines.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path relative to workspace"},
                        "start": {"type": "integer", "description": "1-based first line", "default": 1},
                        "end": {"type": "integer", "description": "1-based last line"},
                    },
                    "required": ["path"],
                },
                fn=self.read_file,
                retryable=True,
            ),
            "grep": ToolSpec(
                name="grep",
                description="Search file contents with a regex inside the workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string", "default": "."},
                        "max_results": {"type": "integer", "default": 50},
                    },
                    "required": ["pattern"],
                },
                fn=self.grep,
                retryable=True,
            ),
            "list_files": ToolSpec(
                name="list_files",
                description="List files and directories under a path in the workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "default": "."},
                        "depth": {"type": "integer", "default": 2},
                    },
                },
                fn=self.list_files,
                retryable=True,
            ),
            "write_file": ToolSpec(
                name="write_file",
                description="Create or overwrite a text file inside the workspace. Requires approval.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
                fn=self.write_file,
                risk="write",
            ),
            "edit_file": ToolSpec(
                name="edit_file",
                description=(
                    "Replace a unique 'old' substring with 'new' in a file inside the workspace. "
                    "Requires approval."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old": {"type": "string"},
                        "new": {"type": "string"},
                    },
                    "required": ["path", "old", "new"],
                },
                fn=self.edit_file,
                risk="write",
            ),
            "bash": ToolSpec(
                name="bash",
                description="Run a shell command in the workspace. Requires approval. Output is truncated.",
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"},
                        "timeout": {"type": "number", "default": 30},
                        "cwd": {"type": "string", "default": "."},
                    },
                    "required": ["command"],
                },
                fn=self.bash,
                risk="exec",
            ),
        }

        if context is not None:
            self._tools["recall"] = ToolSpec(
                name="recall",
                description=(
                    "Resolve an [offloaded: ref] reference and return its original content. "
                    "Use this when the context shows an offloaded ref you need again."
                ),
                parameters={
                    "type": "object",
                    "properties": {"ref": {"type": "string"}},
                    "required": ["ref"],
                },
                fn=self.recall,
                retryable=True,
            )
        if state is not None:
            self._tools["update_task"] = ToolSpec(
                name="update_task",
                description=(
                    "Update the harness task state: plan, done, open, or a note. "
                    "Call it when the plan or progress changes."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "plan": {"type": "array", "items": {"type": "string"}},
                        "done": {"type": "array", "items": {"type": "string"}},
                        "open": {"type": "array", "items": {"type": "string"}},
                        "note": {"type": "string"},
                    },
                },
                fn=self.update_task,
            )

    def risk(self, name: str) -> str:
        spec = self._tools.get(name)
        return spec.risk if spec is not None else "read"

    def is_retryable(self, name: str) -> bool:
        spec = self._tools.get(name)
        return bool(spec and spec.retryable)

    def bash(self, command: str, timeout: float = 30.0, cwd: str = ".") -> str:
        target = self._resolve(cwd)
        if not target.is_dir():
            raise NotADirectoryError(cwd)
        check_command(command)
        code, output, timed_out = run_command(command, target, float(timeout))
        header = f"$ {command}\n(cwd={cwd}, timeout={timeout}s)\n"
        if timed_out:
            return header + f"ERROR timeout after {timeout}s\n{output}"
        return header + f"exit={code}\n" + (output or "(no output)")

    def preview(self, call: ToolCall) -> str:
        if call.name == "write_file":
            path = str(call.args.get("path", ""))
            content = str(call.args.get("content", ""))
            target = self._resolve(path)
            old = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
            if target.exists():
                self._assert_fresh(target, old)
                self._read_hashes[target] = self._hash(old)
            return unified_diff(old, content, path)
        if call.name == "bash":
            command = str(call.args.get("command", ""))
            check_command(command)
            timeout = call.args.get("timeout", 30)
            cwd = call.args.get("cwd", ".")
            return f"$ {command}\ncwd: {cwd}\ntimeout: {timeout}s\n\n[risk] exec — approval required"
        if call.name == "edit_file":
            path = str(call.args.get("path", ""))
            old_text = str(call.args.get("old", ""))
            new_text = str(call.args.get("new", ""))
            target = self._resolve(path)
            if not target.exists():
                raise FileNotFoundError(path)
            if not old_text:
                raise ValueError("old must not be empty")
            content = target.read_text(encoding="utf-8", errors="replace")
            self._assert_fresh(target, content)
            if content.count(old_text) != 1:
                raise ValueError(f"old must match exactly once (found {content.count(old_text)})")
            self._read_hashes[target] = self._hash(content)
            return unified_diff(content, content.replace(old_text, new_text, 1), path)
        args = json.dumps(call.args, ensure_ascii=False, sort_keys=True)
        return f"{call.name} {args}\n\n[risk] {self.risk(call.name)} — approval required"

    def write_file(self, path: str, content: str) -> str:
        target = self._resolve(path)
        if target.is_dir():
            raise IsADirectoryError(path)
        if len(content.encode("utf-8")) > 200_000:
            raise ValueError("content exceeds 200k bytes")
        if target.exists():
            current = target.read_text(encoding="utf-8", errors="replace")
            self._assert_fresh(target, current)
        self._backup(target)
        self._atomic_write(target, content)
        self._read_hashes[target] = self._hash(content)
        return f"wrote {path} ({len(content.encode('utf-8'))} bytes)"

    def edit_file(self, path: str, old: str, new: str) -> str:
        target = self._resolve(path)
        if not target.exists():
            raise FileNotFoundError(path)
        if target.is_dir():
            raise IsADirectoryError(path)
        if not old:
            raise ValueError("old must not be empty")
        content = target.read_text(encoding="utf-8")
        self._assert_fresh(target, content)
        count = content.count(old)
        if count != 1:
            raise ValueError(f"old must match exactly once (found {count})")
        updated = content.replace(old, new, 1)
        if len(updated.encode("utf-8")) > 200_000:
            raise ValueError("content exceeds 200k bytes")
        self._backup(target)
        self._atomic_write(target, updated)
        self._read_hashes[target] = self._hash(updated)
        return f"edited {path} (1 replacement)"

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _assert_fresh(self, target: Path, content: str) -> None:
        previous = self._read_hashes.get(target)
        if previous is not None and self._hash(content) != previous:
            rel = target.relative_to(self.root)
            raise ValueError(f"file changed since last read: {rel}; re-read before editing")

    def _backup(self, target: Path) -> None:
        if not target.exists():
            return
        backup_dir = self.root / ".vibe" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        rel = target.relative_to(self.root).as_posix().replace("/", "__")
        shutil.copy2(target, backup_dir / f"{stamp}-{rel}")

    def _atomic_write(self, target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)

    def recall(self, ref: str) -> str:
        if self.context is None:
            raise ValueError("recall unavailable: no context attached")
        try:
            messages = self.context.resolve(ref)
        except KeyError as exc:
            raise ValueError(f"unknown ref: {ref}") from exc
        parts: list[str] = []
        for m in messages:
            content = m.content.strip()
            if len(content) > 8000:
                content = content[:8000] + "\n...[truncated]"
            calls = "".join(f"\n  call {c.name}({c.args})" for c in m.tool_calls)
            role = f"{m.role} {m.name}" if m.name else m.role
            parts.append(f"[{role}]\n{content}{calls}")
        return "\n\n".join(parts)[:20000]

    def update_task(
        self,
        plan: list[str] | None = None,
        done: list[str] | None = None,
        open: list[str] | None = None,
        note: str | None = None,
    ) -> str:
        if self.state is None:
            raise ValueError("update_task unavailable: no state attached")
        return self.state.update(plan=plan, done=done, open=open, note=note)

    def names(self) -> list[str]:
        return list(self._tools)

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def specs(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._tools.values()
        ]

    async def execute_async(self, call: ToolCall) -> tuple[bool, str]:
        spec = self._tools.get(call.name)
        if spec is None:
            return False, f"ERROR unknown_tool: {call.name}"
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(spec.fn, **call.args), timeout=spec.timeout
            )
            return True, str(result)
        except asyncio.TimeoutError:
            return False, f"ERROR timeout: {call.name} exceeded {spec.timeout}s"
        except Exception as exc:
            return False, f"ERROR {type(exc).__name__}: {exc}"

    def execute(self, call: ToolCall) -> tuple[bool, str]:
        spec = self._tools.get(call.name)
        if spec is None:
            return False, f"ERROR unknown_tool: {call.name}"
        try:
            return True, spec.fn(**call.args)
        except Exception as exc:
            return False, f"ERROR {type(exc).__name__}: {exc}"

    def _resolve(self, path: str) -> Path:
        candidate = (self.root / path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"path outside workspace: {path}")
        return candidate

    def read_file(self, path: str, start: int = 1, end: int | None = None) -> str:
        target = self._resolve(path)
        if not target.exists():
            raise FileNotFoundError(path)
        if target.is_dir():
            raise IsADirectoryError(path)

        text = target.read_text(encoding="utf-8", errors="replace")
        self._read_hashes[target] = self._hash(text)
        if len(text) > 200_000:
            text = text[:200_000] + "\n...[truncated at 200k chars]"
        lines = text.splitlines()
        total = len(lines)
        start = max(1, int(start))
        end = min(int(end) if end is not None else start + 199, total, start + 199)
        body = "\n".join(f"{i:5d}  {lines[i - 1]}" for i in range(start, end + 1))
        return f"{path} lines {start}-{end}/{total}\n{body}"

    def grep(self, pattern: str, path: str = ".", max_results: int = 50) -> str:
        target = self._resolve(path)
        if not target.exists():
            raise FileNotFoundError(path)

        rg = shutil.which("rg")
        if rg:
            rel = target.relative_to(self.root)
            search_path = "." if str(rel) == "." else str(rel)
            try:
                proc = subprocess.run(
                    [rg, "-n", "--no-heading", "--color", "never", pattern, search_path],
                    cwd=self.root,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                return "ERROR timeout: grep exceeded 10s"
            lines = proc.stdout.splitlines()[: int(max_results)]
            return "\n".join(line[:300] for line in lines) if lines else "(no matches)"

        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc

        matches: list[str] = []
        for item in sorted(target.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(self.root)
            if any(part in IGNORE_DIRS for part in rel.parts):
                continue
            if item.stat().st_size > 200_000:
                continue
            try:
                text = item.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{rel}:{lineno}:{line[:300]}")
                    if len(matches) >= int(max_results):
                        return "\n".join(matches)
        return "\n".join(matches) if matches else "(no matches)"

    def list_files(self, path: str = ".", depth: int = 2) -> str:
        target = self._resolve(path)
        if target.is_file():
            return str(target.relative_to(self.root))
        if not target.exists():
            raise FileNotFoundError(path)

        entries: list[str] = []
        for item in sorted(target.rglob("*")):
            rel = item.relative_to(target)
            if any(part in IGNORE_DIRS for part in rel.parts):
                continue
            if len(rel.parts) > max(1, int(depth)):
                continue
            suffix = "/" if item.is_dir() else ""
            entries.append(str(item.relative_to(self.root)) + suffix)
            if len(entries) >= 200:
                entries.append("...[truncated at 200 entries]")
                break
        return "\n".join(entries) if entries else "(empty)"
