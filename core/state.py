from __future__ import annotations

from dataclasses import dataclass, field

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
) -> str:
    """harness 生成的交付核对：运行结束与任务验收分开呈现，不代替人工核对。"""
    tokens = (usage or {}).get("total_tokens", 0)
    verified = [item for item in state.command_results if item.get("verification")]
    checks = "; ".join(
        f"{_clip(item['command'], 80)} [{item['status']}, exit={item.get('exit_code')}]"
        for item in verified[-3:]
    ) or "未运行验证命令"
    changed = ", ".join(state.files_changed[:8]) or "无（未记录文件修改）"
    unverified: list[str] = []
    if state.files_changed and not verified:
        unverified.append("本轮改动均未经验证")
    if status != "done":
        unverified.append(f"运行以 {status} 结束，已发生改动仍需核对")
    if task_kind == "action" and tool_calls == 0:
        blocker = "审批拒绝（决定已尊重，未重试）" if denied else "模型未执行操作"
        unverified.append(f"动作任务没有已执行的操作：{blocker}")
    if verified and state.files_changed:
        unverified.append("改动与验证的对应关系需人工核对")
    acceptance = "有验证证据，请核对后确认" if verified and status == "done" else "未确认，请核对以下证据"
    return "\n".join([
        "[运行结束] " + f"status={status} steps={steps} tokens={tokens} cost=${cost_usd:.4f}",
        "[任务验收] " + acceptance,
        "本轮改动（工具记录）：" + changed,
        "实际验证（含退出码）：" + checks,
        "未验证项：" + ("; ".join(unverified) or "无"),
    ])


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

    def render(self) -> str:
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

    def observe_tool(self, name: str, args: dict, ok: bool, output: str,
                     execution: ExecutionOutcome | None = None) -> None:
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
                                         "verification": verification, "output": str(output), **execution})
            if verification and command not in self.tests_run:
                self.tests_run.append(command)
        if not ok:
            self.notes.append(f"{name} failed: {_clip(output, 160)}")
