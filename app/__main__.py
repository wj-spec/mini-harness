from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from app.agent import Agent
from app.config import Config
from app.policy import ApprovalGate
from app.instructions import load_agents_md
from app.mcp import McpManager
from app.session import SessionStore
from app.summarize import LLMSummarizer
from app.trace import Pricing
from app.providers.fake import FakeProvider
from app.providers.openai_compat import OpenAICompatProvider
from app.tools import ToolRegistry
from core.context import ContextBuilder
from core.state import TaskState
from core.summarize import summarize_messages


def build_textual_app(*args, **kwargs):
    from app.tui import VibeApp

    return VibeApp(*args, **kwargs)


def build_provider(config: Config, fake: bool, debug_path=None):
    if fake or config.provider == "fake":
        return FakeProvider(
            [
                "这是 fake provider。设 VIBE_API_KEY 后，用 --provider openai 接真模型。",
            ]
        )
    if not config.api_key:
        print(
            "缺少 API key：设置 VIBE_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY，"
            "或先用 --fake。",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return OpenAICompatProvider(
        config.model,
        config.api_key,
        config.base_url,
        trust_env=config.trust_env,
        debug_path=debug_path,
        stream_usage=config.stream_usage,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="vibe")
    parser.add_argument("--fake", action="store_true", help="不联网，用脚本 provider")
    parser.add_argument("--stdio", action="store_true", help="供 Ink 使用的 JSONL 后端")
    parser.add_argument("--provider", default=None, help="openai | fake")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--chat-only", action="store_true", help="M1: 不挂工具，只验证真实模型流式回复")
    parser.add_argument("--continue", dest="continue_session", action="store_true", help="打开 workspace 中最近有对话的 session")
    parser.add_argument("--eval", action="store_true", help="跑 eval 任务并退出")
    parser.add_argument("--eval-limit", type=int, default=None)
    parser.add_argument("--eval-tasks", default=None)
    args = parser.parse_args()

    if args.stdio and (
        args.eval
        or args.eval_limit is not None or args.eval_tasks is not None
    ):
        parser.error("--stdio cannot be combined with eval options")
    config = Config.from_env()
    if args.provider:
        config.provider = args.provider
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.workspace:
        config.workspace = Path(args.workspace)
    if args.max_steps is not None:
        config.max_steps = args.max_steps

    if args.eval:
        from app.eval import DEFAULT_TASKS, load_tasks, run_eval

        tasks = load_tasks(args.eval_tasks or DEFAULT_TASKS)
        report = run_eval(
            tasks,
            lambda task: build_provider(config, args.fake),
            config.workspace,
            pricing=Pricing(config.input_price_per_1m, config.output_price_per_1m),
            model=config.model,
            limit=args.eval_limit,
        )
        print(f"eval {report.passed}/{report.total} -> {report.run_dir}")
        return

    debug_path = (
        config.workspace / ".vibe" / "debug" / "provider.jsonl"
        if os.getenv("VIBE_DEBUG", "").lower() in {"1", "true", "yes"}
        else None
    )
    provider = build_provider(config, args.fake, debug_path=debug_path)
    policy = config.budget_policy()
    if args.fake or config.provider == "fake":
        summarizer = summarize_messages
    else:
        summarizer = LLMSummarizer(
            model=config.summary_model or config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            max_chars=config.summary_max_chars,
            trust_env=config.trust_env,
        )
    context = ContextBuilder(
        policy=policy,
        keep_recent_groups=config.keep_recent_groups,
        offload_dir=config.workspace / ".vibe" / "offload",
        summarizer=summarizer,
    )
    if args.continue_session:
        session = SessionStore.latest(config.workspace)
        if session is None:
            print(f"no conversation found under {config.workspace}/.vibe/sessions", file=sys.stderr)
            raise SystemExit(1)
        repaired = session.repair_incomplete_tool_calls()
        if repaired:
            print(f"recovered {repaired} incomplete tool call(s)", file=sys.stderr)
        history = session.load_context()
        state = session.load_state() or TaskState(goal="")
    else:
        session = SessionStore.new(config.workspace, config.model)
        history = []
        state = TaskState(goal="")

    approval = ApprovalGate(audit_path=session.audit_path)
    tools = ToolRegistry(config.workspace, context=context, state=state)
    try:
        mcp = McpManager.from_env(config.mcp_config, config.mcp_servers, config.workspace)
    except (OSError, ValueError) as exc:
        print(f"error: invalid MCP config: {exc}", file=sys.stderr)
        raise SystemExit(1)
    _run(config, session, tools, approval, provider, context, state, history, args,
         mcp=None if args.chat_only else mcp)


def _run(config, session, tools, approval, provider, context, state, history, args, mcp=None) -> None:
    instructions = load_agents_md(config.workspace, config.agents_max_chars)
    pricing = Pricing(config.input_price_per_1m, config.output_price_per_1m)
    agent = Agent(
        provider,
        tools,
        max_steps=config.max_steps,
        max_run_seconds=config.max_run_seconds,
        llm_timeout=config.llm_timeout,
        llm_retries=config.llm_retries,
        llm_retry_backoff=config.llm_retry_backoff,
        max_continuations=config.max_continuations,
        max_no_progress=config.max_no_progress,
        max_run_tokens=config.max_run_tokens,
        compact_interval_tokens=config.compact_interval_tokens,
        max_run_cost_usd=config.max_run_cost_usd,
        tool_retries=config.tool_retries,
        context=context,
        tools_enabled=not args.chat_only,
        state=state,
        approval=approval,
        history=history,
        pricing=pricing,
        project_instructions=instructions,
    )
    if args.stdio:
        from app.stdio import run_stdio

        run_stdio(agent, config, session=session, resumed=args.continue_session, mcp=mcp)
    else:
        build_textual_app(config, agent, session=session, resumed=args.continue_session, mcp=mcp).run()


if __name__ == "__main__":
    main()
