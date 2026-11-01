from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import ExecutionOutcome


_ACTION_MARKERS = (
    "创建", "新建", "修改", "删除", "修复", "实现", "添加", "重构", "安装", "替换",
    "更新", "迁移", "调试", "改写", "写一个", "写个", "补一个", "改成", "落地",
    "fix", "add", "create", "implement", "change", "update", "remove", "delete",
    "refactor", "install", "write", "migrate", "debug", "build", "apply",
)
_QUESTION_MARKERS = (
    "如何", "怎么", "为什么", "什么是", "吗", "？", "?", "explain", "how ", "what ",
    "why ", "which ", "是否", "多少", "几个",
)


def classify_task(goal: str) -> str:
    """启发式区分动作任务与问答任务，只影响交付核对的呈现，不参与执行决策。"""
    text = goal.strip().lower()
    if not text:
        return "qa"
    if any(marker in text for marker in _QUESTION_MARKERS):
        return "qa"
    return "action" if any(marker in text for marker in _ACTION_MARKERS) else "qa"


@dataclass(frozen=True)
class CompletionDecision:
    """完成守卫的确定性结论；只依据 harness 能核对的环境证据。"""

    allowed: bool
    codes: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "codes": list(self.codes),
                "messages": list(self.messages)}

    @property
    def reason_text(self) -> str:
        return "；".join(self.messages)

    def render(self) -> str:
        if self.allowed:
            return "通过"
        return "未通过：" + self.reason_text


def completion_decision(
    state: "TaskState",
    *,
    task_kind: str | None = None,
    start_mutation_seq: int = 0,
    start_file_changes: int = 0,
) -> CompletionDecision:
    """动作任务在**本轮**有文件改动时，必须有覆盖最后一次改动的成功验证。

    start_* 是 run 开始时 TaskState 的累计值；默认 0 表示按整段会话判断
    （直接调用兼容）。Agent 传入 run 起点，避免第二轮继承上一轮证据。
    只使用 harness 观测到的事实：model 的 update_task/自述不参与判定。
    QA 任务与本轮没有动作改动的任务不启用该守卫，避免误伤只读任务。
    """
    kind = task_kind or classify_task(state.goal)
    if kind != "action":
        return CompletionDecision(True)
    start_mutation_seq = max(0, int(start_mutation_seq))
    changed = len(state.file_changes) > max(0, int(start_file_changes))
    if not changed:
        return CompletionDecision(True)

    codes: list[str] = []
    messages: list[str] = []
    if state.last_verification_seq is None or state.last_verification_seq <= start_mutation_seq:
        codes.append("no_verification")
        messages.append("本轮有文件改动，但没有记录到验证命令")
    elif state.last_verification_seq < state.mutation_seq:
        codes.append("mutation_after_verification")
        messages.append("最后一次改动发生在最后一次验证之后，需要重新验证")
    elif not state.last_verification_ok:
        codes.append("last_verification_failed")
        messages.append("最后一次验证命令失败，不能按完成交付")
    return CompletionDecision(not codes, tuple(codes), tuple(messages))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()



def delivery_receipt(
    state: "TaskState",
    *,
    status: str,
    steps: int = 0,
    usage: dict | None = None,
    cost_usd: float = 0.0,
    tool_calls: int = 0,
    denied: bool = False,
    task_kind: str = "qa",
    verified: bool | None = None,
    completion: "CompletionDecision | None" = None,
    audit: dict | None = None,
    start_command_results: int = 0,
    start_files_changed: int = 0,
) -> str:
    """harness 生成的交付核对：运行结束与任务验收分开呈现，不代替人工核对。

    start_* 用于只统计本轮（run 起点）的验证命令与改动文件；默认 0 兼容旧调用。
    """
    tokens = (usage or {}).get("total_tokens", 0)
    verified_commands = [
        item for item in state.command_results[max(0, int(start_command_results)):]
        if item.get("verification")
    ]
    run_files = list(state.files_changed[max(0, int(start_files_changed)):])
    checks = "; ".join(
        f"{_clip(item['command'], 80)} [{item['status']}, exit={item.get('exit_code')}]"
        for item in verified_commands[-3:]
    ) or "未运行验证命令"
    changed = ", ".join(run_files[:8]) or "无（未记录文件修改）"
    unverified: list[str] = []
    if run_files and not verified_commands:
        unverified.append("本轮改动均未经验证")
    if status != "done":
        unverified.append(f"运行以 {status} 结束，已发生改动仍需核对")
    if task_kind == "action" and tool_calls == 0:
        blocker = "审批拒绝（决定已尊重，未重试）" if denied else "模型未执行操作"
        unverified.append(f"动作任务没有已执行的操作：{blocker}")
    if verified_commands and run_files:
        unverified.append("改动与验证的对应关系需人工核对")

    if verified is True:
        acceptance = "已验证（有环境证据）；仍需你核对是否满足原始需求"
    elif verified is False:
        reason = completion.reason_text if completion is not None and completion.messages else ""
        if not reason and audit:
            findings = audit.get("findings") or []
            reason = "；".join(str(item) for item in findings[:3])
        acceptance = "未验证：" + (reason or "未通过完成守卫")
        if completion is not None and completion.messages and completion.reason_text not in unverified:
            unverified.append(completion.reason_text)
    else:
        acceptance = (
            "有验证证据，请核对后确认"
            if verified_commands and status == "done" else "未确认，请核对以下证据"
        )

    lines = [
        "[运行结束] " + f"status={status} steps={steps} tokens={tokens} cost=${cost_usd:.4f}",
        "[任务验收] " + acceptance,
        "本轮改动（工具记录）：" + changed,
        "实际验证（含退出码）：" + checks,
        "未验证项：" + ("; ".join(unverified) or "无"),
    ]
    if completion is not None:
        lines.append("完成判定：" + completion.render())
    if audit:
        lines.append(
            "环境审计：status=" + str(audit.get("status", "unknown"))
            + " integrity=" + str(audit.get("integrity", "unknown"))
            + " verified=" + str(bool(audit.get("verified")))
        )
        llm = audit.get("llm")
        if isinstance(llm, dict):
            lines.append(
                "LLM 审计：status=" + str(llm.get("status", "unknown"))
                + " integrity=" + str(llm.get("integrity", "unknown"))
                + " contract=" + str(llm.get("contract_audit", "unknown"))
            )
    return "\n".join(lines)



def _clip(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _patch_paths(patch: str) -> list[str]:
    """从 apply_patch 的 diff 头提取目标路径；core 不依赖 app。"""
    import re
    found: list[str] = []
    for match in re.finditer(r"^diff --git a/(.+?) b/(.+)$", patch, re.M):
        found.append(match.group(2))
    if not found:
        for match in re.finditer(r"^\+\+\+ b/(.+)$", patch, re.M):
            found.append(match.group(1))
    return list(dict.fromkeys(found))


@dataclass
class TaskState:
    """harness 侧的任务进度；不依赖模型记忆。"""

    goal: str
    plan: list[str] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    open: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    tests_run: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    command_results: list[dict] = field(default_factory=list)
    constraints: list[dict] = field(default_factory=list)
    file_changes: list[dict] = field(default_factory=list)
    verifications: list[dict] = field(default_factory=list)
    mutation_seq: int = 0
    last_verification_seq: int | None = None
    last_verification_ok: bool | None = None

    def render(self, *, start_mutation_seq: int = 0, start_file_changes: int = 0) -> str:
        lines = ["[TASK STATE]", f"Goal: {_clip(self.goal, 300)}"]
        if self.plan:
            lines.append("Plan: " + " | ".join(_clip(x, 120) for x in self.plan[:5]))
        if self.done:
            lines.append("Done: " + " | ".join(_clip(x, 120) for x in self.done[:5]))
        if self.open:
            lines.append("Open: " + " | ".join(_clip(x, 120) for x in self.open[:5]))
        if self.files_changed:
            lines.append("Files changed: " + " | ".join(self.files_changed[:8]))
        if self.tests_run:
            lines.append("Recorded checks/legacy commands (not proof of passing): "
                         + " | ".join(_clip(x, 120) for x in self.tests_run[-3:]))
        if self.command_results:
            results = []
            for item in self.command_results[-5:]:
                kind = "verification" if item.get("verification") else "command"
                code = item.get("exit_code")
                results.append(f"{kind}: {_clip(item['command'], 120)} -> {item['status']} "
                               f"(exit={code if code is not None else 'unknown'})")
            lines.append("Command results: " + " | ".join(results))
        if self.file_changes:
            latest = self.file_changes[-5:]
            lines.append(
                "Recorded file changes: " + " | ".join(
                    f"{_clip(str(item.get('path', '')), 100)} [{item.get('kind', '?')}]"
                    for item in latest
                )
            )
        if self.verifications:
            latest = self.verifications[-3:]
            lines.append(
                "Verification attempts: " + " | ".join(
                    f"{_clip(str(item.get('command', '')), 100)} "
                    f"[{item.get('status', '?')}, exit={item.get('exit_code')}]"
                    for item in latest
                )
            )
        if len(self.file_changes) > max(0, int(start_file_changes)):
            decision = completion_decision(
                self,
                start_mutation_seq=start_mutation_seq,
                start_file_changes=start_file_changes,
            )
            if decision.codes:
                lines.append("Completion guard: blocked (" + ", ".join(decision.codes) + ")")
        if self.notes:
            lines.append("Notes: " + " | ".join(_clip(x, 160) for x in self.notes[-3:]))
        if self.constraints:
            lines.append("Constraints (by time; later entries override earlier ones on conflict):")
            for item in self.constraints:
                lines.append(f"  {item['seq']}. ({item['source']}) {_clip(item['text'], 300)}")
        return "\n".join(lines)

    def add_constraint(self, text: str, source: str) -> str:
        """记录当前有效约束；保留来源与先后顺序，新的覆盖旧的。"""
        cleaned = " ".join(str(text).split())
        if cleaned and (not self.constraints or self.constraints[-1]["text"] != cleaned):
            self.constraints.append({"text": cleaned, "source": source, "seq": len(self.constraints) + 1})
        return self.render()

    def command_summary(self) -> str:
        if self.command_results:
            return "; ".join(f"{_clip(item['command'], 120)} [{item['status']}, "
                             f"exit={item.get('exit_code')}]" for item in self.command_results[-5:])
        return "; ".join(f"{_clip(command, 120)} [unverified]" for command in self.tests_run[-3:]) or "无"

    def update(
        self,
        plan: list[str] | None = None,
        done: list[str] | None = None,
        open: list[str] | None = None,
        note: str | None = None,
    ) -> str:
        if plan is not None:
            self.plan = [str(x) for x in plan]
        if done is not None:
            self.done = [str(x) for x in done]
        if open is not None:
            self.open = [str(x) for x in open]
        if note:
            self.notes.append(str(note))
        return self.render()

    def record_file_changes(
        self,
        *,
        before: dict[str, dict],
        after: dict[str, dict],
        tool: str,
        step: int | None = None,
        tool_call_id: str | None = None,
        approval_scope: str = "",
    ) -> list[dict]:
        """记录一次工具调用造成的文件变化；只存路径与前后 hash，不存正文。"""
        recorded: list[dict] = []
        for path in sorted(set(before) | set(after)):
            old = before.get(path) or {}
            new = after.get(path) or {}
            old_status = str(old.get("status") or "missing")
            new_status = str(new.get("status") or "missing")
            old_sha = old.get("sha256") if old_status == "present" else None
            new_sha = new.get("sha256") if new_status == "present" else None
            if old_status == new_status and old_sha == new_sha:
                continue
            if new_status == "missing" and old_status != "missing":
                kind = "delete"
            elif old_status == "missing" and new_status != "missing":
                kind = "create"
            else:
                kind = "modify"
            record = {
                "path": path,
                "kind": kind,
                "before_sha256": old_sha,
                "after_sha256": new_sha,
                "before_status": old_status,
                "after_status": new_status,
                "size": new.get("size"),
                "step": step,
                "tool": tool,
                "tool_call_id": tool_call_id,
                "approval_scope": approval_scope,
                "at": _now(),
            }
            self.file_changes.append(record)
            recorded.append(record)
            if path not in self.files_changed:
                self.files_changed.append(path)
        if recorded:
            self.mutation_seq += 1
        return recorded

    def record_verification(
        self,
        *,
        command: str,
        status: str,
        exit_code: int | None,
        output_digest: str = "",
        step: int | None = None,
    ) -> dict:
        """记录一次验证命令；只有 succeeded 且 exit=0 才算通过。"""
        ok = status == "succeeded" and exit_code == 0
        record = {
            "command": command,
            "status": status,
            "exit_code": exit_code,
            "ok": ok,
            "output_digest": output_digest,
            "step": step,
            "mutation_seq": self.mutation_seq,
            "at": _now(),
        }
        self.verifications.append(record)
        self.last_verification_seq = self.mutation_seq
        self.last_verification_ok = ok
        return record

    def observe_tool(self, name: str, args: dict, ok: bool, output: str,
                     execution: ExecutionOutcome | None = None,
                     step: int | None = None) -> None:
        if ok and name in {"write_file", "edit_file"} and args.get("path"):
            path = str(args["path"])
            if path not in self.files_changed:
                self.files_changed.append(path)
        if ok and name == "apply_patch" and isinstance(args.get("patch"), str):
            for path in _patch_paths(str(args["patch"])):
                if path not in self.files_changed:
                    self.files_changed.append(path)
        if name == "bash" and args.get("command") and execution is not None:
            command = str(args["command"])
            verification = args.get("verification") is True
            self.command_results.append({"command": command, "cwd": str(args.get("cwd", ".")),
                                         "verification": verification, "output": str(output),
                                         "step": step, **execution})
            if verification and command not in self.tests_run:
                self.tests_run.append(command)
        if not ok:
            self.notes.append(f"{name} failed: {_clip(output, 160)}")
