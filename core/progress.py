from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from typing import Literal

from core.models import ToolCall


@dataclass(frozen=True)
class ProgressDecision:
    action: Literal["continue", "warn", "stop"]
    reason: str
    streak: int


@dataclass(frozen=True)
class ArtifactDecision:
    action: Literal["continue", "warn", "stop"]
    reason: str
    count: int


class ArtifactProgress:
    """动作任务的真实进展：只有文件内容变化或验证结果变化才清零停滞计数。"""

    def __init__(self, threshold: int = 20, max_warnings: int = 2):
        self.threshold = max(1, threshold)
        self.max_warnings = max(1, max_warnings)
        self._files: dict[str, str] = {}
        self._verifications: dict[str, str] = {}
        self._calls = 0
        self._warnings = 0

    def observe(
        self,
        fingerprint: dict[str, str] | None = None,
        verification: tuple[str, str] | None = None,
    ) -> ArtifactDecision:
        self._calls += 1
        changed = False
        if fingerprint:
            for path, digest in fingerprint.items():
                if self._files.get(path) != digest:
                    self._files[path] = digest
                    changed = True
        if verification:
            key, digest = verification
            if self._verifications.get(key) != digest:
                self._verifications[key] = digest
                changed = True
        if changed:
            self._calls = 0
            self._warnings = 0
            return ArtifactDecision("continue", "artifact_progress", 0)
        if self._calls < self.threshold:
            return ArtifactDecision("continue", "no_artifact_progress", self._calls)
        self._calls = 0
        self._warnings += 1
        action = "stop" if self._warnings >= self.max_warnings else "warn"
        return ArtifactDecision(action, "no_artifact_progress", self._warnings)


class ProgressMonitor:
    """识别工具停滞信号；新结果不代表任务已经完成。"""

    _MUTATIONS = {"write_file", "edit_file", "apply_patch"}

    def __init__(
        self, threshold: int = 2, max_entries: int = 128,
        max_reads_per_file: int = 5, max_warnings: int = 4,
        max_repeated_calls: int = 6,
    ):
        self.threshold = threshold
        self.max_entries = max(1, max_entries)
        self.max_reads_per_file = max(1, max_reads_per_file)
        self.max_warnings = max(1, max_warnings)
        self.max_repeated_calls = max(1, max_repeated_calls)
        self._call_counts: dict[tuple[str, str], int] = {}
        self._observations: OrderedDict[tuple[str, str], tuple[bool, str]] = OrderedDict()
        self._denials: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._read_counts: dict[str, int] = {}
        self._warnings = 0
        self._streak = 0
        self._warned = False

    def observe(self, call: ToolCall, ok: bool, output: str) -> ProgressDecision:
        if self.threshold <= 0:
            return ProgressDecision("continue", "disabled", 0)

        key = (call.name, json.dumps(call.args, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
        digest = hashlib.sha256(output.encode("utf-8")).hexdigest()
        failed = not ok or output.startswith("ERROR") or self._bash_failed(call, output)
        observation = (not failed, digest)
        previous = self._observations.get(key)
        self._remember(self._observations, key, observation)

        if output.startswith("ERROR denied_by_user") or output.startswith("ERROR approval_required"):
            repeated_denial = key in self._denials
            self._remember(self._denials, key, None)
            self._warned = True
            self._streak = 0
            self._warnings += 1
            if self._warnings > self.max_warnings:
                return ProgressDecision("stop", "permission_denied", 1)
            return ProgressDecision("stop" if repeated_denial else "warn", "permission_denied", 1)

        if failed:
            return self._stalled("tool_error")
        self._call_counts[key] = self._call_counts.get(key, 0) + 1
        if self._call_counts[key] >= self.max_repeated_calls:
            return self._stalled("repeated_call")
        if call.name == "update_task":
            return self._stalled("state_only")
        if call.name == "read_file":
            path = call.args.get("path")
            if isinstance(path, str):
                count = self._read_counts.get(path, 0) + 1
                self._read_counts[path] = count
                if count > self.max_reads_per_file:
                    return self._stalled("repeated_read")
        if previous == observation:
            return self._stalled("repeated_result")

        if call.name in self._MUTATIONS:
            # 成功修改后允许重新读取；同时保留写入指纹，其他调用计数清零。
            self._read_counts.clear()
            self._call_counts = {
                observed_key: count for observed_key, count in self._call_counts.items()
                if observed_key[0] in self._MUTATIONS
            }
            for observed_key in list(self._observations):
                if observed_key[0] not in self._MUTATIONS:
                    del self._observations[observed_key]
        self._streak = 0
        self._warned = False
        return ProgressDecision("continue", "new_result", 0)

    def _stalled(self, reason: str) -> ProgressDecision:
        self._streak += 1
        streak = self._streak
        if streak < self.threshold:
            return ProgressDecision("continue", reason, streak)
        self._warnings += 1
        if self._warnings > self.max_warnings:
            self._streak = 0
            return ProgressDecision("stop", reason, streak)
        action = "stop" if self._warned else "warn"
        self._warned = True
        self._streak = 0
        return ProgressDecision(action, reason, streak)

    def _remember(self, cache: OrderedDict, key: tuple[str, str], value) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self.max_entries:
            cache.popitem(last=False)

    @staticmethod
    def _bash_failed(call: ToolCall, output: str) -> bool:
        if call.name != "bash":
            return False
        prefix = f"$ {call.args.get('command', '')}\n"
        if not output.startswith(prefix):
            return False
        lines = output[len(prefix):].splitlines()
        if len(lines) < 2 or not lines[0].startswith("(cwd="):
            return False
        status = lines[1]
        if status.startswith("ERROR"):
            return True
        if status.startswith("exit="):
            try:
                return int(status[5:]) != 0
            except ValueError:
                return False
        return False
