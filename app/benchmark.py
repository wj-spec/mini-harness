"""SWE-bench Lite 开发集：固定数据、容器执行、官方独立评分。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import uuid
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from app.agent import Agent
from app.config import Config
from app.policy import ApprovalDecision, ApprovalGate
from app.providers.fake import FakeProvider
from app.providers.openai_compat import OpenAICompatProvider
from app.swebench_data import (
    DATASET_JSON_NAME, DATASET_NAME, DEFAULT_MANIFEST, REVISION,
    image_name, load_manifest, official_grade_args, prepare_dataset,
)
from app.trace import Pricing
from core.context import ContextBuilder
from core.models import Response, ToolCall
from core.state import TaskState

TOOLS = ["read_file", "grep", "list_files", "write_file", "edit_file", "bash", "recall", "update_task"]
SETUP_TOOLS = {"eval_run", "eval_prepare", "eval_container", "eval_export", "eval_grade"}
BUDGET_STOPS = {"token_budget", "cost_budget", "context_window"}


def _budget(config: Config) -> dict:
    return {name: getattr(config, name) for name in (
        "max_steps", "max_run_seconds", "max_run_tokens", "max_run_cost_usd",
        "compact_interval_tokens", "llm_timeout", "llm_retries", "tool_retries",
        "context_window", "keep_recent_groups",
    )}


def auto_gate(path: Path) -> ApprovalGate:
    async def approve(call, preview):
        return ApprovalDecision(call.name in SETUP_TOOLS or call.name in TOOLS, "once")
    return ApprovalGate(approve, audit_path=path)


async def require_approval(gate: ApprovalGate, name: str, args: dict, root: Path) -> None:
    decision = await gate.request(ToolCall(name, args), json.dumps(args, ensure_ascii=False), workspace=root)
    if not decision.allowed:
        raise PermissionError(f"{name}: denied by evaluation policy")


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _append(path: Path, value) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(value, ensure_ascii=False) + "\n")


def _source_hash() -> str:
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    for folder in ("app", "core"):
        for path in sorted((root / folder).rglob("*.py")):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _protocol(config: Config, fake: bool) -> dict:
    return {
        "suite": "swebench-lite-dev", "protocol_version": 2,
        "dataset": DATASET_NAME, "revision": REVISION, "split": "dev",
        "grader": "swebench==4.1.0", "fake": fake,
        "provider": "scripted" if fake else config.provider, "model": "fake" if fake else config.model,
        "tools": TOOLS, "network": "none (execution container)",
        "skills": "disabled", "mcp": "disabled", "source_sha256": _source_hash(),
        "require_tool_use": True,
        "budget": _budget(config),
        "pricing": {"input_per_1m": config.input_price_per_1m, "output_per_1m": config.output_price_per_1m},
    }


def _provider(config: Config, fake: bool):
    if fake:
        return FakeProvider([
            Response(tool_calls=[ToolCall("bash", {"command": "pwd; git status --short", "timeout": 30}, "smoke")]),
            "Scripted wiring smoke complete; no repair attempted.",
        ])
    return OpenAICompatProvider(
        config.model, config.api_key, config.base_url, timeout=config.llm_timeout,
        trust_env=config.trust_env, stream_usage=config.stream_usage,
    )


def _validate_tasks(tasks: list[dict]) -> None:
    if not tasks:
        raise ValueError("select at least one benchmark task")
    ids = []
    for task in tasks:
        image_name(task["instance_id"])
        if not re.fullmatch(r"[0-9a-f]{40}", task.get("base_commit", "")):
            raise ValueError("invalid base_commit")
        if not isinstance(task.get("problem_statement"), str) or not task["problem_statement"].strip():
            raise ValueError("missing problem_statement")
        ids.append(task["instance_id"])
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate instance IDs")


def _save_report(report: dict) -> None:
    root = Path(report["run_dir"])
    report["execution_summary"] = _execution_summary(report)
    _write_json(root / "report.json", report)
    lines = [
        "# SWE-bench Lite dev", "",
        f"- model: {report['metadata']['model']}",
        f"- scripted: {report['metadata']['fake']}",
        f"- resolved: {report['resolved'] if report['resolved'] is not None else 'ungraded'}/{report['total']}",
        f"- agent completed: {report['execution_summary']['agent_completed']}; "
        f"budget paused: {report['execution_summary']['budget_paused']}; "
        f"runtime errors: {report['execution_summary']['runtime_errors']}",
        "- Development subset; not a full leaderboard score.", "",
        "| instance | grade | agent status | steps | tokens | cost USD |", "|---|---|---|---|---|---|",
    ]
    for result in report["results"]:
        lines.append(f"| {result['instance_id']} | {result['status']} | {result.get('agent_status', '')} | "
                     f"{result.get('steps', 0)} | {result.get('usage', {}).get('total_tokens', 0)} | {result.get('cost_usd', 0):.6f} |")
    diagnostics = _diagnostics(report)
    if diagnostics:
        lines.extend(["", *[f"- {line}" for line in diagnostics]])
    (root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _diagnostics(report: dict) -> list[str]:
    lines = []
    if report.get("grading_error"):
        lines.append(f"grading error: {report['grading_error']}")
    for result in report["results"]:
        prefix = result["instance_id"]
        agent_status = result.get("agent_status")
        if agent_status == "error" or result.get("inference_error"):
            # 旧报告只有 text；导出或清理错误可能覆盖 error。
            reason = result.get("inference_error") or result.get("text") or result.get("error") or "model execution failed"
            lines.append(f"{prefix}: agent error: {reason.removeprefix('error: ')}")
        elif agent_status == "stopped" and result.get("stop_reason") in BUDGET_STOPS:
            lines.append(f"{prefix}: budget paused: {result['stop_reason']} (task unfinished)")
        elif agent_status in {"paused", "stopped", "cancelled", "truncated"}:
            reason = result.get("stop_reason") or result.get("text") or "execution did not finish"
            lines.append(f"{prefix}: agent {agent_status}: {reason}")
        if result["status"] in {"environment_error", "grading_error"}:
            reason = result.get("error") or report.get("grading_error") or "see official report and grader logs"
            lines.append(f"{prefix}: {result['status']}: {reason}")
        elif result.get("error") and agent_status != "error":
            lines.append(f"{prefix}: {result['error']}")
    return lines


def _execution_summary(report: dict) -> dict:
    rows = report["results"]
    errors = sum(
        row.get("agent_status") == "error" or bool(row.get("inference_error"))
        or row["status"] in {"environment_error", "grading_error"}
        for row in rows
    )
    return {
        "agent_completed": sum(row.get("agent_status") == "done" for row in rows),
        "budget_paused": sum(row.get("agent_status") == "stopped"
                             and row.get("stop_reason") in BUDGET_STOPS for row in rows),
        "runtime_errors": max(errors, int(bool(report.get("grading_error")))),
    }


async def run_swebench(
    tasks: list[dict], config: Config, output_root: Path, *, fake: bool = False,
    docker_host: str = "", provider_factory=None, sandbox_factory=None,
) -> dict:
    from app.eval_container import ContainerToolRegistry, DockerSandbox

    _validate_tasks(tasks)
    if not fake and provider_factory is None and not config.api_key:
        raise ValueError("缺少模型 API 配置：请在本机设置 VIBE_API_KEY；--fake 只验证接线")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    root = Path(output_root).resolve() / "swebench-lite-dev" / ("scripted" if fake else "model") / stamp
    gate = auto_gate(root / "audit.jsonl")
    await require_approval(gate, "eval_run", {"instances": [t["instance_id"] for t in tasks]}, root)
    root.mkdir(parents=True, exist_ok=True)
    metadata = _protocol(config, fake)
    metadata["instances"] = [{k: task[k] for k in ("instance_id", "repo", "base_commit")} for task in tasks]
    _write_json(root / "metadata.json", metadata)
    # 完整评分数据留在控制侧，Agent 只看到各自的 workspace。
    _write_json(root / "dataset.json", tasks)
    predictions = root / "predictions.jsonl"
    predictions.write_text("", encoding="utf-8")
    report = {"run_dir": str(root), "total": len(tasks), "resolved": None, "metadata": metadata, "results": []}
    _save_report(report)
    factory = sandbox_factory or DockerSandbox
    for task in tasks:
        task_root = root / task["instance_id"]
        task_root.mkdir()
        sandbox = factory(task_root / "workspace", image_name(task["instance_id"]), docker_host=docker_host)
        item = {"instance_id": task["instance_id"], "status": "environment_error", "error": None}
        started = asyncio.get_running_loop().time()
        try:
            await require_approval(gate, "eval_container", {"image": image_name(task["instance_id"]), "network": "none"}, task_root)
            await sandbox.open()
            item["image_id"] = sandbox.image_id
            code, commit, timed_out = await sandbox.run("git rev-parse HEAD")
            matches = not timed_out and code == 0 and commit.strip() == task["base_commit"]
            if not matches and not timed_out and code == 0 and re.fullmatch(r"[0-9a-f]{40}", commit.strip()):
                # 官方镜像安装后会额外提交一次 SWE-bench，父提交仍须精确对应任务。
                parent_code, parent, parent_timeout = await sandbox.run("git show -s --format='%P%n%s' HEAD")
                matches = not parent_timeout and parent_code == 0 and parent.strip() == task["base_commit"] + "\nSWE-bench"
            if not matches:
                raise RuntimeError(f"container repository base_commit mismatch (exit={code}, output={commit[-500:]!r})")
            item["repository_head"] = commit.strip()
            if (await sandbox.export_patch()).strip():
                raise RuntimeError("dirty container baseline; refusing preexisting changes")
            skills_path = task_root / "skills.json"
            _write_json(skills_path, {"discover": False})
            state = TaskState(goal=task["problem_statement"])
            context = ContextBuilder(
                policy=config.budget_policy(), keep_recent_groups=config.keep_recent_groups,
                offload_dir=task_root / "offload",
            )
            tools = ContainerToolRegistry(sandbox, context=context, state=state, skills_config=str(skills_path))
            provider = provider_factory(task) if provider_factory else _provider(config, fake)
            agent = Agent(
                provider, tools, context=context, state=state, approval=gate,
                pricing=Pricing(config.input_price_per_1m, config.output_price_per_1m),
                max_steps=config.max_steps, max_run_seconds=config.max_run_seconds,
                max_run_tokens=config.max_run_tokens, max_run_cost_usd=config.max_run_cost_usd,
                compact_interval_tokens=config.compact_interval_tokens,
                require_tool_use=True,
                llm_timeout=config.llm_timeout, llm_retries=config.llm_retries,
                llm_retry_backoff=config.llm_retry_backoff, tool_retries=config.tool_retries,
                max_continuations=config.max_continuations, max_no_progress=config.max_no_progress,
                max_calls_without_progress=config.max_calls_without_progress,
                max_reads_per_file=config.max_reads_per_file,
                max_progress_warnings=config.max_progress_warnings,
                max_repeated_calls=config.max_repeated_calls,
                project_instructions=(
                    "Evaluation workspace corresponds to /testbed inside an offline container. "
                    "All bash commands execute there with the repository test environment active. "
                    "Fix the issue in source files, preserve tests, run relevant tests, and report actual results. "
                    "Do not seek or reconstruct benchmark reference patches."
                ),
            )
            result = await agent.run(
                task["problem_statement"],
                on_message=lambda m: _append(task_root / "messages.jsonl", asdict(m)),
                trace=lambda event: _append(task_root / "trace.jsonl", event),
            )
            item.update({"agent_status": result.status, "stop_reason": result.stop_reason,
                         "text": result.text, "steps": result.steps, "usage": result.usage, "cost_usd": result.cost_usd})
            if result.error or result.status == "error":
                item["inference_error"] = item["error"] = result.error or result.text or "model execution failed"
            await require_approval(gate, "eval_export", {"instance_id": task["instance_id"]}, task_root)
            patch = await sandbox.export_patch()
            (task_root / "model.patch").write_text(patch, encoding="utf-8")
            _append(predictions, {"instance_id": task["instance_id"],
                                  "model_name_or_path": "mini-harness-fake" if fake else "mini-harness",
                                  "model_patch": patch})
            item["status"] = "ungraded" if patch.strip() else "no_patch"
        except asyncio.CancelledError:
            item.update(status="cancelled", error="benchmark cancelled")
            raise
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"[:2000]
        finally:
            try:
                await sandbox.close()
            except Exception as exc:
                item.update(status="environment_error", error=f"container cleanup failed: {type(exc).__name__}")
            item["duration_ms"] = round((asyncio.get_running_loop().time() - started) * 1000, 1)
            report["results"].append(item)
            _save_report(report)
    return report


def classify_grade(official: dict, ids: list[str]) -> dict[str, str]:
    groups = {"resolved": "resolved_ids", "unresolved": "unresolved_ids", "environment_error": "error_ids"}
    result = {}
    for status, key in groups.items():
        values = official.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f"invalid official report: {key}")
        for instance_id in values:
            if instance_id in result:
                raise ValueError("overlapping official result categories")
            result[instance_id] = status
    # 官方空补丁不会进入实际测试，仍属于未解决。
    for instance_id in official.get("empty_patch_ids", []):
        result.setdefault(instance_id, "unresolved")
    return {instance_id: result.get(instance_id, "grading_error") for instance_id in ids}


async def grade_run(run_dir: Path, docker_host: str = "", timeout: float = 1800) -> dict:
    root = Path(run_dir).resolve()
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    if importlib.metadata.version("swebench") != "4.1.0":
        raise RuntimeError("评分器版本不符，请运行 uv sync --extra test --extra eval")
    ids = [row["instance_id"] for row in report["results"]]
    run_id = "eval-" + uuid.uuid4().hex[:16]
    grade_root = root / "grade" / run_id
    gate = auto_gate(root / "audit.jsonl")
    await require_approval(gate, "eval_grade", {"instances": ids, "grader": "swebench==4.1.0"}, root)
    grade_root.mkdir(parents=True)
    report["resolved"] = None
    report.pop("official_report", None)
    report.pop("grading_error", None)
    for item in report["results"]:
        if item["status"] != "environment_error":
            item["status"] = "grading_error"
    _save_report(report)
    args = official_grade_args(root / "dataset.json", root / "predictions.jsonl", run_id, grade_root, ids)
    env = dict(os.environ)
    # 官方评分子进程不需要模型凭据，缓存与产物均留 .vibe。
    for name in list(env):
        if name.endswith("API_KEY") or name in {"HF_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"}:
            env.pop(name)
    if docker_host:
        env["DOCKER_HOST"] = docker_host
    env.update(HF_HOME=str(root.parent.parent.parent / "cache" / "huggingface"), HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1")
    log_path = grade_root / "grader.log"
    with log_path.open("wb") as output:
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(
            *args, cwd=grade_root, env=env, stdout=output, stderr=output, start_new_session=True,
        ))
        proc = None
        try:
            proc = await asyncio.shield(spawning)
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except BaseException:
            if proc is None:
                proc = await spawning
            await _stop_grader(proc, ids, run_id, docker_host)
            report["grading_error"] = "official grading interrupted; result unknown"
            _save_report(report)
            raise
    model_label = "mini-harness-fake" if report["metadata"]["fake"] else "mini-harness"
    official_path = grade_root / f"{model_label}.{run_id}.json"
    if proc.returncode or not official_path.exists():
        report["grading_error"] = f"official grader exit={proc.returncode}; see {log_path}"
        _save_report(report)
        return report
    official = json.loads(official_path.read_text(encoding="utf-8"))
    outcomes = classify_grade(official, ids)
    for item in report["results"]:
        if item["status"] != "environment_error":
            item["status"] = outcomes[item["instance_id"]]
    report["resolved"] = sum(row["status"] == "resolved" for row in report["results"])
    report["official_report"] = str(official_path)
    _save_report(report)
    return report


async def _stop_grader(proc, ids: list[str], run_id: str, docker_host: str) -> None:
    if proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
    # 官方进程被终止时 finally 不会执行，只回收本次唯一命名的评分容器。
    names = [f"sweb.eval.{instance_id.lower()}.{run_id}" for instance_id in ids]
    args = ["docker"] + (["--host", docker_host] if docker_host else []) + ["rm", "-f", *names]
    cleanup = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        await asyncio.wait_for(cleanup.wait(), 20)
    except asyncio.TimeoutError:
        cleanup.kill()
        await cleanup.wait()
        raise RuntimeError("grading container cleanup timed out")


def docker_ready(host: str) -> bool:
    if not shutil.which("docker"):
        return False
    args = ["docker"] + (["--host", host] if host else []) + ["info", "--format", "{{.ServerVersion}}"]
    try:
        return subprocess.run(args, capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def doctor(config: Config, docker_host: str) -> dict:
    try:
        grader = importlib.metadata.version("swebench")
    except importlib.metadata.PackageNotFoundError:
        grader = None
    return {"model": config.model, "api_key_configured": bool(config.api_key),
            "docker_ready": docker_ready(docker_host), "grader_version": grader,
            "grader_ready": grader == "4.1.0", "dataset_revision": REVISION,
            "budget": _budget(config)}


async def _prepare(root: Path, manifest: Path) -> list[dict]:
    gate = auto_gate(root / "prepare-audit.jsonl")
    await require_approval(gate, "eval_prepare", {"manifest": str(manifest), "revision": REVISION}, root)
    return await asyncio.to_thread(prepare_dataset, root / "cache", manifest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mini-harness 预算验收与 SWE-bench Lite dev 评测")
    parser.add_argument("action", choices=["check-budget", "doctor", "prepare", "run", "grade", "validate"])
    parser.add_argument("--level", choices=["all", "context", "compaction", "total"],
                        help="check-budget 的离线验收层级，默认 all")
    parser.add_argument("--root", type=Path, default=Path.cwd() / ".vibe" / "benchmarks")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--limit", type=int, default=1, help="公开任务数量，默认先验收 1 题")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--docker-host", default=os.environ.get("DOCKER_HOST", ""))
    parser.add_argument("--fake", action="store_true", help="只验证接线，不代表模型能力")
    parser.add_argument("--skip-grade", action="store_true")
    parser.add_argument("--max-steps", type=int, help="覆盖配置的步数上限；0 不限")
    parser.add_argument("--max-tokens", type=int, help="覆盖配置的累计 token 上限；0 不限")
    parser.add_argument("--max-seconds", type=float, help="覆盖配置的运行时间上限")
    args = parser.parse_args(argv)
    try:
        if args.action == "check-budget":
            from app.budget_checks import run_budget_checks

            if any(value is not None for value in (args.max_steps, args.max_tokens, args.max_seconds)):
                parser.error("check-budget 使用固定离线样例预算；用 --level 选择层级")
            report = asyncio.run(run_budget_checks(args.root, args.level or "all"))
            print(f"budget mechanisms {report['passed']}/{report['total']} -> {report['run_dir']}")
            print("scripted offline checks; not a model benchmark score")
            labels = {"passed": "PASS", "failed": "FAIL", "error": "ERROR", "skipped": "SKIP"}
            for row in report["results"]:
                detail = row.get("error") or row.get("stop_reason") or row["agent_status"]
                print(f"[{labels[row['status']]}] {row['level']}: {detail}")
            if any(row["status"] == "error" for row in report["results"]):
                return 2
            return 0 if report["passed"] == report["total"] else 1
        if args.level is not None:
            parser.error("--level 仅用于 check-budget")
        overrides = {name: value for name, value in (
            ("max_steps", args.max_steps), ("max_run_tokens", args.max_tokens),
            ("max_run_seconds", args.max_seconds),
        ) if value is not None}
        # 交互默认不限累计 token、30 分钟单轮；评测/基准保留 1M / 600s 以保证可比。
        if args.max_tokens is None and "VIBE_MAX_RUN_TOKENS" not in os.environ:
            overrides["max_run_tokens"] = 1_000_000
        if args.max_seconds is None and "VIBE_MAX_RUN_SECONDS" not in os.environ:
            overrides["max_run_seconds"] = 600.0
        config = replace(Config.from_env(), **overrides)
        if (args.limit < 1 or config.max_steps < 0 or config.max_run_tokens < 0
                or not math.isfinite(config.max_run_seconds) or config.max_run_seconds <= 0):
            parser.error("limit / seconds 必须大于零且有限；steps / tokens 不能为负数")
        if args.action == "doctor":
            print(json.dumps(doctor(config, args.docker_host), ensure_ascii=False, indent=2))
            return 0
        if args.action == "grade":
            if args.run_dir is None:
                parser.error("grade 需要 --run-dir")
            report = asyncio.run(grade_run(args.run_dir, args.docker_host))
        else:
            if args.action == "run" and not args.fake and not config.api_key:
                raise ValueError("缺少模型 API 配置，请在本机设置 VIBE_API_KEY；先用 doctor 或 prepare")
            tasks = asyncio.run(_prepare(args.root.resolve(), args.manifest))[:args.limit]
            if args.action == "prepare":
                print(json.dumps({"cache": str(args.root / "cache"), "revision": REVISION,
                                  "instances": [{k: t[k] for k in ("instance_id", "repo", "base_commit")} for t in tasks]}, ensure_ascii=False, indent=2))
                return 0
            if args.action == "validate":
                # 默认只验第一题；--limit 3 可对固定前三题做参考补丁自检。
                return asyncio.run(validate_grader(tasks, config, args.root.resolve(), args.docker_host))
            report = asyncio.run(run_swebench(tasks, config, args.root, fake=args.fake, docker_host=args.docker_host))
            if not args.skip_grade:
                report = asyncio.run(grade_run(Path(report["run_dir"]), args.docker_host))
        print(f"resolved {report['resolved'] if report['resolved'] is not None else 'ungraded'}/{report['total']} -> {report['run_dir']}")
        summary = _execution_summary(report)
        print(f"agent completed {summary['agent_completed']}; budget paused {summary['budget_paused']}; "
              f"runtime errors {summary['runtime_errors']}")
        for line in _diagnostics(report):
            print(line)
        if summary["runtime_errors"]:
            return 2
        return 0 if report["resolved"] == report["total"] else 1
    except (OSError, ValueError, RuntimeError, importlib.metadata.PackageNotFoundError) as exc:
        print(f"benchmark error: {exc}", file=sys.stderr)
        return 2


async def validate_grader(tasks: list[dict], config: Config, root: Path, docker_host: str) -> int:
    """参考答案只用于检查评分环境，不提供给受测 Agent。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    validation = root / "grader-validation" / stamp
    outcomes = {}
    for kind in ("empty", "reference"):
        case_root = validation / kind
        gate = auto_gate(case_root / "audit.jsonl")
        await require_approval(gate, "eval_run", {"validation_only": True, "kind": kind}, case_root)
        metadata = _protocol(config, True)
        metadata["validation_only"] = True
        _write_json(case_root / "metadata.json", metadata)
        _write_json(case_root / "dataset.json", tasks)
        (case_root / "predictions.jsonl").write_text("".join(json.dumps({
            "instance_id": t["instance_id"], "model_name_or_path": "mini-harness-fake",
            "model_patch": t["patch"] if kind == "reference" else "",
        }) + "\n" for t in tasks), encoding="utf-8")
        report = {"run_dir": str(case_root), "metadata": metadata, "total": len(tasks), "resolved": None,
                  "results": [{"instance_id": t["instance_id"], "status": "ungraded"} for t in tasks]}
        _save_report(report)
        report = await grade_run(case_root, docker_host)
        outcomes[kind] = {
            "resolved": report["resolved"], "total": report["total"], "run_dir": str(case_root),
            "runtime_errors": _execution_summary(report)["runtime_errors"],
            # 逐题记录，参考补丁失败时可归因到具体环境或题目。
            "results": {row["instance_id"]: row.get("status") for row in report.get("results", [])},
            "error_ids": [row["instance_id"] for row in report.get("results", [])
                          if row.get("status") in ("environment_error", "grading_error")],
        }
    passed = outcomes["empty"]["resolved"] == 0 and outcomes["reference"]["resolved"] == len(tasks)
    _write_json(validation / "validation.json", {"validation_only": True, "passed": passed, "outcomes": outcomes})
    print(json.dumps({"validation_only": True, "passed": passed, "outcomes": outcomes}, ensure_ascii=False, indent=2))
    return 2 if any(outcome["runtime_errors"] for outcome in outcomes.values()) else 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
