"""公开评测的 Docker 执行边界；模型请求留在宿主。"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import tempfile
import uuid
from copy import copy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.sandbox import CommandCancelled, check_command
from app.tools import ToolRegistry, _command_output
from core.context import ContextBuilder
from core.state import TaskState


@dataclass
class _Result:
    code: int | None
    output: str
    timed_out: bool = False
    overflow: bool = False


class DockerSandbox:
    def __init__(self, workspace: Path, image: str, *, docker_host: str = "", timeout: float = 300):
        if not isinstance(image, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@:-]*", image):
            raise ValueError("invalid Docker image reference")
        self.workspace = Path(workspace).resolve()
        self.image = image
        self.image_id = ""
        self.timeout = self._timeout(timeout)
        self._docker = ["docker", "--host", docker_host] if docker_host else ["docker"]
        self._name = "mini-harness-eval-" + uuid.uuid4().hex
        self._source = self._name + "-source"
        self._names: set[str] = set()
        self._opened = False
        self._used = False

    @staticmethod
    def _timeout(value: float) -> float:
        value = float(value)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("timeout must be a finite positive number")
        return value

    async def _invoke(self, args: list[str], *, timeout: float, limit: int = 10_000,
                      cleanup: bool = True) -> _Result:
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(
            *self._docker, *args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        ))
        proc = None
        reading = None
        output = bytearray()
        overflow = False

        async def drain():
            nonlocal overflow
            while chunk := await proc.stdout.read(8192):
                if len(output) + len(chunk) > limit:
                    overflow = True
                output.extend(chunk)
                if len(output) > limit:
                    del output[:-limit]
            return await proc.wait()

        async def stop():
            nonlocal proc, reading
            if proc is None:
                proc = await spawning
            try:
                if cleanup:
                    await self.close()
            finally:
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                if reading is None:
                    reading = asyncio.create_task(drain())
                await reading

        timed_out = False
        cancelled = False
        try:
            proc = await asyncio.shield(spawning)
            reading = asyncio.create_task(drain())
            await asyncio.wait_for(asyncio.shield(reading), timeout)
        except FileNotFoundError as exc:
            raise RuntimeError("Docker CLI unavailable; install Docker and start its daemon") from exc
        except asyncio.TimeoutError:
            timed_out = True
            await stop()
        except asyncio.CancelledError:
            cancelled = True
            await stop()
        text = bytes(output).decode("utf-8", errors="replace")
        if overflow:
            text = "...[truncated]...\n" + text
        if cancelled:
            raise CommandCancelled(proc.returncode, text)
        return _Result(proc.returncode, text, timed_out, overflow)

    async def _require(self, args: list[str]) -> str:
        result = await self._invoke(args, timeout=self.timeout)
        if result.timed_out or result.code != 0:
            raise RuntimeError(f"Docker {args[0]} failed (exit={result.code}, timeout={result.timed_out}): {result.output}")
        return result.output

    async def open(self) -> None:
        if self._used:
            raise RuntimeError("Docker sandbox cannot be reopened")
        self._used = True
        self.workspace.mkdir(parents=True, exist_ok=True)
        if any(self.workspace.iterdir()):
            raise ValueError("evaluation workspace must be empty")
        try:
            inspected = await self._invoke(["image", "inspect", "--format", "{{.Id}}", self.image], timeout=self.timeout)
            if inspected.timed_out:
                raise RuntimeError("Docker image inspection timed out")
            if inspected.code != 0:
                await self._require(["pull", "--platform", "linux/amd64", self.image])
                self.image_id = (await self._require(["image", "inspect", "--format", "{{.Id}}", self.image])).strip()
            else:
                self.image_id = inspected.output.strip()
            if not self.image_id:
                raise RuntimeError("Docker image inspection returned no image ID")
            self._names.add(self._source)
            await self._require(["create", "--name", self._source, "--platform", "linux/amd64", self.image_id])
            await self._require(["cp", f"{self._source}:/testbed/.", str(self.workspace)])
            await self._remove(self._source)
            self._names.add(self._name)
            await self._require([
                "run", "-d", "--name", self._name, "--network", "none", "--platform", "linux/amd64",
                "--cpus", "2", "--memory", "2g", "--pids-limit", "256", "-v", f"{self.workspace}:/testbed",
                "-w", "/testbed", "--entrypoint", "/bin/bash", self.image_id, "-c", "tail -f /dev/null",
            ])
            self._opened = True
        except BaseException:
            await self.close()
            raise

    async def _remove(self, name: str) -> None:
        result = await self._invoke(["rm", "-f", name], timeout=min(self.timeout, 15), cleanup=False)
        if result.timed_out or (result.code != 0 and "No such container" not in result.output):
            raise RuntimeError(f"Docker cleanup failed for {name}: {result.output}")
        self._names.discard(name)

    def _cwd(self, cwd: str) -> str:
        relative = PurePosixPath(cwd)
        if relative.is_absolute() or ".." in relative.parts or "\\" in cwd or "\x00" in cwd:
            raise ValueError("cwd must be a relative workspace path")
        resolved = (self.workspace / cwd).resolve()
        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise ValueError("cwd outside workspace")
        return "/testbed" if str(relative) == "." else "/testbed/" + str(relative)

    def _exec(self, location: str, command: str) -> list[str]:
        return [
            "exec", "-w", location,
            "--env", "GIT_CONFIG_COUNT=1", "--env", "GIT_CONFIG_KEY_0=safe.directory",
            "--env", "GIT_CONFIG_VALUE_0=/testbed", self._name, "bash", "-lc", command,
        ]

    async def run(self, command: str, cwd: str = ".", timeout: float = 30) -> tuple[int | None, str, bool]:
        if not self._opened:
            raise RuntimeError("Docker sandbox is not running")
        check_command(command)
        location = self._cwd(cwd)
        timeout = self._timeout(timeout)
        script = "source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && " + command
        result = await self._invoke(self._exec(location, script), timeout=timeout)
        return result.code, result.output, result.timed_out

    async def export_patch(self) -> str:
        if not self._opened:
            raise RuntimeError("Docker sandbox is not running")
        command = "git add -N -- . ':!.vibe' && git -c core.externalDiff= diff --no-ext-diff --binary HEAD -- . ':!.vibe'"
        result = await self._invoke(
            self._exec("/testbed", command),
            timeout=self.timeout, limit=2 * 1024 * 1024,
        )
        if result.overflow:
            raise RuntimeError("patch exceeds 2 MiB limit; refusing a truncated patch")
        if result.timed_out or result.code != 0:
            raise RuntimeError(f"Docker patch export failed (exit={result.code}, timeout={result.timed_out}): {result.output}")
        return result.output

    async def close(self) -> None:
        self._opened = False
        errors = []
        for name in tuple(self._names):
            try:
                await self._remove(name)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("; ".join(errors))


class ContainerToolRegistry(ToolRegistry):
    def __init__(self, sandbox: DockerSandbox, context: ContextBuilder | None = None,
                 state: TaskState | None = None, *, skills_config: str):
        self.sandbox = sandbox
        config_path = Path(skills_config).expanduser().resolve()
        root = Path(sandbox.workspace).resolve()
        if config_path == root or root in config_path.parents:
            raise ValueError("evaluation skills config must be outside workspace")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if (not isinstance(config, dict) or config.get("discover") is not False
                or config.get("paths") or config.get("activate")):
            raise ValueError("evaluation must disable all skills and discovery")
        super().__init__(sandbox.workspace, context, state, skills_config=str(config_path))
        allowed = {"read_file", "list_files", "grep", "write_file", "edit_file", "bash", "recall", "update_task"}
        self._tools = {name: spec for name, spec in self._tools.items() if name in allowed}

    def _backup(self, target: Path) -> None:
        self._resolve(".vibe/backups")
        super()._backup(target)

    def recall(self, ref: str) -> str:
        if self.context is None:
            raise ValueError("recall unavailable: no context attached")
        safe_context = copy(self.context)
        safe_context._refs = {}
        safe_context.offload_dir = None

        def collect(candidate: str):
            if not isinstance(candidate, str) or not re.fullmatch(r"off-[0-9a-f]{8}", candidate):
                raise ValueError("invalid offload reference")
            if candidate in safe_context._refs:
                return
            if len(safe_context._refs) >= 256:
                raise ValueError("too many nested offload references")
            messages = self.context._refs.get(candidate)
            if messages is None:
                if self.context.offload_dir is None:
                    raise ValueError(f"unknown ref: {candidate}")
                root = Path(self.context.offload_dir).resolve()
                file = (root / f"{candidate}.json").resolve()
                if file.parent != root:
                    raise ValueError("offload reference outside offload directory")
                if not file.is_file():
                    raise ValueError(f"unknown ref: {candidate}")
                if file.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError("offload exceeds 2 MiB limit")
                raw = json.loads(file.read_text(encoding="utf-8"))
                messages = [self.context._message_from_dict(value) for value in raw]
            safe_context._refs[candidate] = messages
            for message in messages:
                if message.name == "history_archive_ref":
                    for nested in re.findall(r"\[offloaded: ([^\]]+)\]", message.content):
                        collect(nested)

        collect(ref)
        staged = copy(self)
        staged.context = safe_context
        return ToolRegistry.recall(staged, ref)

    def _atomic_write(self, target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".vibe-write-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def bash(self, command: str, timeout: float = 30.0, cwd: str = ".", verification: bool = False) -> str:
        raise RuntimeError("container bash requires async execution")

    async def _bash_async(self, command: str, timeout: float = 30.0, cwd: str = ".",
                          verification: bool = False) -> str:
        check_command(command)
        try:
            code, output, timed_out = await self.sandbox.run(command, cwd=cwd, timeout=float(timeout))
        except CommandCancelled as exc:
            return _command_output(command, cwd, timeout, exc.exit_code, exc.output, cancelled=True)
        return _command_output(command, cwd, timeout, code, output, timed_out=timed_out)
