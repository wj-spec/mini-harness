from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from core.budget import BudgetPolicy


def _optional_int_env(name: str) -> int | None:
    value = os.getenv(name)
    return int(value) if value is not None else None


@dataclass
class Config:
    provider: str = "openai"
    model: str = "deepseek-chat"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    workspace: Path = field(default_factory=Path.cwd)
    max_steps: int = 0
    max_run_seconds: float = 600.0
    llm_timeout: float = 120.0
    llm_retries: int = 1
    llm_retry_backoff: float = 0.5
    max_continuations: int = 2
    max_no_progress: int = 2
    max_calls_without_progress: int = 20
    max_reads_per_file: int = 5
    max_progress_warnings: int = 4
    max_repeated_calls: int = 6
    max_run_tokens: int = 0
    compact_interval_tokens: int = 200_000
    max_run_cost_usd: float = 0.0
    tool_retries: int = 1
    keep_recent_groups: int = 8
    context_window: int = BudgetPolicy.context_window
    reserve_output: int | None = None
    system_cap: int | None = None
    memory_cap: int | None = None
    tools_cap: int | None = None
    history_cap: int | None = None
    scratch_cap: int | None = None
    summary_model: str = ""
    summary_max_chars: int = 4000
    agents_max_chars: int = 12_000
    stream_usage: bool = True
    input_price_per_1m: float = 0.0
    output_price_per_1m: float = 0.0
    trust_env: bool = False
    mcp_config: str = ""
    mcp_servers: str = ""
    skills_config: str = ""
    sandbox: bool = False
    sandbox_network: bool = False
    sandbox_network_allow: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            provider=os.getenv("VIBE_PROVIDER", "openai"),
            model=os.getenv("VIBE_MODEL", "deepseek-chat"),
            api_key=(
                os.getenv("VIBE_API_KEY")
                or os.getenv("OPENAI_API_KEY")
                or os.getenv("DEEPSEEK_API_KEY")
                or ""
            ),
            base_url=os.getenv(
                "VIBE_BASE_URL",
                os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1"),
            ),
            workspace=Path(os.getenv("VIBE_WORKSPACE", Path.cwd())),
            max_steps=int(os.getenv("VIBE_MAX_STEPS", str(cls.max_steps))),
            max_run_seconds=float(os.getenv("VIBE_MAX_RUN_SECONDS", "600")),
            llm_timeout=float(os.getenv("VIBE_LLM_TIMEOUT", "120")),
            llm_retries=int(os.getenv("VIBE_LLM_RETRIES", "1")),
            llm_retry_backoff=float(os.getenv("VIBE_LLM_RETRY_BACKOFF", "0.5")),
            max_continuations=int(os.getenv("VIBE_MAX_CONTINUATIONS", "2")),
            max_no_progress=int(os.getenv("VIBE_MAX_NO_PROGRESS", "2")),
            max_calls_without_progress=int(os.getenv("VIBE_MAX_CALLS_WITHOUT_PROGRESS", "20")),
            max_reads_per_file=int(os.getenv("VIBE_MAX_READS_PER_FILE", "5")),
            max_progress_warnings=int(os.getenv("VIBE_MAX_PROGRESS_WARNINGS", "4")),
            max_repeated_calls=int(os.getenv("VIBE_MAX_REPEATED_CALLS", "6")),
            max_run_tokens=int(os.getenv("VIBE_MAX_RUN_TOKENS", str(cls.max_run_tokens))),
            compact_interval_tokens=int(os.getenv(
                "VIBE_COMPACT_INTERVAL_TOKENS", str(cls.compact_interval_tokens),
            )),
            max_run_cost_usd=float(os.getenv("VIBE_MAX_RUN_COST_USD", "0")),
            tool_retries=int(os.getenv("VIBE_TOOL_RETRIES", "1")),
            keep_recent_groups=int(os.getenv("VIBE_KEEP_RECENT_GROUPS", "8")),
            context_window=int(os.getenv(
                "VIBE_CONTEXT_WINDOW", os.getenv("VIBE_MAX_TOKENS", str(cls.context_window)),
            )),
            reserve_output=_optional_int_env("VIBE_RESERVE_OUTPUT"),
            system_cap=_optional_int_env("VIBE_SYSTEM_CAP"),
            memory_cap=_optional_int_env("VIBE_MEMORY_CAP"),
            tools_cap=_optional_int_env("VIBE_TOOLS_CAP"),
            history_cap=_optional_int_env("VIBE_HISTORY_CAP"),
            scratch_cap=_optional_int_env("VIBE_SCRATCH_CAP"),
            summary_model=os.getenv("VIBE_SUMMARY_MODEL", ""),
            summary_max_chars=int(os.getenv("VIBE_SUMMARY_MAX_CHARS", "4000")),
            agents_max_chars=int(os.getenv("VIBE_AGENTS_MAX_CHARS", "12000")),
            stream_usage=os.getenv("VIBE_STREAM_USAGE", "1").lower() in {"1", "true", "yes"},
            input_price_per_1m=float(os.getenv("VIBE_INPUT_PRICE_PER_1M", "0")),
            output_price_per_1m=float(os.getenv("VIBE_OUTPUT_PRICE_PER_1M", "0")),
            trust_env=os.getenv("VIBE_TRUST_ENV", "0").lower() in {"1", "true", "yes"},
            mcp_config=os.getenv("VIBE_MCP_CONFIG", ""),
            mcp_servers=os.getenv("VIBE_MCP_SERVERS", ""),
            skills_config=os.getenv("VIBE_SKILLS_CONFIG", ""),
            sandbox=os.getenv("VIBE_SANDBOX", "0").lower() in {"1", "true", "yes"},
            sandbox_network=os.getenv("VIBE_SANDBOX_NETWORK", "0").lower() in {"1", "true", "yes"},
            sandbox_network_allow=os.getenv("VIBE_SANDBOX_NETWORK_ALLOW", ""),
        )

    def label(self) -> str:
        return f"{self.model} · {self.workspace}"

    def budget_policy(self) -> BudgetPolicy:
        return BudgetPolicy.for_context_window(
            context_window=self.context_window,
            output_cap=self.reserve_output,
            system_cap=self.system_cap,
            memory_cap=self.memory_cap,
            tools_cap=self.tools_cap,
            history_cap=self.history_cap,
            scratch_cap=self.scratch_cap,
        )
