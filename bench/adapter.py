"""Harbor 自定义 Agent：把本地 mini-harness 装进任务容器并跑一次。

用法见 bench/README.md；本文件只在 `harbor run` 时被 import，因此 harbor
不是本仓库的运行依赖。
"""

from __future__ import annotations

import os
import shlex
import shutil
import tempfile
from pathlib import Path

try:  # Python 3.12+
    from typing import override
except ImportError:  # Python 3.11
    def override(func):
        return func

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from bench import support

REPO_ROOT = Path(__file__).resolve().parent.parent
GUEST_PKG = "/opt/vibe"
GUEST_SECRET = "/opt/vibe_secret"
KEY_FILE = f"{GUEST_SECRET}/api.key"
LOG_DIR = "/logs/agent"
UV_ENV = '. "$HOME/.local/bin/env"'
AGENT_VERSION = support.agent_version(REPO_ROOT)


class VibeAgent(BaseInstalledAgent):
    """把 `python -m app --task ... --bench` 包装成 Harbor agent。"""

    SUPPORTS_ATIF: bool = False

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        provider: str | None = None,
        base_url: str | None = None,
        api_key_env: str | None = None,
        max_steps: int | None = None,
        max_run_seconds: float | None = None,
        **kwargs,
    ):
        settings = {**os.environ, **(kwargs.get("extra_env") or {})}
        resolved = support.resolve_agent_settings(
            settings,
            model_name=model_name,
            provider=provider,
            base_url=base_url,
            api_key_env=api_key_env,
            max_steps=max_steps,
            max_run_seconds=max_run_seconds,
        )
        self._resolved = resolved
        super().__init__(
            logs_dir=logs_dir,
            model_name=f"{resolved['provider']}/{resolved['model']}",
            **kwargs,
        )

    @staticmethod
    @override
    def name() -> str:
        return "vibe-mini-harness"

    @override
    def get_version_command(self) -> str | None:
        return None

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command="apt-get update && apt-get install -y curl ca-certificates",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "pkg"
            stage.mkdir()
            for name in ("pyproject.toml", "README.md"):
                source = REPO_ROOT / name
                if source.exists():
                    shutil.copy(source, stage / name)
            for name in ("app", "core"):
                shutil.copytree(
                    REPO_ROOT / name, stage / name,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
            requirements = stage / "bench" / "requirements-bench.txt"
            requirements.parent.mkdir(parents=True)
            shutil.copy(REPO_ROOT / "bench" / "requirements-bench.txt", requirements)
            await environment.upload_dir(stage, GUEST_PKG)
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "secret"
            secret.mkdir()
            (secret / "api.key").write_text(self._resolved["api_key"], encoding="utf-8")
            await environment.upload_dir(secret, GUEST_SECRET)
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                "curl -LsSf https://astral.sh/uv/install.sh | sh; "
                f"{UV_ENV}; "
                "uv venv --python 3.12 \"$HOME/vibe-venv\"; "
                "uv pip install --python \"$HOME/vibe-venv/bin/python\" "
                f"-r {GUEST_PKG}/bench/requirements-bench.txt; "
                f"PYTHONPATH={GUEST_PKG} \"$HOME/vibe-venv/bin/python\" -c 'import app, core'"
            ),
        )

    @override
    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        escaped = shlex.quote(instruction)
        await self.exec_as_agent(
            environment,
            command=(
                "set -o pipefail; "
                f"{UV_ENV}; "
                f'export PYTHONPATH={GUEST_PKG}; '
                f'export VIBE_API_KEY="$(cat {KEY_FILE} 2>/dev/null)"; '
                f"rm -f {KEY_FILE}; "
                'test -n "$VIBE_API_KEY" || exit 3; '
                f"mkdir -p {LOG_DIR}; "
                f'"$HOME/vibe-venv/bin/python" -m app --task {escaped} --bench '
                f'--workspace "$PWD" '
                f"--telemetry-out {LOG_DIR}/telemetry.json "
                f"--runtime-dir {LOG_DIR}/vibe "
                f"2>&1 | tee {LOG_DIR}/agent.log"
            ),
            env={
                **self._resolved["env"],
                "VIBE_BENCH_AGENT_VERSION": AGENT_VERSION,
                "PYTHONUNBUFFERED": "1",
            },
        )

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        try:
            telemetry = support.read_telemetry(Path(self.logs_dir) / "telemetry.json")
            usage = telemetry.get("usage") or {}
            context.n_input_tokens = int(usage.get("prompt_tokens") or 0)
            context.n_output_tokens = int(usage.get("completion_tokens") or 0)
        except Exception as exc:
            print(f"[adapter]: telemetry read failed for {self.logs_dir}: {type(exc).__name__}: {exc}")
