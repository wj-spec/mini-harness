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


DEFAULT_EVENTS_MAX_BYTES = 32 * 1024 * 1024


def _sessions_root(workspace: Path) -> Path:
    return workspace / ".vibe" / "sessions"


def count_pending_approvals(workspace: Path) -> int:
    """只读统计跨 session/task 的 pending 审批（不 import app，保持脚本可独立运行）。"""
    root = _sessions_root(workspace)
    if not root.is_dir():
        return 0
    total = 0
    for session in sorted(root.iterdir()):
        if not session.is_dir() or session.is_symlink():
            continue
        tasks = session / "tasks"
        if not tasks.is_dir():
            continue
        for task in sorted(tasks.iterdir()):
            if not task.is_dir() or task.is_symlink():
                continue
            path = task / "approvals.jsonl"
            if not path.is_file():
                continue
            states: dict[str, str] = {}
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                approval_id = event.get("approval_id")
                kind = event.get("type")
                if isinstance(approval_id, str) and isinstance(kind, str):
                    states[approval_id] = kind
            total += sum(1 for kind in states.values() if kind == "requested")
    return total


def check_pending_approvals(workspace: Path) -> Check:
    count = count_pending_approvals(workspace)
    if count:
        return Check(
            WARN, f"有 {count} 个待审批动作",
            "运行 ./run_vibe.sh --approvals --workspace <ws> 查看；无 TUI 时用 --decide-approval <id> 处理",
        )
    return Check(OK, "没有待审批动作")


def _events_soft_limit(env: Mapping[str, str]) -> int:
    try:
        value = int((env.get("VIBE_EVENTS_MAX_BYTES") or "").strip())
    except ValueError:
        return DEFAULT_EVENTS_MAX_BYTES
    return max(0, value)


def oversized_session_events(workspace: Path, limit: int) -> list[dict]:
    root = _sessions_root(workspace)
    if limit <= 0 or not root.is_dir():
        return []
    items: list[dict] = []
    for session in sorted(root.iterdir()):
        if not session.is_dir() or session.is_symlink():
            continue
        path = session / "events.jsonl"
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > limit:
            items.append({"session": session.name, "bytes": size})
    return items


def check_session_logs(workspace: Path, env: Mapping[str, str] | None = None) -> Check:
    environ: Mapping[str, str] = os.environ if env is None else env
    limit = _events_soft_limit(environ)
    if limit <= 0:
        return Check(OK, "events 软上限告警已关闭（VIBE_EVENTS_MAX_BYTES=0）")
    oversized = oversized_session_events(workspace, limit)
    if oversized:
        names = "、".join(item["session"] for item in oversized[:3])
        return Check(
            WARN, f"{len(oversized)} 个会话 events.jsonl 超过软上限（{limit} 字节）：{names}",
            "运行 ./run_vibe.sh --cleanup-logs --workspace <ws> 查看；events 不自动轮转",
        )
    return Check(OK, "会话 events.jsonl 未超过软上限")


def stale_worktrees(workspace: Path) -> list[dict]:
    root = workspace / ".vibe" / "worktrees"
    if not root.is_dir():
        return []
    items: list[dict] = []
    for meta_path in sorted(root.glob("*.meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            items.append({"task_id": meta_path.name.removesuffix(".meta.json"), "reason": "meta 不可读"})
            continue
        path = meta.get("path") if isinstance(meta, dict) else None
        if not isinstance(path, str) or not Path(path).is_dir():
            items.append({"task_id": meta_path.name.removesuffix(".meta.json"), "reason": "目录缺失"})
    return items


def check_worktrees(workspace: Path) -> Check:
    stale = stale_worktrees(workspace)
    if stale:
        names = "、".join(item["task_id"] for item in stale[:3])
        return Check(WARN, f"{len(stale)} 个 worktree 目录缺失或 meta 不可读：{names}",
                     "用 ./run_vibe.sh --tasks 核对；确认后手动清理 .vibe/worktrees/<task_id>.meta.json")
    return Check(OK, "worktree 元数据完整")


UNATTENDED_PROFILE_KEYS = {
    "schema_version", "profile_id", "allow_read", "allow_write", "allow_exec",
    "allow_network", "allow_mcp", "allow_remote_action", "require_worktree", "approval_mode",
}


def check_unattended_profile(workspace: Path, env: Mapping[str, str] | None = None) -> Check:
    environ: Mapping[str, str] = os.environ if env is None else env
    path = workspace / ".vibe" / "unattended.json"
    override = (environ.get("VIBE_UNATTENDED_PROFILE") or "").strip()
    if override:
        path = Path(override).expanduser()
    if not path.exists():
        return Check(OK, "无人值守 profile 使用默认保守策略")
    if path.is_symlink():
        return Check(FAIL, "无人值守 profile 不能是符号链接",
                     "把 .vibe/unattended.json 换成普通文件；worker 会拒绝启动")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Check(FAIL, f"无人值守 profile 不可读或不是合法 JSON：{exc}",
                     "修正 .vibe/unattended.json，或删除它使用默认保守 profile")
    if not isinstance(raw, dict):
        return Check(FAIL, "无人值守 profile 必须是 JSON 对象", "修正 .vibe/unattended.json")
    if raw.get("schema_version", 1) != 1:
        return Check(FAIL, "无人值守 profile schema_version 不支持", "删除或改成 {\"schema_version\": 1}")
    unknown = set(raw) - UNATTENDED_PROFILE_KEYS
    if unknown:
        return Check(FAIL, f"无人值守 profile 有未知键：{sorted(unknown)}",
                     "只保留已知字段；未知键会让 worker 拒绝启动")
    relaxed = [
        name for name in ("allow_exec", "allow_network", "allow_mcp", "allow_remote_action")
        if raw.get(name) is True
    ]
    if raw.get("require_worktree") is False:
        relaxed.append("require_worktree=false")
    if raw.get("approval_mode") not in (None, "queue"):
        relaxed.append(f"approval_mode={raw.get('approval_mode')}")
    approval_required = (environ.get("VIBE_UNATTENDED_REQUIRE_APPROVAL") or "").strip().lower() in {
        "1", "true", "yes",
    }
    if relaxed:
        if approval_required:
            approval_path = workspace / ".vibe" / "unattended.approval.json"
            if not approval_path.is_file():
                return Check(
                    WARN,
                    f"无人值守 profile 放宽了：{', '.join(relaxed)}（要求审批但缺少批准记录）",
                    "运行 ./run_vibe.sh --approve-unattended-profile --workspace <ws>；"
                    "否则 worker 会拒绝启动",
                )
            return Check(OK, f"无人值守 profile 已批准（放宽：{', '.join(relaxed)}）")
        return Check(WARN, f"无人值守 profile 放宽了：{', '.join(relaxed)}",
                     "确认 .vibe/unattended.json 的放宽范围；worker 启动会写 unattended_profile_relaxed 通知")
    return Check(OK, "无人值守 profile 使用默认保守策略")


def bench_history_rows(workspace: Path) -> list[dict]:
    root = workspace / ".vibe" / "bench-long"
    if not root.is_dir():
        return []
    rows: list[dict] = []
    for path in sorted(root.glob("*/report.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        summary = payload.get("summary") or {}
        rows.append({
            "generated_at": payload.get("generated_at"),
            "mode": payload.get("mode"),
            "agent_version": payload.get("agent_version"),
            "total": summary.get("total"),
            "passed": summary.get("passed"),
            "failed": summary.get("failed"),
        })
    rows.sort(key=lambda item: str(item.get("generated_at") or ""))
    return rows


def check_bench_history(workspace: Path) -> Check:
    rows = bench_history_rows(workspace)
    if not rows:
        return Check(OK, "没有长任务 benchmark 历史（可选）")
    latest = rows[-1]
    passed = int(latest.get("passed") or 0)
    total = int(latest.get("total") or 0)
    failed = int(latest.get("failed") or 0)
    if len(rows) < 2:
        return Check(OK, f"长任务 benchmark 历史 {len(rows)} 次（最近 {passed}/{total}）")
    previous = rows[-2]
    previous_passed = int(previous.get("passed") or 0)
    if failed > 0 or passed < previous_passed:
        return Check(
            WARN, f"最近长任务 benchmark 有回归：{passed}/{total}"
                  f"（上次 {previous_passed}/{previous.get('total')}）",
            "运行 scripts/bench_long.py 复跑；--bench-history 查看历史与 agent_version",
        )
    return Check(OK, f"最近长任务 benchmark 无回归（{passed}/{total}）")


def real_suite_rows(workspace: Path) -> list[dict]:
    root = workspace / ".vibe" / "real-p1-acceptance"
    if not root.is_dir():
        return []
    rows: list[dict] = []
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            rows.append({"path": str(path), **payload})
    return rows


def check_real_suite(workspace: Path) -> Check:
    rows = real_suite_rows(workspace)
    if not rows:
        return Check(OK, "没有真实模型验收报告（可选）")
    latest = rows[-1]
    if latest.get("ok") is True:
        return Check(OK, f"最近真实模型验收通过（agent {latest.get('agent_version') or '?'}）")
    reason = str(latest.get("error") or latest.get("stage") or "unknown")[:80]
    return Check(
        WARN, f"最近真实模型验收失败：{reason}",
        "查看 .vibe/real-p1-acceptance 最新报告；运行 scripts/real_p1_acceptance.py --suite 复验",
    )


def check_bench_storage(workspace: Path) -> Check:
    root = workspace / ".vibe" / "bench-long"
    if not root.is_dir():
        return Check(OK, "没有长任务 benchmark 运行目录")
    run_dirs = [path for path in root.iterdir()
                if path.is_dir() and re.fullmatch(r"\d{8}T\d{6}Z", path.name)]
    total = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    size_mb = total / (1024 * 1024)
    if len(run_dirs) > 50:
        return Check(
            WARN, f"benchmark 运行目录 {len(run_dirs)} 个（{size_mb:.1f} MiB），建议清理",
            "运行 --cleanup-bench --bench-keep 20（--log-dry-run 先预演）",
        )
    return Check(OK, f"benchmark 运行目录 {len(run_dirs)} 个（{size_mb:.1f} MiB）")


def _project_version(path: Path) -> str | None:
    try:
        import tomllib

        with path.open("rb") as handle:
            return str(tomllib.load(handle).get("project", {}).get("version") or "") or None
    except Exception:
        return None


def _package_version(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = payload.get("version")
    return str(version) if version else None


def check_version_consistency(repo_root: Path) -> Check:
    pyproject = repo_root / "pyproject.toml"
    package = repo_root / "tui" / "package.json"
    python_version = _project_version(pyproject) if pyproject.is_file() else None
    tui_version = _package_version(package) if package.is_file() else None
    if python_version is None or tui_version is None:
        return Check(OK, "版本文件不完整，跳过 Python/TUI 版本一致性检查")
    if python_version != tui_version:
        return Check(FAIL, f"Python 版本 {python_version} 与 TUI 版本 {tui_version} 不一致",
                     "同步 pyproject.toml 与 tui/package.json 的 version")
    return Check(OK, f"Python/TUI 版本一致：{python_version}")


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
        check_pending_approvals(target),
        check_session_logs(target, environ),
        check_worktrees(target),
        check_unattended_profile(target, environ),
        check_bench_history(target),
        check_bench_storage(target),
        check_real_suite(target),
        check_version_consistency(root),
    ]


def exit_code(checks: Sequence[Check]) -> int:
    """有任一阻塞项返回 2，否则 0。"""
    return 2 if any(check.status == FAIL for check in checks) else 0


def render_report(
    workspace: Path, checks: Sequence[Check], build: dict | None = None,
) -> str:
    """把清单渲染成中文报告；末尾一行是结论。"""
    detail = f"workspace: {workspace}"
    if build:
        detail += f"；version {build.get('version') or '?'}，agent {build.get('agent_version') or '?'}"
    lines = [f"mini-harness 环境自检（{detail}）", ""]
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


def build_info(repo_root: Path) -> dict:
    """构建信息（version / git short SHA）；读取失败时降级为 unknown。"""
    version = "unknown"
    try:
        import tomllib

        with (repo_root / "pyproject.toml").open("rb") as handle:
            version = str(tomllib.load(handle).get("project", {}).get("version") or version)
    except Exception:
        pass
    agent_version = "unknown"
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            agent_version = out.stdout.strip()
    except Exception:
        pass
    return {"version": version, "agent_version": agent_version}


def main(argv: Sequence[str] | None = None, **probe_kwargs) -> int:
    """解析参数、打印清单、返回退出码；probe_kwargs 透传给 collect_checks（供测试注入）。"""
    parser = argparse.ArgumentParser(prog="doctor.py", description="检查启动 Ink 工作台所需的环境。")
    parser.add_argument("--workspace", default=None, help="要自检的工作区，默认仓库根目录")
    parser.add_argument("--doctor", action="store_true", help="兼容 ./run_ink.sh --doctor 转发的标记")
    parser.add_argument("--json", action="store_true", help="机器可读输出（脚本 / CI 用）")
    args, _unknown = parser.parse_known_args(list(argv) if argv is not None else None)
    stream = probe_kwargs.pop("out", sys.stdout)
    root = Path(probe_kwargs.pop("repo_root", REPO_ROOT)).absolute()
    workspace = Path(args.workspace).expanduser().absolute() if args.workspace else root
    checks = collect_checks(repo_root=root, workspace=workspace, **probe_kwargs)
    if args.json:
        info = build_info(root)
        payload = {
            "workspace": str(workspace),
            **info,
            "ok": exit_code(checks) == 0,
            "failures": [check.label for check in checks if check.status == FAIL],
            "warnings": [check.label for check in checks if check.status == WARN],
            "checks": [
                {"status": check.status, "label": check.label, "hint": check.hint}
                for check in checks
            ],
        }
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        stream.write(render_report(workspace, checks, build_info(root)))
    return exit_code(checks)


if __name__ == "__main__":
    raise SystemExit(main())
