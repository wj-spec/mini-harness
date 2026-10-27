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
    parser.add_argument("--test-session", action="store_true", help="使用隔离的测试会话范围；fake 模式自动启用")
    parser.add_argument("--stdio", action="store_true", help="供 Ink 使用的 JSONL 后端")
    parser.add_argument("--provider", default=None, help="openai | fake")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--sandbox", action="store_true", help="用 macOS Seatbelt 沙箱执行命令（写入限工作区与临时目录）")
    parser.add_argument("--sandbox-network", action="store_true", help="沙箱内允许网络（默认拒绝）")
    parser.add_argument("--sandbox-network-allow", default=None, metavar="HOSTS", help="沙箱内只允许这些域名（逗号分隔，隐含 --sandbox）")
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--chat-only", action="store_true", help="M1: 不挂工具，只验证真实模型流式回复")
    sessions = parser.add_mutually_exclusive_group()
    sessions.add_argument("--continue", dest="continue_session", action="store_true", help="打开 workspace 中最近有对话的 session")
    sessions.add_argument("--session", metavar="SESSION_ID", help="恢复本工作区指定 session，不自动续跑")
    sessions.add_argument("--list-sessions", action="store_true", help="只读列出本工作区已有对话的 session 后退出")
    parser.add_argument("--eval", action="store_true", help="跑 eval 任务并退出")
    parser.add_argument("--eval-limit", type=int, default=None)
    parser.add_argument("--eval-tasks", default=None)
    parser.add_argument("--task", default=None, help="非交互跑一个任务后退出（跑分/CI 用）")
    parser.add_argument("--bench", action="store_true", help="跑分模式：自动批准、放宽容器内路径限制；只用于一次性环境")
    parser.add_argument("--telemetry-out", default=None, help="跑分结果 JSON 路径")
    parser.add_argument("--runtime-dir", default=None, help="跑分会话/offload/telemetry 的运行时目录")
    args = parser.parse_args()

    if args.stdio and (
        args.eval
        or args.eval_limit is not None or args.eval_tasks is not None
    ):
        parser.error("--stdio cannot be combined with eval options")
    if args.list_sessions and (args.stdio or args.eval):
        parser.error("--list-sessions cannot be combined with --stdio or --eval")
    if args.eval_limit is not None and args.eval_limit < 1:
        parser.error("--eval-limit must be positive")
    if args.task is not None:
        if args.stdio or args.eval or args.list_sessions or args.continue_session or args.session is not None:
            parser.error("--task cannot be combined with --stdio/--eval/--list-sessions/--continue/--session")
        if not args.task.strip():
            parser.error("--task must not be empty")
    if args.bench and args.task is None:
        parser.error("--bench requires --task")
    if args.telemetry_out and args.task is None:
        parser.error("--telemetry-out requires --task")
    if not args.stdio and not args.eval and not args.list_sessions and args.task is None:
        print(
            "Textual 入口已退役。交互界面请运行 ./run_ink.sh（例如 ./run_ink.sh --fake）；"
            "后端与 eval 分别使用 --stdio / --eval。",
            file=sys.stderr,
        )
        raise SystemExit(2)
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
    if args.sandbox:
        config.sandbox = True
    if args.sandbox_network:
        config.sandbox = True
        config.sandbox_network = True
    if args.sandbox_network_allow:
        config.sandbox = True
        config.sandbox_network_allow = args.sandbox_network_allow

    if args.task is not None:
        from app.oneshot import OneShotOptions, run_oneshot

        provider = build_provider(config, args.fake)
        options = OneShotOptions(
            task=args.task,
            telemetry_out=Path(args.telemetry_out) if args.telemetry_out else None,
            runtime_dir=Path(args.runtime_dir) if args.runtime_dir else None,
            bench=args.bench,
        )
        try:
            _, exit_code = run_oneshot(config, provider, options)
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit(exit_code)

    session_purpose = "test" if args.test_session or args.fake or config.provider == "fake" else "user"

    if args.list_sessions:
        rows = SessionStore.list_sessions(config.workspace, purpose=session_purpose)
        if not rows:
            print(f"no conversation found under {config.workspace}/.vibe/sessions")
            return
        print("SESSION_ID\tPURPOSE\tMESSAGES\tUPDATED_AT\tTITLE")
        for row in rows:
            print(f"{row['id']}\t{session_purpose}\t{row['message_count']}\t{row['updated_at']}\t{row['title']}")
        return

    resumed_session = None
    if args.continue_session or args.session is not None:
        try:
            resumed_session = (
                SessionStore.open_existing(config.workspace, args.session, purpose=session_purpose)
                if args.session is not None else SessionStore.latest(config.workspace, purpose=session_purpose)
            )
        except ValueError as exc:
            parser.error(str(exc))
        if resumed_session is None or not resumed_session.load_context():
            target = f"session {args.session}" if args.session is not None else "conversation"
            print(f"no {target} found under {config.workspace}/.vibe/sessions", file=sys.stderr)
            raise SystemExit(1)

    if args.eval:
        from app.eval import DEFAULT_TASKS, eval_diagnostics, eval_exit_code, load_tasks, run_eval

        try:
            tasks = load_tasks(args.eval_tasks or DEFAULT_TASKS)
            report = run_eval(
                tasks,
                lambda task: build_provider(config, args.fake),
                config.workspace,
                pricing=Pricing(config.input_price_per_1m, config.output_price_per_1m),
                model="fake" if args.fake or config.provider == "fake" else config.model,
                limit=args.eval_limit,
                config=config,
            )
        except (OSError, ValueError, TypeError) as exc:
            parser.error(str(exc))
        print(f"eval {report.passed}/{report.total} -> {report.run_dir}")
        for detail in eval_diagnostics(report):
            print(detail)
        exit_code = eval_exit_code(report)
        if exit_code:
            raise SystemExit(exit_code)
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
    if resumed_session is not None:
        session = resumed_session
        repaired = session.repair_incomplete_tool_calls()
        if repaired:
            print(f"recovered {repaired} incomplete tool call(s)", file=sys.stderr)
        history = session.load_context()
        state = session.load_state() or TaskState(goal="")
    else:
        session = SessionStore.new(config.workspace, config.model, purpose=session_purpose)
        history = []
        state = TaskState(goal="")

    approval = ApprovalGate(audit_path=session.audit_path)
    try:
        tools = ToolRegistry(config.workspace, context=context, state=state,
                             skills_config=config.skills_config,
                             sandbox=config.sandbox, sandbox_network=config.sandbox_network,
                             sandbox_network_allow=config.sandbox_network_allow)
    except (OSError, ValueError) as exc:
        print(f"error: invalid skills config: {exc}", file=sys.stderr)
        raise SystemExit(1)
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
        max_calls_without_progress=config.max_calls_without_progress,
        max_reads_per_file=config.max_reads_per_file,
        max_progress_warnings=config.max_progress_warnings,
        max_repeated_calls=config.max_repeated_calls,
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
    if not args.stdio:
        raise SystemExit(2)  # main() 已拦截；保留防御，避免误走交互入口。
    from app.stdio import run_stdio

    run_stdio(agent, config, session=session, resumed=args.continue_session or args.session is not None, mcp=mcp)


if __name__ == "__main__":
    main()
