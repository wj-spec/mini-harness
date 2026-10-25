from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.sandbox_proxy import PROXY_ENV_KEYS

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


WRITE_COMMANDS = frozenset({
    "rm", "rmdir", "mv", "cp", "ln", "tee", "touch", "mkdir", "install", "truncate", "shred",
})
SAFE_WRITE_ROOTS = (Path("/tmp").resolve(),)

SEATBELT_BINARY = "sandbox-exec"
BWRAP_BINARY = "bwrap"
SENSITIVE_READ_PATHS = (
    ".ssh", ".aws", ".gnupg", ".netrc", ".docker", ".config/gh", ".config/gcloud",
)


@dataclass(frozen=True)
class SandboxSpec:
    """一次运行的沙箱配置；写入限制在工作区与临时目录。"""

    kind: str  # seatbelt | bubblewrap
    workspace: Path
    allow_network: bool = False
    profile: Path | None = None
    proxy_port: int | None = None


def sandbox_kind() -> str | None:
    if sys.platform == "darwin" and shutil.which(SEATBELT_BINARY):
        return "seatbelt"
    if sys.platform.startswith("linux") and shutil.which(BWRAP_BINARY):
        return "bubblewrap"
    return None


def sandbox_available() -> bool:
    """当前平台是否有可用的 OS 级沙箱后端。"""
    return sandbox_kind() is not None


def probe_sandbox() -> tuple[bool, str]:
    """探测沙箱能否真正应用；嵌套沙箱等环境会失败，需提前给出清晰错误。"""
    kind = sandbox_kind()
    if kind is None:
        return False, "no sandbox backend available"
    try:
        if kind == "bubblewrap":
            proc = subprocess.run(
                [BWRAP_BINARY, "--ro-bind", "/", "/", "--dev", "/dev", "/bin/true"],
                capture_output=True, text=True, timeout=10,
            )
        else:
            handle = tempfile.NamedTemporaryFile("w", suffix=".sb", delete=False, encoding="utf-8")
            try:
                handle.write("(version 1)\n(allow default)\n")
                handle.close()
                proc = subprocess.run(
                    [SEATBELT_BINARY, "-f", handle.name, "/bin/echo", "ok"],
                    capture_output=True, text=True, timeout=10,
                )
            finally:
                Path(handle.name).unlink(missing_ok=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()[:300]
    return True, ""


def build_sandbox(workspace: Path, *, allow_network: bool = False,
                  proxy_port: int | None = None) -> SandboxSpec:
    kind = sandbox_kind()
    if kind is None:
        raise ValueError("no OS-level sandbox backend available (macOS seatbelt or Linux bwrap)")
    profile = (ensure_seatbelt_profile(workspace, allow_network=allow_network, proxy_port=proxy_port)
               if kind == "seatbelt" else None)
    return SandboxSpec(kind=kind, workspace=workspace.resolve(),
                       allow_network=allow_network, profile=profile, proxy_port=proxy_port)


def sandbox_env(spec: SandboxSpec | None) -> dict[str, str] | None:
    """白名单模式下给子命令注入代理环境变量。"""
    if spec is None or spec.proxy_port is None:
        return None
    url = f"http://127.0.0.1:{spec.proxy_port}"
    env = {key: url for key in PROXY_ENV_KEYS}
    env["NO_PROXY"] = "localhost,127.0.0.1"
    return env


def build_seatbelt_profile(workspace: Path, *, allow_network: bool = False,
                           proxy_port: int | None = None) -> str:
    """生成 Seatbelt 策略：默认放行读取，写入仅限工作区与临时目录，网络默认拒绝。"""
    lines = ["(version 1)", "(allow default)"]
    if proxy_port is not None:
        # 白名单模式：只允许连本机代理，其余网络由策略与代理共同拦截。
        lines.append("(deny network*)")
        lines.append(f'(allow network* (remote ip "localhost:{proxy_port}"))')
    elif not allow_network:
        lines.append("(deny network*)")
    lines.append("(deny file-write*)")
    roots = [workspace.resolve()]
    for candidate in (Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve(),
                      Path("/private/var/folders").resolve()):
        if candidate not in roots:
            roots.append(candidate)
    for root in roots:
        lines.append(f'(allow file-write* (subpath "{root}"))')
    lines.append('(allow file-write* (literal "/dev/null") (literal "/dev/stdout") '
                 '(literal "/dev/stderr") (literal "/dev/dtracehelper"))')
    # 保护 .git 的钩子与配置：写它们可造成沙箱外的持久化代码执行。
    git_dir = workspace.resolve() / ".git"
    lines.append(f'(deny file-write* (subpath "{git_dir / "hooks"}"))')
    for name in ("config", "config.worktree"):
        lines.append(f'(deny file-write* (literal "{git_dir / name}"))')
    home = Path.home()
    for name in SENSITIVE_READ_PATHS:
        lines.append(f'(deny file-read* (subpath "{home / name}"))')
    return "\n".join(lines) + "\n"


def ensure_seatbelt_profile(workspace: Path, *, allow_network: bool = False,
                            proxy_port: int | None = None) -> Path:
    """按工作区写出策略文件；内容不变时复用。"""
    name = ("seatbelt-allowlist.sb" if proxy_port is not None
            else "seatbelt-network.sb" if allow_network else "seatbelt.sb")
    profile = workspace / ".vibe" / "sandbox" / name
    content = build_seatbelt_profile(workspace, allow_network=allow_network, proxy_port=proxy_port)
    profile.parent.mkdir(parents=True, exist_ok=True)
    if not profile.is_file() or profile.read_text(encoding="utf-8") != content:
        profile.write_text(content, encoding="utf-8")
    return profile


def build_bwrap_command(command: str, workspace: Path, *, allow_network: bool = False) -> list[str]:
    roots = [workspace.resolve()]
    for candidate in (Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve()):
        if candidate not in roots:
            roots.append(candidate)
    argv = [
        BWRAP_BINARY, "--die-with-parent", "--unshare-pid", "--unshare-ipc", "--unshare-uts",
        "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
    ]
    for root in roots:
        argv += ["--bind", str(root), str(root)]
    if not allow_network:
        argv.append("--unshare-net")
    argv += ["--", "/bin/bash", "-lc", command]
    return argv


def sandbox_wrapped(command: str, spec: SandboxSpec | None) -> list[str]:
    if spec is None:
        return ["/bin/bash", "-lc", command]
    if spec.kind == "bubblewrap":
        return build_bwrap_command(command, spec.workspace, allow_network=spec.allow_network)
    return [SEATBELT_BINARY, "-f", str(spec.profile), "/bin/bash", "-lc", command]


def annotate_sandbox_denial(output: str, spec: SandboxSpec | None) -> str:
    """沙箱拒绝时给出结构化前缀，模型能区分策略拒绝与普通失败。"""
    if spec is None:
        return output
    if "blocked by sandbox allowlist" in output:
        return ("ERROR sandbox_denied: host not in sandbox network allowlist\n" + output)
    if "sandbox_apply:" in output or "bwrap: setting up uid map" in output:
        return ("ERROR sandbox_unavailable: sandbox could not be applied in this "
                "environment; rerun without --sandbox\n" + output)
    if "Operation not permitted" in output or "Sandbox:" in output:
        return ("ERROR sandbox_denied: command blocked by sandbox policy "
                "(write outside workspace or network disabled)\n" + output)
    return output


def _outside_workspace_write(token: str, workspace: Path) -> str | None:
    """显式写/删工作区外的一次尽力检查；bash 仍可绕过，不能当边界。"""
    probe = token.strip("'\"")
    if not probe or probe.startswith("-") or probe in {"~", "$HOME", "${HOME}"}:
        return None
    expanded = os.path.expandvars(os.path.expanduser(probe))
    if expanded.startswith(("~", "$")):
        return None
    if not expanded.startswith("/"):
        if ".." not in Path(expanded).parts:
            return None
        target = (workspace / expanded).resolve()
    else:
        target = Path(expanded).resolve()
    root = workspace.resolve()
    if target == root or root in target.parents:
        return None
    if any(target == base or base in target.parents for base in SAFE_WRITE_ROOTS):
        return None
    return str(target)


def check_write_paths(command: str, workspace: Path) -> None:
    try:
        tokens = list(shlex.shlex(command, posix=True, punctuation_chars=True))
    except ValueError:
        return
    command_word = ""
    after_redirect = False
    for token in tokens:
        if after_redirect:
            violation = _outside_workspace_write(token, workspace)
            if violation:
                raise PermissionError(f"blocked write outside workspace: {violation}")
            after_redirect = False
            continue
        if token in {">", ">>", ">|"}:
            after_redirect = True
            continue
        if token and token[0] in "|;&()<>":
            command_word = ""
            continue
        if not command_word:
            command_word = token
            continue
        if command_word in WRITE_COMMANDS:
            violation = _outside_workspace_write(token, workspace)
            if violation:
                raise PermissionError(f"blocked write outside workspace: {violation}")


class CommandCancelled(Exception):
    def __init__(self, exit_code: int | None, output: str):
        self.exit_code = exit_code
        self.output = output
        super().__init__("command cancelled; process group reaped")


def check_command(command: str, workspace: Path | None = None) -> None:
    for pattern, reason in DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            raise PermissionError(f"blocked dangerous command ({reason})")
    if workspace is not None:
        check_write_paths(command, workspace)


def run_command(
    command: str,
    cwd: Path,
    timeout: float = 30.0,
    max_chars: int = 10_000,
    *,
    sandbox: SandboxSpec | None = None,
    env_extra: dict[str, str] | None = None,
) -> tuple[int, str, bool]:
    proc = subprocess.Popen(
        sandbox_wrapped(command, sandbox), env=({**os.environ, **env_extra} if env_extra else None),
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

    output = annotate_sandbox_denial(output or "", sandbox)
    if len(output) > max_chars:
        output = "...[truncated]...\n" + output[-max_chars:]
    return proc.returncode, output, timed_out


async def run_command_async(
    command: str,
    cwd: Path,
    timeout: float = 30.0,
    max_chars: int = 10_000,
    *,
    sandbox: SandboxSpec | None = None,
    env_extra: dict[str, str] | None = None,
) -> tuple[int, str, bool]:
    """异步执行；取消和超时都回收整个进程组。"""
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        *sandbox_wrapped(command, sandbox), cwd=str(cwd),
        env=({**os.environ, **env_extra} if env_extra else None),
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

    text = annotate_sandbox_denial((output or b"").decode("utf-8", errors="replace"), sandbox)
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
