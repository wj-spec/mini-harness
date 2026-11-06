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


def build_provider(config: Config, fake: bool, debug_path=None):
    if fake or config.provider == "fake":
        if os.getenv("VIBE_FAKE_DEMO", "").strip().lower() in {"1", "true", "yes"}:
            return FakeProvider(_fake_demo_script())
        if os.getenv("VIBE_FAKE_READ", "").strip().lower() in {"1", "true", "yes"}:
            from core.contracts import ToolCall
            return FakeProvider([
                Response(tool_calls=[ToolCall("read_file", {"path": "README.md"}, id="fake-read-1")]),
                "fake read 完成",
            ])
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
    parser.add_argument("--notifications", action="store_true", help="只读输出本工作区最近的本地通知")
    parser.add_argument("--plans", action="store_true", help="只读列出本工作区会话的计划版本")
    parser.add_argument("--acceptance", action="store_true", help="只读列出本工作区会话的计划验收结果")
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
    parser.add_argument("--cleanup-logs", action="store_true", help="显式轮转会话 trace（记录到 .vibe/retention.jsonl）")
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
        if args.stdio or args.eval or args.task is not None or args.list_sessions or args.tasks or args.worker or args.attach or args.merge_task is not None or args.bench or args.stop or args.pause_task is not None:
            parser.error("--cleanup-logs cannot be combined with other modes")
    if args.pause_task is not None:
        if args.workspace is None:
            parser.error("--pause-task requires --workspace")
        if args.stdio or args.eval or args.task is not None or args.list_sessions or args.tasks or args.worker or args.attach or args.merge_task is not None or args.bench or args.stop or args.stop_task is not None:
            parser.error("--pause-task cannot be combined with other modes")
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
    if not args.stdio and not args.eval and not args.list_sessions and not args.tasks and not args.notifications and not args.plans and not args.acceptance and not args.worker and not args.attach and args.merge_task is None and not args.stop and args.pause_task is None and not args.cleanup_logs and args.task is None:
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
        from app.runtime.merge import MergeError, MergeGate

        gate = MergeGate(config.workspace)
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
                    verify_commands=args.merge_verify,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return
            candidate = gate.prepare(task.task_id)
            print(candidate.diff_stat or "(empty diff)")
            approval = gate.request_approval(task.task_id, session_root=session.root)
            print(f"merge approval requested: {approval['approval_id']}")
            print(f"rerun with --approve-merge {approval['approval_id']}")
            raise SystemExit(2)
        except MergeError as exc:
            print(f"merge failed: {exc}", file=sys.stderr)
            raise SystemExit(2)

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

    if args.acceptance:
        from app.runtime.acceptance import load_acceptance_result

        sessions_root = Path(config.workspace) / ".vibe" / "sessions"
        rows = []
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
                            continue
                        saved = load_acceptance_result(task_path, version)
                        if saved is None:
                            continue
                        rows.append({
                            "session": session_path.name, "task_id": task_path.name,
                            "plan_version": version, "status": saved.get("status"),
                            "codes": saved.get("codes", []),
                            "commands": len((saved.get("results") or {})),
                        })
        print(json.dumps({"acceptance": rows[-100:]}, ensure_ascii=False, indent=2))
        return

    if args.plans:
        from app.runtime.plan import PlanError, load_plan

        sessions_root = Path(config.workspace) / ".vibe" / "sessions"
        rows = []
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
                            continue
                        rows.append({
                            "workspace": str(config.workspace),
                            "session": session_path.name,
                            "task_id": task_path.name,
                            "plan_id": plan.plan_id,
                            "version": plan.version,
                            "status": plan.status,
                            "stale": bool(plan.approval.get("stale")),
                            "objective": plan.objective,
                        })
        print(json.dumps({"plans": rows[-100:]}, ensure_ascii=False, indent=2))
        return

    if args.notifications:
        from app.runtime.notifications import read_notifications

        items = read_notifications(config.workspace)
        print(json.dumps({"workspace": str(config.workspace), "notifications": items[-50:]},
                         ensure_ascii=False, indent=2))
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
        snapshots = WorkerSupervisor(session_root).snapshot()
        if not snapshots:
            print(f"no tasks under {session_root}")
            return
        print("TASK_ID\tKIND\tSTATUS\tSEGMENTS\tWORKER\tPID\tHEARTBEAT\tSTALE\tOBJECTIVE")
        for snapshot in snapshots:
            task = snapshot.task
            worker = snapshot.worker
            segment = task.segments[-1].segment if task.segments else 0
            print("\t".join([
                task.task_id,
                task.kind,
                task.status,
                str(segment),
                worker.status if worker is not None else "-",
                str(worker.pid) if worker is not None else "-",
                worker.heartbeat_at if worker is not None else "-",
                "!" if snapshot.stale else "-",
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
