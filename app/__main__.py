from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from app.runtime.agent import Agent
from app.config import Config
from app.safety.policy import ApprovalGate
from app.profiles.instructions import load_agents_md
from app.tools.mcp import McpManager
from app.session.store import SessionStore
from app.runtime.summarize import LLMSummarizer
from app.session.trace import Pricing
from app.providers.fake import FakeProvider
from app.providers.openai_compat import OpenAICompatProvider
from app.tools.registry import ToolRegistry
from core.context import ContextBuilder
from core.contracts import Response, ToolCall
from core.task import TaskState
from core.context.summarize import summarize_messages


def _fake_demo_script() -> list:
    """无需 API key 的确定性演示：写文件 → 审批 → 验证 → 回执。

    只写 .vibe/（运行时产物目录），不碰用户源码；需要用户在审批面板确认两步。
    """
    return [
        Response(tool_calls=[ToolCall(
            "write_file", {"path": ".vibe/fake-demo.txt", "content": "fake demo\n"}, id="fake-demo-w1",
        )]),
        Response(tool_calls=[ToolCall(
            "bash", {"command": "test -f .vibe/fake-demo.txt", "verification": True},
            id="fake-demo-b1",
        )]),
        "fake 演示完成：写文件 → 审批 → 验证。输入 /evidence 查看最近一轮证据；"
        "配好 VIBE_API_KEY 后可以直接跑真实任务。",
    ]


def _fake_stream_faults() -> list[dict]:
    """VIBE_FAKE_STREAM_ERRORS=N：在 fake 脚本前注入 N 次可重试流错误。"""
    try:
        count = int(os.getenv("VIBE_FAKE_STREAM_ERRORS", "0") or 0)
    except ValueError:
        count = 0
    return [
        {"stream_error": "HTTP 503: injected fake stream error", "retryable": True}
        for _ in range(max(0, min(count, 5)))
    ]


def build_provider(config: Config, fake: bool, debug_path=None):
    debug_path = debug_path or os.getenv("VIBE_LLM_DEBUG_PATH") or None
    if fake or config.provider == "fake":
        if os.getenv("VIBE_FAKE_DEMO", "").strip().lower() in {"1", "true", "yes"}:
            return FakeProvider(_fake_stream_faults() + _fake_demo_script())
        if os.getenv("VIBE_FAKE_READ", "").strip().lower() in {"1", "true", "yes"}:
            from core.contracts import ToolCall
            return FakeProvider(_fake_stream_faults() + [
                Response(tool_calls=[ToolCall("read_file", {"path": "README.md"}, id="fake-read-1")]),
                "fake read 完成",
            ])
        return FakeProvider(
            _fake_stream_faults() + [
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
        timeout=config.llm_timeout,
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
    parser.add_argument("--tools-root", default=None, help="工具写入根（worktree 隔离用；会话仍用 --workspace）")
    parser.add_argument("--sandbox", action="store_true", help="用 macOS Seatbelt 沙箱执行命令（写入限工作区与临时目录）")
    parser.add_argument("--sandbox-network", action="store_true", help="沙箱内允许网络（默认拒绝）")
    parser.add_argument("--sandbox-network-allow", default=None, metavar="HOSTS", help="沙箱内只允许这些域名（逗号分隔，隐含 --sandbox）")
    parser.add_argument("--autonomy", choices=["ask", "edits", "workspace", "full"], default=None, help="整体授权档位：ask/edits/workspace/full")
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--chat-only", action="store_true", help="M1: 不挂工具，只验证真实模型流式回复")
    sessions = parser.add_mutually_exclusive_group()
    sessions.add_argument("--continue", dest="continue_session", action="store_true", help="打开 workspace 中最近有对话的 session")
    sessions.add_argument("--session", metavar="SESSION_ID", help="恢复本工作区指定 session，不自动续跑")
    sessions.add_argument("--list-sessions", action="store_true", help="只读列出本工作区已有对话的 session 后退出")
    parser.add_argument("--tasks", action="store_true", help="只读列出最近会话的任务与 worker 状态")
    parser.add_argument("--tasks-json", action="store_true", help="--tasks 的 JSON 输出")
    parser.add_argument("--notifications", action="store_true", help="只读输出本工作区最近的本地通知")
    parser.add_argument("--notification-kind", default=None, metavar="KIND", help="与 --notifications 搭配，只显示指定 kind")
    parser.add_argument("--plans", action="store_true", help="只读列出本工作区会话的计划版本")
    parser.add_argument("--acceptance", action="store_true", help="只读列出本工作区会话的计划验收结果")
    parser.add_argument("--approvals", action="store_true", help="只读列出本工作区会话的待审批项")
    parser.add_argument("--evidence", action="store_true", help="只读输出指定会话最近一轮验收证据")
    parser.add_argument("--evidence-json", action="store_true", help="--evidence 的 JSON 输出")
    parser.add_argument("--trace-summary", action="store_true", help="只读聚合指定会话 trace（LLM/tool/审批/停止原因）")
    parser.add_argument("--trace-json", action="store_true", help="--trace-summary 的 JSON 输出")
    parser.add_argument("--trace-all-sessions", action="store_true", help="与 --trace-summary 搭配：汇总本工作区所有会话")
    parser.add_argument("--trace-last-hours", type=float, default=None, metavar="H", help="与 --trace-summary 搭配：只统计最近 H 小时事件")
    parser.add_argument("--bench-history", action="store_true", help="只读列出本工作区长任务 benchmark 历史报告")
    parser.add_argument("--bench-history-json", action="store_true", help="--bench-history 的 JSON 输出")
    parser.add_argument("--bench-history-limit", type=int, default=20, metavar="N", help="--bench-history 显示条数（默认 20，1-200）")
    parser.add_argument("--redteam", action="store_true", help="只读检查无人值守 profile 默认拒绝矩阵")
    parser.add_argument("--prepare-plan-task", action="store_true", help="把会话中最新可执行计划准备成待运行 task（不启动 worker）")
    parser.add_argument("--run-plan-queue", action="store_true", help="按创建顺序依次运行会话中已准备的计划 task")
    parser.add_argument("--queue-task-timeout", type=float, default=None, help="--run-plan-queue 单个 worker 超时秒数（默认 1800）")
    parser.add_argument("--queue-dry-run", action="store_true", help="与 --run-plan-queue 搭配：只报告队列与计划可执行性，不启动 worker")
    parser.add_argument("--queue-all-sessions", action="store_true", help="与 --run-plan-queue 搭配：跨会话收集 created 计划 task 并串行执行")
    parser.add_argument("--queue-workspace", action="append", default=None, metavar="PATH", help="与 --run-plan-queue 搭配：指定工作区（可重复；默认当前 --workspace）")
    parser.add_argument("--queue-continue-on-failure", action="store_true", help="与 --run-plan-queue 搭配：某个任务失败/计划不可执行时继续后续任务")
    parser.add_argument("--queue-concurrency", type=int, default=None, metavar="N", help="与 --run-plan-queue 搭配：并行 worker 数（1-8，默认 1）")
    parser.add_argument("--plan-task-priority", type=int, default=None, metavar="N", help="与 --prepare-plan-task 搭配：待运行 task 优先级（0-100，默认 0）")
    parser.add_argument("--set-task-priority", default=None, metavar="TASK_ID", help="调整尚未开始的 created 任务优先级")
    parser.add_argument("--task-priority", type=int, default=None, metavar="N", help="与 --set-task-priority 搭配：新优先级（0-100）")
    parser.add_argument("--approve-unattended-profile", action="store_true", help="批准当前无人值守 profile（记录 hash，可含放宽项）")
    parser.add_argument("--prune-worktrees", action="store_true", help="只读报告 stale/孤立的 worktree")
    parser.add_argument("--prune-apply", action="store_true", help="与 --prune-worktrees 搭配：把目录缺失的 worktree 标记 removed 并删分支")
    parser.add_argument("--worker", action="store_true", help="后台 worker：执行一个 task segment 并暴露 attach socket")
    parser.add_argument("--attach", action="store_true", help="attach 到运行中的 worker（JSONL 回放 + live）")
    parser.add_argument("--mode", choices=["observe", "control"], default="observe", help="attach 模式：observe/control")
    parser.add_argument("--take-control", action="store_true", help="接管已有 controller")
    parser.add_argument("--task-id", default=None, metavar="TASK_ID", help="--worker/--attach 的目标任务")
    parser.add_argument("--merge-task", default=None, metavar="TASK_ID", help="合并 worktree 任务（先请求/校验审批）")
    parser.add_argument("--approve-merge", default=None, metavar="APPROVAL_ID", help="批准并执行 merge（需 --merge-task）")
    parser.add_argument("--merge-verify", action="append", default=None, metavar="CMD", help="合并后验证命令（可重复）")
    parser.add_argument("--stop", action="store_true", help="workspace kill switch：停止所有本机 worker")
    parser.add_argument("--stop-task", default=None, metavar="TASK_ID", help="只停止指定 worker")
    parser.add_argument("--pause-task", default=None, metavar="TASK_ID", help="暂停指定 worker（保留 worktree，不写 .vibe/stop）")
    parser.add_argument("--decide-approval", default=None, metavar="APPROVAL_ID", help="通过 worker attach 通道处理一个待审批项")
    parser.add_argument("--approval-decision", choices=["once", "session", "workspace", "deny"], default=None, help="与 --decide-approval 搭配，默认 once")
    parser.add_argument("--cleanup-logs", action="store_true", help="显式轮转会话 trace（记录到 .vibe/retention.jsonl）")
    parser.add_argument("--cleanup-bench", action="store_true", help="显式清理 .vibe/bench-long 旧运行目录")
    parser.add_argument("--bench-keep", type=int, default=None, metavar="N", help="与 --cleanup-bench 搭配：保留最近 N 次（默认 20）")
    parser.add_argument("--log-max-bytes", type=int, default=None, metavar="N")
    parser.add_argument("--log-dry-run", action="store_true", help="只报告 --cleanup-logs 会轮转什么")
    parser.add_argument("--eval", action="store_true", help="跑 eval 任务并退出")
    parser.add_argument("--eval-limit", type=int, default=None)
    parser.add_argument("--eval-tasks", default=None)
    parser.add_argument("--task", default=None, help="非交互跑一个任务后退出（跑分/CI 用）")
    parser.add_argument("--bench", action="store_true", help="跑分模式：自动批准、放宽容器内路径限制；只用于一次性环境")
    parser.add_argument("--telemetry-out", default=None, help="跑分结果 JSON 路径")
    parser.add_argument("--runtime-dir", default=None, help="跑分会话/offload/telemetry 的运行时目录")
    args = parser.parse_args()

    if args.tools_root and not (args.stdio or args.worker or args.attach):
        parser.error("--tools-root is only valid with --stdio/--worker/--attach")
    if args.stdio and (
        args.eval
        or args.eval_limit is not None or args.eval_tasks is not None
    ):
        parser.error("--stdio cannot be combined with eval options")
    if args.list_sessions and (args.stdio or args.eval):
        parser.error("--list-sessions cannot be combined with --stdio or --eval")
    if args.tasks and (
        args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
        or args.list_sessions or args.task is not None or args.continue_session
        or args.session is not None or args.bench
    ):
        parser.error("--tasks cannot be combined with --stdio/--eval/--list-sessions/--task/--continue/--session/--bench")
    if args.tasks_json and not args.tasks:
        parser.error("--tasks-json requires --tasks")
    if args.notification_kind is not None and not args.notifications:
        parser.error("--notification-kind requires --notifications")
    if args.notifications and (
        args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
        or args.list_sessions or args.task is not None or args.continue_session
        or args.session is not None or args.bench or args.tasks or args.worker or args.attach
        or args.stop or args.stop_task is not None or args.pause_task is not None
        or args.merge_task is not None or args.cleanup_logs
    ):
        parser.error("--notifications cannot be combined with other modes")
    if args.plans and (
        args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
        or args.list_sessions or args.task is not None or args.continue_session
        or args.session is not None or args.bench or args.tasks or args.notifications
        or args.worker or args.attach or args.stop or args.stop_task is not None
        or args.pause_task is not None or args.merge_task is not None or args.cleanup_logs
        or args.acceptance
    ):
        parser.error("--plans cannot be combined with other modes")
    if args.redteam and (
        args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
        or args.list_sessions or args.task is not None or args.continue_session
        or args.session is not None or args.bench or args.tasks or args.notifications
        or args.worker or args.attach or args.stop or args.stop_task is not None
        or args.pause_task is not None or args.merge_task is not None or args.cleanup_logs
        or args.plans or args.acceptance
    ):
        parser.error("--redteam cannot be combined with other modes")
    if args.acceptance and (
        args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
        or args.list_sessions or args.task is not None or args.continue_session
        or args.session is not None or args.bench or args.tasks or args.notifications
        or args.worker or args.attach or args.stop or args.stop_task is not None
        or args.pause_task is not None or args.merge_task is not None or args.cleanup_logs
        or args.plans
    ):
        parser.error("--acceptance cannot be combined with other modes")
    if args.approvals and (
        args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
        or args.list_sessions or args.task is not None or args.continue_session
        or args.session is not None or args.bench or args.tasks or args.notifications
        or args.worker or args.attach or args.stop or args.stop_task is not None
        or args.pause_task is not None or args.merge_task is not None or args.cleanup_logs
        or args.plans or args.acceptance or args.redteam
    ):
        parser.error("--approvals cannot be combined with other modes")
    if args.worker or args.attach:
        if not args.task_id or args.session is None:
            parser.error("--worker/--attach require --task-id and --session")
        if args.mode != "observe" or args.take_control:
            if not args.attach:
                parser.error("--mode/--take-control require --attach")
        if args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None:
            parser.error("--worker/--attach cannot be combined with --stdio/--eval")
        if args.list_sessions or args.task is not None or args.continue_session or args.tasks or args.bench:
            parser.error("--worker/--attach cannot be combined with other modes")
        if args.worker and args.attach:
            parser.error("--worker and --attach are mutually exclusive")
    elif args.task_id is not None:
        parser.error("--task-id is only valid with --worker/--attach")
    if args.merge_task is not None:
        if args.session is None or args.continue_session:
            parser.error("--merge-task requires a concrete --session")
        if args.stdio or args.eval or args.task is not None or args.list_sessions or args.tasks or args.worker or args.attach or args.stop or args.bench:
            parser.error("--merge-task cannot be combined with other modes")
    elif args.approve_merge is not None:
        parser.error("--approve-merge requires --merge-task")
    if args.merge_verify is not None and args.merge_task is None:
        parser.error("--merge-verify requires --merge-task")
    if args.stop:
        if args.stop_task is not None or args.workspace is None:
            if args.workspace is None:
                parser.error("--stop requires --workspace")
        if args.stdio or args.eval or args.task is not None or args.list_sessions or args.tasks or args.worker or args.attach or args.merge_task is not None or args.bench:
            parser.error("--stop cannot be combined with other modes")
    elif args.stop_task is not None:
        parser.error("--stop-task requires --stop")
    if args.cleanup_logs:
        if args.workspace is None:
            parser.error("--cleanup-logs requires --workspace")
        if args.log_max_bytes is not None and args.log_max_bytes < 1:
            parser.error("--log-max-bytes must be positive")
        if args.stdio or args.eval or args.task is not None or args.list_sessions or args.tasks or args.worker or args.attach or args.merge_task is not None or args.bench or args.stop or args.pause_task is not None or args.cleanup_bench:
            parser.error("--cleanup-logs cannot be combined with other modes")
    if args.bench_keep is not None and not args.cleanup_bench:
        parser.error("--bench-keep requires --cleanup-bench")
    if args.cleanup_bench:
        if args.workspace is None:
            parser.error("--cleanup-bench requires --workspace")
        if args.bench_keep is not None and args.bench_keep < 0:
            parser.error("--bench-keep must be non-negative")
        if (
            args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
            or args.list_sessions or args.task is not None or args.bench or args.tasks
            or args.notifications or args.plans or args.acceptance or args.approvals
            or args.redteam or args.prune_worktrees or args.worker or args.attach
            or args.stop or args.stop_task is not None or args.pause_task is not None
            or args.merge_task is not None or args.cleanup_logs or args.decide_approval is not None
            or args.evidence or args.trace_summary or args.prepare_plan_task or args.run_plan_queue
            or args.bench_history or args.approve_unattended_profile
        ):
            parser.error("--cleanup-bench cannot be combined with other modes")
    if args.pause_task is not None:
        if args.workspace is None:
            parser.error("--pause-task requires --workspace")
        if args.stdio or args.eval or args.task is not None or args.list_sessions or args.tasks or args.worker or args.attach or args.merge_task is not None or args.bench or args.stop or args.stop_task is not None:
            parser.error("--pause-task cannot be combined with other modes")
    if args.approval_decision is not None and args.decide_approval is None:
        parser.error("--approval-decision requires --decide-approval")
    if args.decide_approval is not None:
        if args.workspace is None:
            parser.error("--decide-approval requires --workspace")
        if (
            args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
            or args.list_sessions or args.task is not None or args.continue_session
            or args.session is not None or args.bench or args.tasks or args.notifications
            or args.plans or args.acceptance or args.approvals or args.redteam
            or args.worker or args.attach or args.stop or args.stop_task is not None
            or args.pause_task is not None or args.merge_task is not None or args.cleanup_logs
        ):
            parser.error("--decide-approval cannot be combined with other modes")
    if args.bench_history_json and not args.bench_history:
        parser.error("--bench-history-json requires --bench-history")
    if args.bench_history_limit != 20 and not args.bench_history:
        parser.error("--bench-history-limit requires --bench-history")
    if args.bench_history:
        if args.workspace is None:
            parser.error("--bench-history requires --workspace")
        if args.bench_history_limit < 1 or args.bench_history_limit > 200:
            parser.error("--bench-history-limit must be between 1 and 200")
        if (
            args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
            or args.list_sessions or args.task is not None or args.bench or args.tasks
            or args.notifications or args.plans or args.acceptance or args.approvals
            or args.redteam or args.prune_worktrees or args.worker or args.attach
            or args.stop or args.stop_task is not None or args.pause_task is not None
            or args.merge_task is not None or args.cleanup_logs or args.decide_approval is not None
            or args.evidence or args.trace_summary or args.prepare_plan_task or args.run_plan_queue
        ):
            parser.error("--bench-history cannot be combined with other modes")
    if args.trace_json and not args.trace_summary:
        parser.error("--trace-json requires --trace-summary")
    if args.trace_all_sessions and not args.trace_summary:
        parser.error("--trace-all-sessions requires --trace-summary")
    if args.trace_last_hours is not None and not args.trace_summary:
        parser.error("--trace-last-hours requires --trace-summary")
    if args.trace_last_hours is not None and not 0 < args.trace_last_hours <= 8760:
        parser.error("--trace-last-hours must be between 0 and 8760")
    if args.trace_summary:
        if args.trace_all_sessions:
            if args.session is not None or args.continue_session:
                parser.error("--trace-all-sessions cannot combine with --session")
        elif args.session is None or args.continue_session:
            parser.error("--trace-summary requires a concrete --session")
        if args.workspace is None:
            parser.error("--trace-summary requires --workspace")
        if (
            args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
            or args.list_sessions or args.task is not None or args.bench or args.tasks
            or args.notifications or args.plans or args.acceptance or args.approvals
            or args.redteam or args.prune_worktrees or args.worker or args.attach
            or args.stop or args.stop_task is not None or args.pause_task is not None
            or args.merge_task is not None or args.cleanup_logs or args.decide_approval is not None
            or args.evidence or args.prepare_plan_task or args.run_plan_queue
        ):
            parser.error("--trace-summary cannot be combined with other modes")
    if args.evidence_json and not args.evidence:
        parser.error("--evidence-json requires --evidence")
    if args.evidence:
        if args.session is None or args.continue_session:
            parser.error("--evidence requires a concrete --session")
        if args.workspace is None:
            parser.error("--evidence requires --workspace")
        if (
            args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
            or args.list_sessions or args.task is not None or args.bench or args.tasks
            or args.notifications or args.plans or args.acceptance or args.approvals
            or args.redteam or args.prune_worktrees or args.worker or args.attach
            or args.stop or args.stop_task is not None or args.pause_task is not None
            or args.merge_task is not None or args.cleanup_logs or args.decide_approval is not None
            or args.prepare_plan_task or args.run_plan_queue
        ):
            parser.error("--evidence cannot be combined with other modes")
    if args.queue_task_timeout is not None and not args.run_plan_queue:
        parser.error("--queue-task-timeout requires --run-plan-queue")
    if args.queue_dry_run and not args.run_plan_queue:
        parser.error("--queue-dry-run requires --run-plan-queue")
    if args.queue_all_sessions and not args.run_plan_queue:
        parser.error("--queue-all-sessions requires --run-plan-queue")
    if args.queue_workspace and not args.run_plan_queue:
        parser.error("--queue-workspace requires --run-plan-queue")
    if args.queue_continue_on_failure and not args.run_plan_queue:
        parser.error("--queue-continue-on-failure requires --run-plan-queue")
    if args.queue_concurrency is not None:
        if not args.run_plan_queue:
            parser.error("--queue-concurrency requires --run-plan-queue")
        if args.queue_concurrency < 1 or args.queue_concurrency > 8:
            parser.error("--queue-concurrency must be between 1 and 8")
    if args.plan_task_priority is not None:
        if not args.prepare_plan_task:
            parser.error("--plan-task-priority requires --prepare-plan-task")
        if args.plan_task_priority < 0 or args.plan_task_priority > 100:
            parser.error("--plan-task-priority must be between 0 and 100")
    if args.set_task_priority is not None or args.task_priority is not None:
        if args.set_task_priority is None:
            parser.error("--task-priority requires --set-task-priority")
        if args.task_priority is None:
            parser.error("--set-task-priority requires --task-priority")
        if args.workspace is None:
            parser.error("--set-task-priority requires --workspace")
        if args.task_priority < 0 or args.task_priority > 100:
            parser.error("--task-priority must be between 0 and 100")
        if (
            args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
            or args.list_sessions or args.task is not None or args.bench or args.tasks
            or args.notifications or args.plans or args.acceptance or args.approvals
            or args.redteam or args.prune_worktrees or args.worker or args.attach
            or args.stop or args.stop_task is not None or args.pause_task is not None
            or args.merge_task is not None or args.cleanup_logs or args.decide_approval is not None
            or args.evidence or args.trace_summary or args.prepare_plan_task or args.run_plan_queue
            or args.bench_history or args.approve_unattended_profile or args.cleanup_bench
        ):
            parser.error("--set-task-priority cannot be combined with other modes")

    if args.prepare_plan_task or args.run_plan_queue:
        if args.continue_session:
            parser.error("--prepare-plan-task/--run-plan-queue cannot use --continue")
        if args.prepare_plan_task:
            if args.session is None:
                parser.error("--prepare-plan-task requires a concrete --session")
            if args.queue_all_sessions:
                parser.error("--prepare-plan-task cannot use --queue-all-sessions")
        else:
            if args.session is None and not args.queue_all_sessions:
                parser.error("--run-plan-queue requires --session or --queue-all-sessions")
            if args.session is not None and args.queue_all_sessions:
                parser.error("--run-plan-queue cannot combine --session with --queue-all-sessions")
            if args.session is not None and args.queue_workspace:
                parser.error("--run-plan-queue cannot combine --session with --queue-workspace")
        if (
            args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
            or args.list_sessions or args.task is not None or args.bench or args.tasks
            or args.notifications or args.plans or args.acceptance or args.approvals
            or args.redteam or args.prune_worktrees or args.worker or args.attach
            or args.stop or args.stop_task is not None or args.pause_task is not None
            or args.merge_task is not None or args.cleanup_logs or args.decide_approval is not None
            or (args.prepare_plan_task and args.run_plan_queue)
        ):
            parser.error("--prepare-plan-task/--run-plan-queue cannot be combined with other modes")
        if args.workspace is None and not args.queue_workspace:
            parser.error("--prepare-plan-task/--run-plan-queue require --workspace")
    if args.approve_unattended_profile:
        if args.workspace is None:
            parser.error("--approve-unattended-profile requires --workspace")
        if (
            args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
            or args.list_sessions or args.task is not None or args.continue_session
            or args.session is not None or args.bench or args.tasks or args.notifications
            or args.plans or args.acceptance or args.approvals or args.redteam
            or args.prune_worktrees or args.prune_apply or args.worker or args.attach
            or args.stop or args.stop_task is not None or args.pause_task is not None
            or args.merge_task is not None or args.cleanup_logs or args.decide_approval is not None
            or args.evidence or args.prepare_plan_task or args.run_plan_queue
        ):
            parser.error("--approve-unattended-profile cannot be combined with other modes")
    if args.prune_apply and not args.prune_worktrees:
        parser.error("--prune-apply requires --prune-worktrees")
    if args.prune_worktrees:
        if args.workspace is None:
            parser.error("--prune-worktrees requires --workspace")
        if (
            args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
            or args.list_sessions or args.task is not None or args.continue_session
            or args.session is not None or args.bench or args.tasks or args.notifications
            or args.plans or args.acceptance or args.approvals or args.redteam
            or args.worker or args.attach or args.stop or args.stop_task is not None
            or args.pause_task is not None or args.merge_task is not None or args.cleanup_logs
            or args.decide_approval is not None
        ):
            parser.error("--prune-worktrees cannot be combined with other modes")
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
    if not args.stdio and not args.eval and not args.list_sessions and not args.tasks and not args.notifications and not args.plans and not args.acceptance and not args.approvals and not args.redteam and not args.approve_unattended_profile and not args.prune_worktrees and not args.evidence and not args.trace_summary and not args.bench_history and not args.prepare_plan_task and not args.run_plan_queue and not args.worker and not args.attach and args.merge_task is None and not args.stop and args.pause_task is None and not args.cleanup_logs and not args.cleanup_bench and args.task is None and args.decide_approval is None and args.set_task_priority is None:
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
    if args.tools_root:
        config.tools_root = Path(args.tools_root)
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
    if args.autonomy:
        config.autonomy = args.autonomy

    if args.merge_task is not None:
        purpose = "test" if args.test_session or args.fake or config.provider == "fake" else "user"
        session = SessionStore.open_existing(config.workspace, args.session, purpose=purpose)
        if session is None:
            print(f"no session {args.session} under {config.workspace}", file=sys.stderr)
            raise SystemExit(2)
        from app.runtime.tasks import TaskStore
        try:
            task = TaskStore(session.root).get(args.merge_task)
        except Exception as exc:
            print(f"task {args.merge_task} unavailable: {exc}", file=sys.stderr)
            raise SystemExit(2)
        if Path(task.workspace).resolve() != config.workspace.resolve():
            print("task workspace mismatch", file=sys.stderr)
            raise SystemExit(2)
        from app.runtime.approvals import ApprovalError, ApprovalInbox
        from app.runtime.merge import MergeError, MergeGate, plan_verification_commands
        from app.runtime.notifications import notify

        gate = MergeGate(config.workspace)
        verify_commands = list(args.merge_verify or [])
        plan_commands = plan_verification_commands(session.root, task)
        if not verify_commands:
            verify_commands = plan_commands
        try:
            if args.approve_merge:
                try:
                    ApprovalInbox(session.root).resolve(
                        task.task_id, args.approve_merge, decision="once",
                        decided_by="user", decider="user",
                    )
                except ApprovalError as exc:
                    print(f"approval failed: {exc}", file=sys.stderr)
                    raise SystemExit(2)
                result = gate.apply(
                    task.task_id, session_root=session.root,
                    verify_commands=verify_commands,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                notify(config.workspace, "merge_completed", task_id=task.task_id,
                       verified=result.get("post_merge_verified"))
                if verify_commands and not result.get("post_merge_verified"):
                    notify(config.workspace, "merge_verification_failed",
                           task_id=task.task_id, commands=verify_commands)
                    failed = [item["command"] for item in result.get("verification", [])
                              if not item.get("ok")]
                    print("merge completed but post-merge verification failed: "
                          + "; ".join(failed[:5]), file=sys.stderr)
                    raise SystemExit(1)
                return
            candidate = gate.prepare(task.task_id)
            print(candidate.diff_stat or "(empty diff)")
            if plan_commands and not args.merge_verify:
                print("合并后验收（来自已批准计划）：")
                for command in plan_commands:
                    print(f"- {command}")
            approval = gate.request_approval(
                task.task_id, session_root=session.root,
                verify_commands=verify_commands,
            )
            print(f"merge approval requested: {approval['approval_id']}")
            notify(config.workspace, "merge_approval_requested", task_id=task.task_id,
                   approval_id=approval["approval_id"])
            print(f"rerun with --approve-merge {approval['approval_id']}")
            raise SystemExit(2)
        except MergeError as exc:
            notify(config.workspace, "merge_failed", task_id=task.task_id, reason=str(exc)[:500])
            print(f"merge failed: {exc}", file=sys.stderr)
            raise SystemExit(2)

    if args.decide_approval is not None:
        from app.runtime.approvals import ApprovalInbox
        from app.runtime.worker import WorkerError, decide_approval_cli

        sessions_root = Path(config.workspace) / ".vibe" / "sessions"
        matches = []
        if sessions_root.is_dir() and sessions_root.resolve().is_relative_to(Path(config.workspace).resolve()):
            for session_path in sorted(p for p in sessions_root.iterdir() if p.is_dir() and not p.is_symlink()):
                if not (session_path / "tasks").is_dir():
                    continue
                try:
                    pending = ApprovalInbox(session_path).pending()
                except Exception:
                    continue
                for item in pending:
                    if item.get("approval_id") == args.decide_approval:
                        matches.append((session_path, item))
        if not matches:
            print(f"pending approval not found: {args.decide_approval}", file=sys.stderr)
            raise SystemExit(2)
        if len(matches) > 1:
            print(f"ambiguous approval id: {args.decide_approval}", file=sys.stderr)
            raise SystemExit(2)
        session_path, item = matches[0]
        decision = args.approval_decision or "once"
        try:
            result = decide_approval_cli(
                session_path, str(item.get("task_id") or ""), args.decide_approval, decision,
            )
        except WorkerError as exc:
            print(f"approval decision failed: {exc}", file=sys.stderr)
            raise SystemExit(1)
        print(json.dumps({"session": session_path.name, **result}, ensure_ascii=False, indent=2))
        return

    if args.stop:
        from app.runtime.killswitch import stop_workers

        results = stop_workers(config.workspace, task_id=args.stop_task)
        print(json.dumps({"stop_file": str(Path(config.workspace) / ".vibe" / "stop"),
                          "workers": results}, ensure_ascii=False, indent=2))
        return

    if args.pause_task is not None:
        from app.runtime.killswitch import stop_workers

        results = stop_workers(config.workspace, task_id=args.pause_task, write_stop=False)
        print(json.dumps({"paused": True, "workers": results}, ensure_ascii=False, indent=2))
        return

    if args.cleanup_logs:
        from app.session.retention import RetentionPolicy, cleanup_workspace_logs

        policy = RetentionPolicy(max_bytes=args.log_max_bytes or RetentionPolicy.max_bytes)
        result = cleanup_workspace_logs(config.workspace, policy=policy, dry_run=args.log_dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.cleanup_bench:
        from app.session.retention import DEFAULT_BENCH_KEEP, cleanup_bench_reports

        keep = args.bench_keep if args.bench_keep is not None else DEFAULT_BENCH_KEEP
        result = cleanup_bench_reports(config.workspace, keep=keep, dry_run=args.log_dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.tasks_json and not args.tasks:
        parser.error("--tasks-json requires --tasks")
    if args.notification_kind is not None and not args.notifications:
        parser.error("--notification-kind requires --notifications")
    if args.notifications and (
        args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
        or args.list_sessions or args.task is not None or args.continue_session
        or args.session is not None or args.bench or args.tasks or args.worker or args.attach
        or args.stop or args.stop_task is not None or args.pause_task is not None
        or args.merge_task is not None or args.cleanup_logs
    ):
        parser.error("--notifications cannot be combined with other modes")
    if args.plans and (
        args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
        or args.list_sessions or args.task is not None or args.continue_session
        or args.session is not None or args.bench or args.tasks or args.notifications
        or args.worker or args.attach or args.stop or args.stop_task is not None
        or args.pause_task is not None or args.merge_task is not None or args.cleanup_logs
        or args.acceptance
    ):
        parser.error("--plans cannot be combined with other modes")
    if args.redteam and (
        args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
        or args.list_sessions or args.task is not None or args.continue_session
        or args.session is not None or args.bench or args.tasks or args.notifications
        or args.worker or args.attach or args.stop or args.stop_task is not None
        or args.pause_task is not None or args.merge_task is not None or args.cleanup_logs
        or args.plans or args.acceptance
    ):
        parser.error("--redteam cannot be combined with other modes")
    if args.acceptance and (
        args.stdio or args.eval or args.eval_limit is not None or args.eval_tasks is not None
        or args.list_sessions or args.task is not None or args.continue_session
        or args.session is not None or args.bench or args.tasks or args.notifications
        or args.worker or args.attach or args.stop or args.stop_task is not None
        or args.pause_task is not None or args.merge_task is not None or args.cleanup_logs
        or args.plans
    ):
        parser.error("--acceptance cannot be combined with other modes")
    if args.worker or args.attach:
        purpose = "test" if args.test_session or args.fake or config.provider == "fake" else "user"
        session = SessionStore.open_existing(config.workspace, args.session, purpose=purpose)
        if session is None:
            print(f"no session {args.session} under {config.workspace}", file=sys.stderr)
            raise SystemExit(2)
        from app.runtime.tasks import TaskStore
        store = TaskStore(session.root)
        try:
            task = store.get(args.task_id)
        except Exception as exc:
            print(f"task {args.task_id} unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
            raise SystemExit(2)
        if Path(task.workspace).resolve() != config.workspace.resolve():
            print(f"task workspace mismatch: {task.workspace}", file=sys.stderr)
            raise SystemExit(2)
        if args.attach:
            from app.runtime.worker import attach_worker
            raise SystemExit(attach_worker(
                session.root, args.task_id, mode=args.mode,
                take_control=args.take_control,
            ))
        child_args = [sys.executable, "-m", "app", "--stdio",
                      "--workspace", str(config.workspace), "--session", session.id]
        if args.fake:
            child_args.append("--fake")
        if args.test_session:
            child_args.append("--test-session")
        for flag, value in (
            ("--provider", args.provider), ("--model", args.model), ("--base-url", args.base_url),
            ("--max-steps", str(args.max_steps) if args.max_steps is not None else None),
            ("--autonomy", args.autonomy),
        ):
            if value is not None:
                child_args.extend([flag, value])
        if args.sandbox:
            child_args.append("--sandbox")
        if args.sandbox_network:
            child_args.append("--sandbox-network")
        if args.sandbox_network_allow:
            child_args.extend(["--sandbox-network-allow", args.sandbox_network_allow])
        if args.chat_only:
            child_args.append("--chat-only")
        child_env = {"VIBE_TASK_RUNTIME": "1", "VIBE_WORKER": "1"}
        from app.runtime.worker import run_worker
        raise SystemExit(run_worker(session.root, args.task_id, config.workspace, child_args, child_env))

    if args.redteam:
        from app.safety.unattended import load_profile
        from scripts.redteam_unattended import run_checks

        try:
            profile = load_profile(config.workspace)
        except Exception as exc:
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
            raise SystemExit(2)
        rows = run_checks(profile)
        payload = {"ok": all(row["ok"] for row in rows), "profile": profile.profile_id, "checks": rows}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if not payload["ok"]:
            raise SystemExit(1)
        return

    if args.acceptance:
        from app.runtime.acceptance import load_acceptance_result

        sessions_root = Path(config.workspace) / ".vibe" / "sessions"
        rows = []
        skipped_acceptance_files = 0
        if sessions_root.is_dir() and sessions_root.resolve().is_relative_to(Path(config.workspace).resolve()):
            for session_path in sorted(p for p in sessions_root.iterdir() if p.is_dir() and not p.is_symlink()):
                tasks_root = session_path / "tasks"
                if not tasks_root.is_dir():
                    continue
                for task_path in sorted(p for p in tasks_root.iterdir() if p.is_dir() and not p.is_symlink()):
                    for file in sorted(task_path.glob("acceptance.v*.json")):
                        try:
                            version = int(file.stem.split("v")[-1])
                        except ValueError:
                            skipped_acceptance_files += 1
                            continue
                        saved = load_acceptance_result(task_path, version)
                        if saved is None:
                            skipped_acceptance_files += 1
                            continue
                        plan_id = None
                        objective = None
                        try:
                            from app.runtime.plan import load_plan, plan_path
                            plan = load_plan(plan_path(task_path, version))
                            plan_id, objective = plan.plan_id, plan.objective
                        except Exception:
                            pass
                        rows.append({
                            "session": session_path.name, "task_id": task_path.name,
                            "plan_id": plan_id, "objective": objective,
                            "plan_version": version, "status": saved.get("status"),
                            "codes": saved.get("codes", []),
                            "commands": len((saved.get("results") or {})),
                        })
        if skipped_acceptance_files:
            print(f"warning: skipped {skipped_acceptance_files} corrupt acceptance files",
                  file=sys.stderr)
        print(json.dumps({"acceptance": rows[-100:],
                          "skipped_acceptance_files": skipped_acceptance_files},
                         ensure_ascii=False, indent=2))
        return

    if args.approvals:
        from app.runtime.approvals import ApprovalInbox

        sessions_root = Path(config.workspace) / ".vibe" / "sessions"
        rows = []
        skipped_approval_records = 0
        if sessions_root.is_dir() and sessions_root.resolve().is_relative_to(Path(config.workspace).resolve()):
            for session_path in sorted(p for p in sessions_root.iterdir() if p.is_dir() and not p.is_symlink()):
                if not (session_path / "tasks").is_dir():
                    continue
                inbox = ApprovalInbox(session_path)
                try:
                    pending = inbox.pending()
                except Exception:
                    continue
                skipped_approval_records += int(getattr(inbox, "last_skipped", 0) or 0)
                for item in pending:
                    rows.append({
                        "session": session_path.name,
                        "task_id": item.get("task_id"),
                        "approval_id": item.get("approval_id"),
                        "tool": item.get("tool"),
                        "tier": item.get("tier"),
                        "requester": item.get("requester"),
                        "created_at": item.get("created_at"),
                        "expires_at": item.get("expires_at"),
                        "preview_ref": item.get("preview_ref"),
                    })
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        if skipped_approval_records:
            print(f"warning: skipped {skipped_approval_records} corrupt approval records",
                  file=sys.stderr)
        print(json.dumps({"approvals": rows[-100:], "count": len(rows),
                          "skipped_approval_records": skipped_approval_records},
                         ensure_ascii=False, indent=2))
        return

    if args.plans:
        from app.runtime.plan import PlanError, load_plan

        sessions_root = Path(config.workspace) / ".vibe" / "sessions"
        rows = []
        skipped_plan_files = 0
        if sessions_root.is_dir() and sessions_root.resolve().is_relative_to(Path(config.workspace).resolve()):
            for session_path in sorted(p for p in sessions_root.iterdir() if p.is_dir() and not p.is_symlink()):
                tasks_root = session_path / "tasks"
                if not tasks_root.is_dir():
                    continue
                for task_path in sorted(p for p in tasks_root.iterdir() if p.is_dir() and not p.is_symlink()):
                    plan_root = task_path / "plan"
                    if not plan_root.is_dir():
                        continue
                    for file in sorted(plan_root.glob("plan.v*.json")):
                        try:
                            plan = load_plan(file)
                        except (PlanError, OSError):
                            skipped_plan_files += 1
                            continue
                        acceptance = None
                        try:
                            from app.runtime.acceptance import load_acceptance_result
                            saved = load_acceptance_result(task_path, plan.version)
                            if saved is not None:
                                acceptance = saved.get("status")
                        except Exception:
                            pass
                        done_steps = 0
                        executed_steps = 0
                        try:
                            from app.runtime.plan_execution import load_ledger
                            ledger = load_ledger(task_path, plan.version)
                            executed_steps = len(ledger.steps)
                            done_steps = sum(
                                1 for record in ledger.steps.values()
                                if getattr(record, "status", None) == "done"
                            )
                        except Exception:
                            pass
                        rows.append({
                            "workspace": str(config.workspace),
                            "session": session_path.name,
                            "task_id": task_path.name,
                            "plan_id": plan.plan_id,
                            "version": plan.version,
                            "status": plan.status,
                            "stale": bool(plan.approval.get("stale")),
                            "acceptance": acceptance,
                            "total_steps": len(plan.steps),
                            "done_steps": done_steps,
                            "executed_steps": executed_steps,
                            "objective": plan.objective,
                        })
        if skipped_plan_files:
            print(f"warning: skipped {skipped_plan_files} corrupt plan files",
                  file=sys.stderr)
        print(json.dumps({"plans": rows[-100:], "skipped_plan_files": skipped_plan_files},
                         ensure_ascii=False, indent=2))
        return

    if args.notifications:
        from app.runtime.notifications import read_notifications

        stats: dict[str, int] = {}
        items = read_notifications(config.workspace, stats=stats)
        if args.notification_kind:
            items = [item for item in items if item.get("kind") == args.notification_kind]
        recent = items[-50:]
        skipped_notifications = int(stats.get("skipped", 0))
        if skipped_notifications:
            print(f"warning: skipped {skipped_notifications} corrupt notification records",
                  file=sys.stderr)
        print(json.dumps({"workspace": str(config.workspace), "count": len(recent),
                          "notifications": recent,
                          "skipped_notification_records": skipped_notifications},
                         ensure_ascii=False, indent=2))
        return

    if args.prepare_plan_task or args.run_plan_queue:
        import subprocess

        from app.runtime.plan import can_execute, executable_plans, find_plan
        from app.runtime.tasks import TaskStore

        purpose = "test" if args.test_session or args.fake or config.provider == "fake" else "user"

        def open_sessions(workspace: Path) -> list[SessionStore]:
            if args.queue_all_sessions:
                root = workspace / ".vibe" / "sessions"
                sessions = []
                if root.is_dir():
                    for candidate in sorted(root.iterdir()):
                        if not candidate.is_dir() or candidate.is_symlink():
                            continue
                        session = SessionStore(candidate)
                        if session.purpose == purpose and (candidate / "tasks").is_dir():
                            sessions.append(session)
                return sessions
            session = SessionStore.open_existing(workspace, args.session, purpose=purpose)
            if session is None:
                print(f"no session {args.session} under {workspace}", file=sys.stderr)
                raise SystemExit(2)
            return [session]

        if args.prepare_plan_task:
            workspace = Path(args.workspace or config.workspace).expanduser().resolve()
            sessions = open_sessions(workspace)
            session = sessions[0]
            store = TaskStore(session.root)
            plans = executable_plans(session.root)
            if not plans:
                print("no executable approved plan in this session", file=sys.stderr)
                raise SystemExit(2)
            plan_path, plan = plans[0]
            try:
                task = store.create(
                    kind="run", objective=plan.objective, workspace=str(workspace),
                    mode="act", plan_id=plan.plan_id, plan_version=plan.version,
                    priority=max(0, min(args.plan_task_priority or 0, 100)),
                    session_id=session.id,
                )
            except Exception as exc:
                print(f"prepare plan task failed: {exc}", file=sys.stderr)
                raise SystemExit(2)
            print(json.dumps({
                "workspace": str(workspace), "session": session.id, "task_id": task.task_id,
                "plan_id": plan.plan_id, "plan_version": plan.version,
                "objective": plan.objective, "plan_path": str(plan_path),
                "priority": task.priority,
            }, ensure_ascii=False, indent=2))
            return

        workspaces = [Path(item).expanduser().resolve() for item in (args.queue_workspace or [])]
        if not workspaces:
            workspaces = [Path(config.workspace).expanduser().resolve()]
        entries = []
        session_ids: list[str] = []
        for workspace in workspaces:
            for session in open_sessions(workspace):
                session_ids.append(session.id)
                store = TaskStore(session.root)
                for task in store.list():
                    if task.status == "created" and task.plan_id:
                        entries.append((workspace, session, store, task))
        entries.sort(key=lambda item: (-item[3].priority, item[3].created_at,
                                       item[0].as_posix(), item[1].id, item[3].task_id))

        def plan_executable(session, task) -> bool:
            if not task.plan_id:
                return False
            try:
                found = find_plan(session.root, task.plan_id, task.plan_version)
            except Exception:
                return False
            return bool(found) and can_execute(found[1])

        workspace_names = [str(workspace) for workspace in workspaces]
        if args.queue_dry_run:
            rows = [
                {"workspace": str(workspace), "session": session.id,
                 "task_id": task.task_id, "plan_id": task.plan_id,
                 "plan_version": task.plan_version, "priority": task.priority,
                 "plan_executable": plan_executable(session, task),
                 "objective": task.objective[:200]}
                for workspace, session, _store, task in entries
            ]
            payload = {"dry_run": True, "queue": rows, "count": len(rows),
                       "workspaces": workspace_names, "sessions": session_ids}
            if len(workspaces) == 1:
                payload["workspace"] = workspace_names[0]
            if len(session_ids) == 1:
                payload["session"] = session_ids[0]
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        try:
            timeout = float(args.queue_task_timeout or 1800)
        except ValueError:
            timeout = 1800.0
        timeout = max(10.0, min(timeout, 24 * 3600.0))
        repo_root = str(Path(__file__).resolve().parents[1])
        ran: list[dict] = []
        stopped_early: str | None = None
        concurrency = max(1, min(args.queue_concurrency or 1, 8))
        lock_wait = str(int(max(30, min(timeout, 300))))

        def run_one(entry) -> dict:
            workspace, session, store, task = entry
            command = [sys.executable, "-m", "app", "--worker",
                       "--workspace", str(workspace), "--session", session.id,
                       "--task-id", task.task_id]
            if args.fake or config.provider == "fake":
                command.append("--fake")
            env = dict(os.environ, VIBE_TASK_RUNTIME="1")
            if concurrency > 1:
                env["VIBE_WORKSPACE_LOCK_WAIT"] = lock_wait
            try:
                proc = subprocess.run(command, cwd=repo_root, env=env,
                                      capture_output=True, text=True, timeout=timeout)
                exit_code: int | None = proc.returncode
                stderr = (proc.stderr or "")[-1000:]
            except subprocess.TimeoutExpired:
                exit_code, stderr = None, f"worker timeout after {timeout}s"
            try:
                status = store.get(task.task_id).status
            except Exception:
                status = "unknown"
            return {"workspace": str(workspace), "session": session.id,
                    "task_id": task.task_id, "plan_id": task.plan_id,
                    "plan_version": task.plan_version, "priority": task.priority,
                    "exit_code": exit_code, "status": status, "stderr": stderr}

        failed = 0
        stopped_early: str | None = None
        if concurrency <= 1:
            for entry in entries:
                workspace, session, _store, task = entry
                if not plan_executable(session, task):
                    ran.append({"workspace": str(workspace), "session": session.id,
                                "task_id": task.task_id, "plan_id": task.plan_id,
                                "plan_version": task.plan_version, "priority": task.priority,
                                "exit_code": None, "status": "skipped_plan_unavailable", "stderr": ""})
                    failed += 1
                    if stopped_early is None:
                        stopped_early = "plan_unavailable"
                    if args.queue_continue_on_failure:
                        continue
                    break
                row = run_one(entry)
                ran.append(row)
                if row["status"] != "completed":
                    failed += 1
                    if stopped_early is None:
                        stopped_early = row["status"]
                    if args.queue_continue_on_failure:
                        continue
                    break
        else:
            from concurrent.futures import ThreadPoolExecutor

            runnable = []
            for entry in entries:
                workspace, session, _store, task = entry
                if not plan_executable(session, task):
                    ran.append({"workspace": str(workspace), "session": session.id,
                                "task_id": task.task_id, "plan_id": task.plan_id,
                                "plan_version": task.plan_version, "priority": task.priority,
                                "exit_code": None, "status": "skipped_plan_unavailable", "stderr": ""})
                    continue
                runnable.append(entry)
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                for row in pool.map(run_one, runnable):
                    ran.append(row)
            failed = sum(1 for item in ran if item["status"] != "completed")
        payload = {"ran": ran,
                   "completed": sum(1 for item in ran if item["status"] == "completed"),
                   "failed": failed,
                   "stopped_early": stopped_early if not args.queue_continue_on_failure else None,
                   "continue_on_failure": bool(args.queue_continue_on_failure),
                   "concurrency": concurrency,
                   "workspaces": workspace_names, "sessions": session_ids}
        if len(workspaces) == 1:
            payload["workspace"] = workspace_names[0]
        if len(session_ids) == 1:
            payload["session"] = session_ids[0]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if failed:
            raise SystemExit(1)
        return

    if args.trace_summary:
        import time as _time

        from app.session.observability import (
            aggregate_trace_summaries, render_trace_summary,
            render_trace_totals, summarize_trace,
        )

        since_ts = None
        if args.trace_last_hours is not None:
            since_ts = _time.time() - args.trace_last_hours * 3600.0
        purpose = "test" if args.test_session or args.fake or config.provider == "fake" else "user"
        if args.trace_all_sessions:
            root = Path(config.workspace) / ".vibe" / "sessions"
            candidates = []
            if root.is_dir():
                for candidate in sorted(root.iterdir()):
                    if not candidate.is_dir() or candidate.is_symlink():
                        continue
                    session = SessionStore(candidate)
                    if session.purpose == purpose:
                        candidates.append(candidate)
            candidates.sort(key=lambda path: path.stat().st_mtime)
            summaries = []
            for candidate in candidates:
                summary = summarize_trace(candidate, since_ts=since_ts)
                summaries.append({"session": candidate.name, **summary})
            totals = aggregate_trace_summaries(summaries)
            if args.trace_json:
                print(json.dumps({"mode": "all_sessions", "sessions": summaries,
                                  "totals": totals}, ensure_ascii=False, indent=2))
                return
            print(render_trace_totals(totals, summaries))
            return
        session = SessionStore.open_existing(config.workspace, args.session, purpose=purpose)
        if session is None:
            print(f"no session {args.session} under {config.workspace}", file=sys.stderr)
            raise SystemExit(2)
        summary = summarize_trace(session.root, since_ts=since_ts)
        if args.trace_json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return
        print(render_trace_summary(summary))
        return

    if args.bench_history:
        from app.eval.history import bench_reports, latest_regression, render_bench_history

        stats: dict[str, int] = {}
        rows = bench_reports(config.workspace, limit=args.bench_history_limit, stats=stats)
        skipped_bench_reports = int(stats.get("skipped", 0))
        if skipped_bench_reports:
            print(f"warning: skipped {skipped_bench_reports} corrupt bench reports",
                  file=sys.stderr)
        if args.bench_history_json:
            print(json.dumps({"workspace": str(config.workspace), "count": len(rows),
                              "regression": latest_regression(rows),
                              "skipped_bench_reports": skipped_bench_reports,
                              "reports": rows}, ensure_ascii=False, indent=2))
            return
        print(render_bench_history(rows))
        return

    if args.set_task_priority is not None:
        from app.runtime.tasks import TaskStore, TaskError

        sessions_root = Path(config.workspace) / ".vibe" / "sessions"
        found = None
        if sessions_root.is_dir():
            for session_path in sorted(p for p in sessions_root.iterdir() if p.is_dir() and not p.is_symlink()):
                tasks_root = session_path / "tasks"
                if not (tasks_root / args.set_task_priority).is_dir():
                    continue
                found = (session_path, TaskStore(session_path))
                break
        if found is None:
            print(f"no task {args.set_task_priority} under {config.workspace}", file=sys.stderr)
            raise SystemExit(2)
        session_path, store = found
        try:
            task = store.set_priority(args.set_task_priority, args.task_priority)
        except (ValueError, TaskError) as exc:
            print(f"cannot set task priority: {exc}", file=sys.stderr)
            raise SystemExit(2)
        print(json.dumps({"workspace": str(config.workspace), "session": session_path.name,
                          "task_id": task.task_id, "priority": task.priority,
                          "status": task.status}, ensure_ascii=False, indent=2))
        return

    if args.evidence:
        from app.session.evidence import format_round_evidence

        purpose = "test" if args.test_session or args.fake or config.provider == "fake" else "user"
        session = SessionStore.open_existing(config.workspace, args.session, purpose=purpose)
        if session is None:
            print(f"no session {args.session} under {config.workspace}", file=sys.stderr)
            raise SystemExit(2)
        record = session.latest_round()
        skipped_rounds = int(getattr(session, "last_rounds_skipped", 0) or 0)
        if skipped_rounds:
            print(f"warning: skipped {skipped_rounds} corrupt round records",
                  file=sys.stderr)
        if args.evidence_json:
            print(json.dumps({"session": session.id, "round": record,
                              "skipped_rounds": skipped_rounds},
                             ensure_ascii=False, indent=2))
            return
        print(format_round_evidence(record))
        return

    if args.approve_unattended_profile:
        from app.runtime.notifications import notify
        from app.safety.unattended import (
            UnattendedProfileError, load_profile, profile_hash,
            relaxed_settings, write_profile_approval,
        )

        try:
            profile = load_profile(config.workspace)
        except UnattendedProfileError as exc:
            print(f"cannot load unattended profile: {exc}", file=sys.stderr)
            raise SystemExit(2)
        try:
            path = write_profile_approval(config.workspace, profile, approved_by="user")
        except OSError as exc:
            print(f"cannot write approval: {exc}", file=sys.stderr)
            raise SystemExit(2)
        digest = profile_hash(profile)
        relaxed = relaxed_settings(profile)
        try:
            notify(config.workspace, "unattended_profile_approved",
                   profile=profile.profile_id, hash=digest, relaxed=relaxed)
        except Exception:
            pass
        print(json.dumps({
            "workspace": str(config.workspace), "profile_id": profile.profile_id,
            "profile_hash": digest, "relaxed": relaxed, "approval_file": str(path),
        }, ensure_ascii=False, indent=2))
        return

    if args.prune_worktrees:
        from app.runtime.workspace import WorktreeError, WorktreeManager

        try:
            manager = WorktreeManager(config.workspace)
            worktrees = manager.list()
        except WorktreeError as exc:
            print(f"worktree unavailable: {exc}", file=sys.stderr)
            raise SystemExit(2)
        stale = [item for item in worktrees if item.status == "active" and not item.path.is_dir()]
        applied = []
        if args.prune_apply:
            for item in stale:
                try:
                    manager.remove(item.task_id)
                    applied.append(item.task_id)
                except (WorktreeError, OSError) as exc:
                    applied.append({"task_id": item.task_id, "error": str(exc)[:200]})
        rows = [
            {"task_id": item.task_id, "session_id": item.session_id,
             "path": str(item.path), "branch": item.branch,
             "status": item.status, "stale": item.status == "active" and not item.path.is_dir()}
            for item in worktrees
        ]
        print(json.dumps({
            "workspace": str(config.workspace),
            "worktrees": rows,
            "stale": [item.task_id for item in stale],
            "applied": applied,
        }, ensure_ascii=False, indent=2))
        return

    if args.tasks:
        from app.runtime.supervise import WorkerSupervisor

        purpose = "test" if args.test_session or args.fake or config.provider == "fake" else "user"
        sessions_root = Path(config.workspace) / ".vibe" / "sessions"
        candidates = []
        if sessions_root.is_dir() and sessions_root.resolve().is_relative_to(Path(config.workspace).resolve()):
            for path in sessions_root.iterdir():
                if not path.is_dir() or path.is_symlink():
                    continue
                if not (path / "tasks").is_dir():
                    continue
                if SessionStore(path).purpose != purpose:
                    continue
                candidates.append(path)
        if not candidates:
            print(f"no tasks under {config.workspace}")
            return
        session_root = max(candidates, key=lambda path: (path.stat().st_mtime, path.name))
        supervisor = WorkerSupervisor(session_root)
        snapshots = supervisor.snapshot()
        skipped = int(getattr(supervisor.store, "last_list_skipped", 0) or 0)
        if skipped:
            print(f"warning: skipped {skipped} corrupt task directories under {session_root}",
                  file=sys.stderr)
        if not snapshots:
            print(f"no tasks under {session_root}")
            return
        pending_counts: dict[str, int] = {}
        try:
            from app.runtime.approvals import ApprovalInbox
            for item in ApprovalInbox(session_root).pending():
                task_id = item.get("task_id")
                if isinstance(task_id, str):
                    pending_counts[task_id] = pending_counts.get(task_id, 0) + 1
        except Exception:
            pass
        if args.tasks_json:
            rows = []
            for snapshot in snapshots:
                task = snapshot.task
                worker = snapshot.worker
                rows.append({
                    "task_id": task.task_id, "kind": task.kind, "mode": task.mode,
                    "status": task.status,
                    "segment": task.segments[-1].segment if task.segments else 0,
                    "worker": worker.status if worker is not None else None,
                    "pid": worker.pid if worker is not None else None,
                    "heartbeat": worker.heartbeat_at if worker is not None else None,
                    "stale": snapshot.stale, "plan_id": task.plan_id,
                    "plan_version": task.plan_version, "parent_id": task.parent_id,
                    "priority": task.priority,
                    "pending_approvals": pending_counts.get(task.task_id, 0),
                    "objective": task.objective,
                })
            print(json.dumps({"session": session_root.name, "tasks": rows,
                              "skipped_task_dirs": skipped}, ensure_ascii=False, indent=2))
            return
        print("TASK_ID\tKIND\tMODE\tSTATUS\tSEGMENTS\tWORKER\tPID\tHEARTBEAT\tSTALE\tAPPROVALS\tPRIORITY\tPLAN\tPARENT\tOBJECTIVE")
        for snapshot in snapshots:
            task = snapshot.task
            worker = snapshot.worker
            segment = task.segments[-1].segment if task.segments else 0
            print("\t".join([
                task.task_id,
                task.kind,
                task.mode,
                task.status,
                str(segment),
                worker.status if worker is not None else "-",
                str(worker.pid) if worker is not None else "-",
                worker.heartbeat_at if worker is not None else "-",
                "!" if snapshot.stale else "-",
                str(pending_counts.get(task.task_id, 0)),
                str(task.priority),
                f"{task.plan_id}v{task.plan_version}" if task.plan_id else "-",
                task.parent_id or "-",
                task.objective[:60],
            ]))
        return

    if args.task is not None:
        from app.eval.oneshot import OneShotOptions, run_oneshot

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
        has_tasks = False
        if resumed_session is not None:
            try:
                tasks_dir = resumed_session.root / "tasks"
                has_tasks = tasks_dir.is_dir() and any(tasks_dir.iterdir())
            except OSError:
                has_tasks = False
        if resumed_session is None or (
            not resumed_session.load_context()
            and not os.getenv("VIBE_WORKER")
            and not has_tasks
        ):
            target = f"session {args.session}" if args.session is not None else "conversation"
            print(f"no {target} found under {config.workspace}/.vibe/sessions", file=sys.stderr)
            raise SystemExit(1)

    if args.eval:
        from app.eval.internal import DEFAULT_TASKS, eval_diagnostics, eval_exit_code, load_tasks, run_eval

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
        tools = ToolRegistry(config.tools_root or config.workspace, context=context, state=state,
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
    llm_auditor = None
    if config.llm_auditor and not args.chat_only:
        from app.session.auditor import ReadOnlyAuditor

        llm_auditor = ReadOnlyAuditor(
            provider, tools,
            timeout=config.llm_timeout,
            retries=config.llm_retries,
            require_contract_aligned=config.contract_backcheck,
        ).audit
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
        autonomy=config.autonomy or None,
        completion_guard=config.completion_guard,
        workspace_audit=config.workspace_audit,
        on_round=session.save_round,
        llm_auditor=llm_auditor,
    )
    if not args.stdio:
        raise SystemExit(2)  # main() 已拦截；保留防御，避免误走交互入口。
    from app.stdio import run_stdio

    run_stdio(agent, config, session=session, resumed=args.continue_session or args.session is not None, mcp=mcp)


if __name__ == "__main__":
    main()
