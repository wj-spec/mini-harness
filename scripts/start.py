#!/usr/bin/env python3
"""准备本地依赖、配置模型并启动现有 Ink 入口。"""
from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    print("需要 Python 3.11+（建议 3.12）。安装后重新运行 ./start.sh。", file=sys.stderr)
    raise SystemExit(2)

import argparse
import json
import asyncio
import getpass
import os
import shlex
import shutil
import subprocess
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.runtime.model_config import configured_key
from app.runtime.user_config import config_path, credential_path, startup_environment
from app.safety.policy import ApprovalDecision, ApprovalGate
from core.contracts import ToolCall
from scripts.doctor import DEFAULT_BASE_URL, DEFAULT_MODEL, is_placeholder_key, parse_version
from scripts.launcher_config import MODEL_NAMES, load_environment, save_model_config


class SetupError(Exception):
    pass


class Cancelled(Exception):
    pass


def config_problem_message(error: Exception) -> str:
    # load_environment 的错误只包含行号/路径，不回显文件内容；这里仍只拼接安全文本。
    detail = str(error).strip()
    prefix = "配置文件无法解析。"
    if detail:
        prefix = f"配置文件无法解析：{detail}"
    return prefix + " 原文件未修改；请修正后重新启动。"



class Actions:
    def __init__(self, root: Path, yes: bool = False):
        self.root = root
        self.yes = yes
        self.confirm = False
        self.gate = ApprovalGate(self._approve, audit_path=root / ".vibe" / "setup-audit.jsonl")

    async def _approve(self, call: ToolCall, preview: str) -> ApprovalDecision:
        if self.confirm:
            print(preview)
            if not self.yes and input("继续？[Y/n] ").strip().lower() not in {"", "y", "yes"}:
                return ApprovalDecision(False, "deny")
        return ApprovalDecision(True, "once", "用户请求的启动/配置操作")

    def permit(self, call: ToolCall, preview: str, *, confirm: bool = False) -> None:
        self.confirm = confirm
        try:
            result = asyncio.run(self.gate.request(call, preview, self.root))
        finally:
            self.confirm = False
        if not result.allowed:
            raise Cancelled()

    def commands(self, commands: list[list[str]], *, env: dict[str, str], confirm: bool = False) -> int:
        preview = "\n".join("  " + shlex.join(command) for command in commands)
        self.permit(ToolCall("bash", {"command": preview}), preview, confirm=confirm)
        for command in commands:
            result = subprocess.run(command, cwd=self.root, env=env)
            if result.returncode:
                return result.returncode
        return 0

    def probe(self, command: list[str], env: dict[str, str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
        preview = shlex.join(command)
        self.permit(ToolCall("bash", {"command": preview}), preview)
        return subprocess.run(command, cwd=cwd or self.root, env=env, capture_output=True, text=True, timeout=20)


def validate_endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.port != 0
        valid = valid and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
    except ValueError:
        valid = False
    if not valid or any(character.isspace() for character in value):
        raise ValueError("服务地址需要是 http(s) URL，不能包含账号密码、查询参数或片段。")
    return value.rstrip("/")


def configure(root: Path, env: dict[str, str], actions: Actions) -> bool:
    print("\n配置你的模型（Ctrl+C 取消）")
    print("  1. DeepSeek\n  2. 自定义 OpenAI 兼容接口\n  3. 先体验本地演示（无需 Key，不保存模型配置）")
    choice = input("选择 [1]：").strip() or "1"
    while choice not in {"1", "2", "3"}:
        choice = input("请输入 1、2 或 3：").strip()
    if choice == "3":
        return True
    if choice == "1":
        endpoint = DEFAULT_BASE_URL
        model = DEFAULT_MODEL
        print(f"使用默认服务：{endpoint} · {model}（其他模型可选择自定义接口）")
    else:
        endpoint = validate_endpoint(input("服务地址（例如 https://example.com/v1）：").strip())
        model = input("模型名（按服务商提供的名称填写）：").strip()
    if not model or not model.isprintable():
        raise ValueError("请输入有效的模型名称。")
    # 切换服务时重新输入，避免误用旧服务的 key。
    key = getpass.getpass("API Key（输入隐藏）：").strip()
    if not key or is_placeholder_key(key) or not key.isprintable():
        raise ValueError("请输入真实 API Key；无需 Key 的体验请选择本地演示。")
    values = {"VIBE_PROVIDER": "openai", "VIBE_BASE_URL": endpoint, "VIBE_MODEL": model, "VIBE_API_KEY": key}
    path = config_path(env)
    targets = [str(path)] + ([str(credential_path(path))] if path.suffix == ".toml" else [])
    preview = f"保存用户配置（所有项目通用）到 {', '.join(targets)}\n服务：{endpoint}\n模型：{model}\nAPI Key：已填写（隐藏）"
    actions.permit(ToolCall("write_file", {"path": str(path), "paths": targets, "keys": sorted(values)}), preview, confirm=True)
    if path.suffix == ".toml":
        actions.permit(ToolCall("write_file", {"path": str(credential_path(path))}),
                       f"写入已确认的独立凭据文件 {credential_path(path)}；Key 隐藏。")
    save_model_config(path, values)
    print("模型配置已保存。后续运行 ./start.sh 即可，无需 source。")
    return False


def prepare_dependencies(root: Path, env: dict[str, str], actions: Actions, force: bool = False) -> None:
    node, npm = shutil.which("node", path=env.get("PATH")), shutil.which("npm", path=env.get("PATH"))
    if not node or not npm:
        raise SetupError("需要 Node.js 22+（包含 npm）：https://nodejs.org/ 。安装后重新运行 ./start.sh。")
    result = actions.probe([node, "--version"], env)
    version = parse_version(result.stdout)
    if result.returncode or not version or version[0] < 22:
        raise SetupError("需要 Node.js 22+。请升级后重新运行 ./start.sh。")
    local_python = root / ".venv" / "bin" / "python3"
    python = env.get("PYTHON") or str(local_python)
    ready = False
    if Path(python).is_file() or shutil.which(python, path=env.get("PATH")):
        result = actions.probe([python, "-c", "import httpx, fastmcp, ddgs"], env)
        ready = result.returncode == 0
    commands: list[list[str]] = []
    if force or not ready:
        if env.get("PYTHON"):
            if not ready:
                raise SetupError("PYTHON 指定的解释器缺少依赖；请自行安装，或取消 PYTHON 后使用项目 .venv。")
        else:
            uv = shutil.which("uv", path=env.get("PATH"))
            if uv:
                env["UV_PROJECT_ENVIRONMENT"] = str(root / ".venv")
                commands.append([uv, "sync", "--frozen", "--inexact"])
            else:
                if not local_python.is_file():
                    commands.append([sys.executable, "-m", "venv", str(root / ".venv")])
                dependencies = tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
                commands.extend([
                    [str(local_python), "-m", "ensurepip", "--upgrade"],
                    [str(local_python), "-m", "pip", "install", *dependencies],
                ])
    frontend_ready = False
    if (root / "tui" / "node_modules" / "ink").is_dir():
        check = "await import('ink'); await import('react'); await import('typescript'); await import('string-width');"
        frontend_ready = actions.probe([node, "--input-type=module", "-e", check], env, cwd=root / "tui").returncode == 0
    if force or not frontend_ready:
        commands.append([npm, "--prefix", "tui", "ci"])
    if commands:
        if not actions.yes and not sys.stdin.isatty():
            raise SetupError("需要安装依赖。请在交互终端运行 ./start.sh，或使用 ./start.sh --install --yes。")
        print("\n将下载项目依赖到 .venv/ 和 tui/node_modules/（需要联网）：")
        if actions.commands(commands, env=env, confirm=True):
            raise SetupError("依赖安装失败。检查网络和上方提示后重新运行 ./start.sh；已有模型配置会保留。")
    if not env.get("PYTHON"):
        env["PYTHON"] = str(local_python)


def main(argv: list[str] | None = None, *, root: Path = ROOT, environ: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="./start.sh", description="首次自动准备依赖与模型配置，后续直接进入 Ink 工作台。",
        epilog="其余参数转发给 Ink，例如 --model NAME、--base-url URL、--continue、--autonomy ask。配置分层见 docs/CONFIGURATION.md；开发调试见 docs/CONFIGURATION-DEV.md。",
    )
    parser.add_argument("--acceptance", action="store_true", help="隔离工作区、Demo 模型和本地 MCP 的体验验收")
    parser.add_argument("--demo", action="store_true", help="无需 Key 的本地审批/修改/验证演示")
    parser.add_argument("--fake", action="store_true", help="本地 fake 模式")
    parser.add_argument("--configure", action="store_true", help="重新配置模型并退出")
    parser.add_argument("--install", action="store_true", help="安装/同步项目依赖后退出")
    parser.add_argument("--yes", action="store_true", help="同意启动器的依赖安装与配置保存；不改变 Agent 审批")
    parser.add_argument("--doctor", action="store_true", help="读取用户配置后运行环境自检，不安装依赖")
    parser.add_argument("--workspace", help="目标仓库；默认调用命令时所在目录")
    parser.add_argument("--provider", choices=["openai", "fake"], help="模型 provider")
    options, rest = parser.parse_known_args(argv)
    if sum([options.configure, options.install, options.doctor]) > 1:
        parser.error("--configure、--install、--doctor 不能同时使用")
    if options.acceptance and (options.configure or options.install or options.doctor or options.workspace or options.provider or rest):
        parser.error("--acceptance 使用独立配置与工作区，请单独运行。")
    root = root.resolve()
    shell_env = dict(os.environ if environ is None else environ)
    actions = Actions(root, options.yes)
    workspace = str(Path(options.workspace).expanduser().resolve()) if options.workspace else str(Path.cwd())
    try:
        if options.acceptance:
            from scripts.workbench_acceptance import launch
            clean = {key:value for key,value in shell_env.items() if not key.startswith(("VIBE_", "OPENAI_", "DEEPSEEK_"))}
            prepare_dependencies(root, clean, actions, False)
            return launch(root, actions, clean)
        load_problem = ""
        try:
            env = dict(shell_env) if options.configure else startup_environment(root, Path(workspace), shell_env)
        except (ValueError, OSError) as error:
            load_problem = config_problem_message(error)
            env = dict(shell_env)
        if load_problem and options.doctor:
            raise SetupError(load_problem + " 请先修正配置文件后重试；--doctor 不会自动改写配置。")
        if load_problem and options.configure:
            raise SetupError(load_problem + " 请先手动修正配置文件后重试；--configure 不会覆盖无法解析的文件。")
        if load_problem and options.install:
            print(load_problem + " 本次安装不读取该文件。", file=sys.stderr)
        if options.doctor:
            if options.provider:
                env["VIBE_PROVIDER"] = options.provider
            if options.fake or options.demo or options.provider == "fake":
                env["VIBE_PROVIDER"] = "fake"
            return actions.commands([[sys.executable, str(root / "scripts" / "doctor.py"), "--doctor", "--workspace", workspace, *rest]], env=env)
        if options.configure:
            if not sys.stdin.isatty():
                raise SetupError("模型配置需要交互终端，请运行 ./start.sh --configure。")
            if configure(root, env, actions):
                print("运行 ./start.sh --demo 即可体验，无需保存 Key。")
            if any(name in shell_env for name in MODEL_NAMES):
                print("提示：当前 shell 中的模型环境变量优先于保存的配置；取消这些变量后才会使用文件中的对应值。")
            return 0
        prepare_dependencies(root, env, actions, options.install)
        if options.install:
            print("依赖已准备好。运行 ./start.sh，或 ./start.sh --demo 体验。")
            return 0
        demo = options.demo
        fake = options.demo or options.fake or options.provider == "fake"
        fake = fake or (options.provider is None and env.get("VIBE_PROVIDER") == "fake")
        if load_problem and "--list-sessions" not in rest:
            if fake:
                print(load_problem + " 本次 Demo 使用独立测试配置。", file=sys.stderr)
            elif not sys.stdin.isatty():
                raise SetupError(load_problem + " 请在交互终端运行 ./start.sh 查看配置问题页，"
                                                 "或手动修正文件后重试；也可运行 ./start.sh --demo。")
            else:
                print(load_problem + " 正在打开 Ink 配置问题页。", file=sys.stderr)
        if not fake and not load_problem and "--list-sessions" not in rest and not configured_key(env):
            if not sys.stdin.isatty():
                raise SetupError(
                    "尚未配置模型。请在交互终端运行 ./start.sh 由界面引导配置，"
                    "或运行 ./start.sh --configure 配置、./start.sh --demo 体验。"
                )
            # 不再在终端问答：交给 Ink 首次配置页；Key 只经审批写入凭据文件。
            print("尚未配置模型：正在打开 Ink 引导配置。", file=sys.stderr)
        if demo:
            env["VIBE_FAKE_DEMO"] = "1"
        command = ["bash", str(root / "run_ink.sh"), "--workspace", workspace]
        if fake:
            command.append("--fake")
        elif options.provider:
            command.extend(["--provider", options.provider])
        command.extend(rest)
        env["VIBE_CONFIG_FILE"] = str(config_path(shell_env))
        env["VIBE_CONFIG_LOADED"] = "1"
        env["VIBE_SETTINGS_ENV_KEYS"] = json.dumps(sorted(name for name in shell_env if name.startswith("VIBE_")))
        env["VIBE_MODEL_ENV_KEYS"] = json.dumps(sorted(name for name in shell_env if name in MODEL_NAMES))
        if load_problem and not fake and "--list-sessions" not in rest:
            env["VIBE_BOOTSTRAP"] = "1"
            env["VIBE_CONFIG_PROBLEM"] = load_problem
        return actions.commands([command], env=env)
    except (EOFError, KeyboardInterrupt, Cancelled):
        print("\n已取消。", file=sys.stderr)
        return 130
    except (ValueError, OSError, subprocess.SubprocessError, SetupError) as error:
        print(f"启动未完成：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
