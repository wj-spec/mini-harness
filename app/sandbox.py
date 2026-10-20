from __future__ import annotations

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
