from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
from pathlib import Path

DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(/|~|\$HOME)(\s|$)", "rm -rf on root/home"),
    (r"\bmkfs(\.|\s)", "mkfs"),
    (r"\bdd\s+if=", "dd"),
    (r":\(\)\s*\{.*\};\s*:", "fork bomb"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "system power"),
    (r"\bsudo\b", "sudo"),
    (r"\bchmod\s+-R\s+777\s+/(\s|$)", "chmod 777 root"),
    (r"\bgit\s+push\s+--force\b", "force push"),
    (r"\bgit\s+reset\s+--hard\b", "hard reset"),
    (r"\bcurl\b[^|]*\|\s*(sh|bash)\b", "curl pipe shell"),
    (r"\bwget\b[^|]*\|\s*(sh|bash)\b", "wget pipe shell"),
]


class CommandCancelled(Exception):
    def __init__(self, exit_code: int | None, output: str):
        self.exit_code = exit_code
        self.output = output
        super().__init__("command cancelled; process group reaped")


def check_command(command: str) -> None:
    for pattern, reason in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            raise PermissionError(f"blocked dangerous command ({reason})")


def run_command(
    command: str,
    cwd: Path,
    timeout: float = 30.0,
    max_chars: int = 10_000,
) -> tuple[int, str, bool]:
    proc = subprocess.Popen(
        ["/bin/bash", "-lc", command],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        output, _ = proc.communicate()

    output = output or ""
    if len(output) > max_chars:
        output = "...[truncated]...\n" + output[-max_chars:]
    return proc.returncode, output, timed_out


async def run_command_async(
    command: str,
    cwd: Path,
    timeout: float = 30.0,
    max_chars: int = 10_000,
) -> tuple[int, str, bool]:
    """异步执行；取消和超时都回收整个进程组。"""
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        "/bin/bash", "-lc", command, cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    ))
    proc = None
    communicating = None
    timed_out = False
    cancelled = False
    output = b""
    try:
        proc = await asyncio.shield(spawning)
        communicating = asyncio.create_task(proc.communicate())
        try:
            output, _ = await asyncio.wait_for(asyncio.shield(communicating), timeout)
        except asyncio.TimeoutError:
            timed_out = True
            _kill_group(proc.pid)
            output, _ = await communicating
    except asyncio.CancelledError:
        cancelled = True
    finally:
        # 创建过程中被取消，也必须先取得句柄再回收。
        if proc is None:
            proc = await spawning
        _kill_group(proc.pid)
        if communicating is None:
            communicating = asyncio.create_task(proc.communicate())
        output, _ = await communicating
        await proc.wait()

    text = (output or b"").decode("utf-8", errors="replace")
    if len(text) > max_chars:
        text = "...[truncated]...\n" + text[-max_chars:]
    if cancelled:
        raise CommandCancelled(proc.returncode, text)
    return proc.returncode, text, timed_out


def _kill_group(group: int) -> None:
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        pass
