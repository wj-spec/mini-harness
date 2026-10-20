from __future__ import annotations

from dataclasses import dataclass, field

from .models import Message


def _clip(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


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
            lines.append("Tests run: " + " | ".join(_clip(x, 120) for x in self.tests_run[-3:]))
        if self.notes:
            lines.append("Notes: " + " | ".join(_clip(x, 160) for x in self.notes[-3:]))
        return "\n".join(lines)

    def to_message(self) -> Message:
        return Message("system", self.render(), name="task_state")

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

    def observe_tool(self, name: str, args: dict, ok: bool, output: str) -> None:
        if name in {"write_file", "edit_file"} and args.get("path"):
            path = str(args["path"])
            if path not in self.files_changed:
                self.files_changed.append(path)
        if name == "bash" and args.get("command"):
            command = _clip(str(args["command"]), 120)
            if command not in self.tests_run:
                self.tests_run.append(command)
        if not ok:
            self.notes.append(f"{name} failed: {_clip(output, 160)}")
