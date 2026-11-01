#!/usr/bin/env python3
"""首用环境自检：检查启动 Ink 工作台所需的条件。

只依赖标准库；每个检查函数都接受注入的 env / TTY / 路径 / 探测函数，便于单测。
main 只负责解析参数、打印清单并返回退出码（阻塞项 exit 2，否则 exit 0）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
API_KEY_NAMES = ("VIBE_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")
API_KEY_PLACEHOLDERS = (
    "sk-your", "your-key", "your_api_key", "changeme", "xxx",
    "example", "dummy", "placeholder",
)
PYTHON_MODULES = ("httpx", "fastmcp", "ddgs")
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
MIN_NODE = 22
MIN_PYTHON = (3, 11)

OK = "ok"
WARN = "warn"
FAIL = "fail"
_SYMBOLS = {OK: "✓", WARN: "!", FAIL: "✗"}

# _AUTO 表示"由 collect_checks 自己选择解释器"；显式 None 表示"没有解释器"。
_AUTO = object()
_Version = tuple[int, int, int]
_Run = Callable[..., subprocess.CompletedProcess]

_MODULE_PROBE = (
    "import importlib.util, json, sys\n"
    "missing = []\n"
    "for name in sys.argv[1:]:\n"
    "    try:\n"
    "        found = importlib.util.find_spec(name) is not None\n"
    "    except Exception:\n"
    "        found = False\n"
    "    if not found:\n"
    "        missing.append(name)\n"
    "print(json.dumps(missing))\n"
)


@dataclass(frozen=True)
class Check:
    """一条自检结果：status 为 ok / warn / fail。"""

    status: str
    label: str
    hint: str = ""

    def render(self) -> str:
        line = f"{_SYMBOLS.get(self.status, '?')} {self.label}"
        if self.hint:
            line = f"{line} —— {self.hint}"
        return line


def parse_version(text: str | None) -> _Version | None:
    """从版本输出里取第一个 x.y[.z]。"""
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def format_version(version: _Version) -> str:
    return ".".join(str(part) for part in version[:3])


def _run_probe(run: _Run, command: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None


def _version_text(command: list[str], run: _Run) -> str | None:
    """运行 `--version` 类命令，返回第一行；失败返回 None。"""
    result = _run_probe(run, command)
    if result is None or result.returncode != 0:
        return None
    lines = ((result.stdout or "") + (result.stderr or "")).strip().splitlines()
    return lines[0].strip() if lines else None


def select_python(repo_root: Path | None = None, which: Callable[[str], str | None] | None = None) -> tuple[Path | None, str]:
    """优先仓库内 .venv 的 python3，其次 PATH 上的 python3。"""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    venv_python = root / ".venv" / "bin" / "python3"
    if venv_python.is_file() and os.access(venv_python, os.X_OK):
        return venv_python, "项目 .venv"
    found = (which or shutil.which)("python3")
    if found:
        return Path(found), "系统 python3"
    return None, ""


def probe_python_version(executable: Path, run: _Run | None = None) -> _Version | None:
    result = _run_probe(
        run or subprocess.run,
        [str(executable), "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
    )
    if result is None or result.returncode != 0:
        return None
    return parse_version(result.stdout)


def probe_missing_modules(
    executable: Path, modules: Sequence[str] = PYTHON_MODULES, run: _Run | None = None,
) -> list[str]:
    """用目标解释器探测模块是否可 import；探测失败时按全部缺失处理。"""
    result = _run_probe(
        run or subprocess.run,
        [str(executable), "-c", _MODULE_PROBE, *modules],
    )
    if result is None or result.returncode != 0:
        return list(modules)
    lines = (result.stdout or "").strip().splitlines()
    if not lines:
        return list(modules)
    try:
        missing = json.loads(lines[-1])
    except ValueError:
        return list(modules)
    return [str(name) for name in missing]


def check_node(which: Callable[[str], str | None] | None = None, run: _Run | None = None) -> Check:
    path = (which or shutil.which)("node")
    if not path:
        return Check(FAIL, "未找到 Node.js（需要 22+）", "安装 Node.js 22+：https://nodejs.org/")
    text = _version_text([path, "--version"], run or subprocess.run)
    version = parse_version(text)
    if version is None:
        return Check(FAIL, f"无法识别 Node.js 版本：{path}", "运行 node --version 确认安装完整")
    if version[0] < MIN_NODE:
        return Check(FAIL, f"Node.js {text} 低于 22", "升级 Node.js 22+：https://nodejs.org/")
    return Check(OK, f"Node.js {text}（>=22）")


def check_npm(which: Callable[[str], str | None] | None = None, run: _Run | None = None) -> Check:
    path = (which or shutil.which)("npm")
    if not path:
        return Check(FAIL, "未找到 npm", "安装 Node.js 22+（随附 npm）：https://nodejs.org/")
    text = _version_text([path, "--version"], run or subprocess.run)
    if text is None:
        return Check(FAIL, f"无法识别 npm 版本：{path}", "运行 npm --version 确认安装完整")
    return Check(OK, f"npm {text}")


def check_python(
    executable: Path | None,
    *,
    source: str = "",
    version_probe: Callable[[Path], _Version | None] | None = None,
    run: _Run | None = None,
) -> Check:
    if executable is None:
        return Check(FAIL, "未找到 Python 3", "安装 Python 3.11+（建议 3.12），然后运行 uv sync --extra test")
    probe = version_probe or (lambda path: probe_python_version(path, run))
    version = probe(Path(executable))
    where = f"（{source}）" if source else ""
    if version is None:
        return Check(FAIL, f"无法识别 Python 版本：{executable}{where}", f"运行 {executable} --version 确认安装完整")
    if version[:2] < MIN_PYTHON:
        return Check(
            FAIL, f"Python {format_version(version)} 低于 3.11{where}",
            "安装 Python 3.11+（建议 3.12），然后运行 uv sync --extra test",
        )
    return Check(OK, f"Python {format_version(version)}{where}")


def check_python_deps(
    executable: Path | None,
    *,
    missing_probe: Callable[[Path, Sequence[str]], list[str]] | None = None,
    run: _Run | None = None,
) -> Check:
    if executable is None:
        return Check(WARN, "跳过 Python 依赖检查（未找到 Python）", "先装 Python 3.11+，再运行 uv sync --extra test")
    probe = missing_probe or (lambda path, modules: probe_missing_modules(path, modules, run))
    missing = probe(Path(executable), PYTHON_MODULES)
    if missing:
        return Check(FAIL, f"缺少 Python 依赖：{', '.join(missing)}", "在项目根目录运行 uv sync --extra test")
    return Check(OK, "Python 依赖齐全（httpx / fastmcp / ddgs）")


def check_uv(which: Callable[[str], str | None] | None = None, run: _Run | None = None) -> Check:
    path = (which or shutil.which)("uv")
    if not path:
        return Check(WARN, "未找到 uv（可选）", "安装 uv 后可运行 uv sync --extra test：https://docs.astral.sh/uv/")
    text = _version_text([path, "--version"], run or subprocess.run)
    version = parse_version(text)
    return Check(OK, f"uv {format_version(version)}" if version else "uv 可用")


def check_node_modules(repo_root: Path | None = None) -> Check:
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    ink = root / "tui" / "node_modules" / "ink"
    if ink.exists():
        return Check(OK, "前端依赖已安装（tui/node_modules/ink）")
    return Check(FAIL, "缺少前端依赖 tui/node_modules/ink", "运行 npm --prefix tui ci")


def is_placeholder_key(value: str) -> bool:
    """模板 / 示例里的占位 key 不算已配置（大小写不敏感）。"""
    lowered = (value or "").strip().lower()
    return any(marker in lowered for marker in API_KEY_PLACEHOLDERS)


def sanitize_base_url(raw: str) -> str:
    """只保留 scheme://host[:port]，避免把 userinfo / query / path 里的凭证打印出来。"""
    value = (raw or "").strip() or DEFAULT_BASE_URL
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return "（已隐藏）"
    if not parts.scheme or not host:
        return "（已隐藏）"
    if ":" in host:  # IPv6
        host = f"[{host}]"
    suffix = f":{port}" if port is not None else ""
    return f"{parts.scheme}://{host}{suffix}"


def check_api_key(env: Mapping[str, str]) -> Check:
    base_url = sanitize_base_url(env.get("VIBE_BASE_URL") or "")
    model = (env.get("VIBE_MODEL") or "").strip() or DEFAULT_MODEL
    values = f"VIBE_BASE_URL={base_url}，VIBE_MODEL={model}"
    placeholder: str | None = None
    for name in API_KEY_NAMES:
        value = (env.get(name) or "").strip()
        if not value:
            continue
        if is_placeholder_key(value):
            placeholder = placeholder or name
            continue
        return Check(OK, f"模型 API Key 已配置（{name}）", values)
    if placeholder:
        return Check(
            FAIL, f"模型 API Key 仍是占位值（{placeholder}）",
            f"把 {placeholder} 换成真实 key 后重试（模板占位值不算配置）；默认 {values}",
        )
    hint = (
        "cp .env.example .env 填入 key，再 set -a; source .env; set +a"
        "（或直接 export VIBE_API_KEY；也接受 OPENAI_API_KEY / DEEPSEEK_API_KEY）"
    )
    return Check(FAIL, "未配置模型 API Key", f"{hint}；默认 {values}")


def check_tty(stdin_tty: bool, stdout_tty: bool) -> Check:
    if stdin_tty and stdout_tty:
        return Check(OK, "交互式终端（stdin / stdout 均为 TTY）")
    states = f"stdin={'TTY' if stdin_tty else '非TTY'}，stdout={'TTY' if stdout_tty else '非TTY'}"
    return Check(WARN, f"当前不是完整交互式终端（{states}）", "交互工作台需要 TTY；--help、--list-sessions 不需要")


def check_workspace(workspace: Path) -> Check:
    if not workspace.exists():
        return Check(FAIL, f"工作区不存在：{workspace}", "检查 --workspace 参数，或先创建该目录")
    if not workspace.is_dir():
        return Check(FAIL, f"工作区不是目录：{workspace}", "把 --workspace 指向一个目录")
    if not os.access(workspace, os.W_OK):
        return Check(FAIL, f"工作区不可写：{workspace}", "换一个可写目录，或修正目录权限")
    return Check(OK, f"工作区存在且可写：{workspace}")


def check_git(git_path: str | None, run: _Run | None = None) -> Check:
    if not git_path:
        return Check(WARN, "未找到 git", "安装 git；/diff、Git 侧栏和 hash 审计会用到，纯对话 / 编辑仍可用")
    text = _version_text([git_path, "--version"], run or subprocess.run)
    version = parse_version(text)
    return Check(OK, f"git {format_version(version)}" if version else "git 可用")


def check_git_repo(workspace: Path, git_path: str | None = None, run: _Run | None = None) -> Check:
    if not git_path:
        return Check(WARN, "跳过 git 仓库检查（未找到 git）")
    if not workspace.is_dir():
        return Check(WARN, "跳过 git 仓库检查（工作区不存在）")
    result = _run_probe(
        run or subprocess.run,
        [git_path, "-C", str(workspace), "rev-parse", "--is-inside-work-tree"],
    )
    if result is not None and result.returncode == 0 and "true" in (result.stdout or "").lower():
        return Check(OK, f"是 git 仓库：{workspace}")
    return Check(
        WARN, f"不是 git 仓库：{workspace}",
        "仍可对话和编辑；/diff、Git 侧栏与基于 hash 的审计会受限",
    )


def collect_checks(
    *,
    repo_root: Path | None = None,
    workspace: Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin_tty: bool | None = None,
    stdout_tty: bool | None = None,
    which: Callable[[str], str | None] | None = None,
    run: _Run | None = None,
    python_path: Path | None = _AUTO,
    python_source: str | None = None,
    python_version: Callable[[Path], _Version | None] | None = None,
    missing_modules: Callable[[Path, Sequence[str]], list[str]] | None = None,
) -> list[Check]:
    """跑全部检查并返回有序清单；所有外部事实都可注入，便于单测。"""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    target = Path(workspace) if workspace is not None else root
    environ: Mapping[str, str] = os.environ if env is None else env
    which_fn = which or shutil.which
    run_fn = run or subprocess.run
    if python_path is _AUTO:
        executable, source = select_python(root, which=which_fn)
    else:
        executable, source = python_path, python_source or ""
    git_path = which_fn("git")
    return [
        check_node(which=which_fn, run=run_fn),
        check_npm(which=which_fn, run=run_fn),
        check_python(executable, source=source, version_probe=python_version, run=run_fn),
        check_python_deps(executable, missing_probe=missing_modules, run=run_fn),
        check_uv(which=which_fn, run=run_fn),
        check_node_modules(root),
        check_api_key(environ),
        check_tty(
            stdin_tty if stdin_tty is not None else sys.stdin.isatty(),
            stdout_tty if stdout_tty is not None else sys.stdout.isatty(),
        ),
        check_workspace(target),
        check_git(git_path, run=run_fn),
        check_git_repo(target, git_path, run=run_fn),
    ]


def exit_code(checks: Sequence[Check]) -> int:
    """有任一阻塞项返回 2，否则 0。"""
    return 2 if any(check.status == FAIL for check in checks) else 0


def render_report(workspace: Path, checks: Sequence[Check]) -> str:
    """把清单渲染成中文报告；末尾一行是结论。"""
    lines = [f"mini-harness 环境自检（workspace: {workspace}）", ""]
    lines.extend(check.render() for check in checks)
    warnings = [check for check in checks if check.status == WARN]
    failures = [check for check in checks if check.status == FAIL]
    if warnings:
        lines.extend(["", f"提示：{len(warnings)} 项警告不影响启动。"])
    lines.append("")
    if failures:
        lines.append(f"结论：缺少 {len(failures)} 项，先按上面的命令补齐")
    else:
        lines.append("结论：可以启动 ./run_ink.sh")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None, **probe_kwargs) -> int:
    """解析参数、打印清单、返回退出码；probe_kwargs 透传给 collect_checks（供测试注入）。"""
    parser = argparse.ArgumentParser(prog="doctor.py", description="检查启动 Ink 工作台所需的环境。")
    parser.add_argument("--workspace", default=None, help="要自检的工作区，默认仓库根目录")
    parser.add_argument("--doctor", action="store_true", help="兼容 ./run_ink.sh --doctor 转发的标记")
    args, _unknown = parser.parse_known_args(list(argv) if argv is not None else None)
    stream = probe_kwargs.pop("out", sys.stdout)
    root = Path(probe_kwargs.pop("repo_root", REPO_ROOT)).absolute()
    workspace = Path(args.workspace).expanduser().absolute() if args.workspace else root
    checks = collect_checks(repo_root=root, workspace=workspace, **probe_kwargs)
    stream.write(render_report(workspace, checks))
    return exit_code(checks)


if __name__ == "__main__":
    raise SystemExit(main())
