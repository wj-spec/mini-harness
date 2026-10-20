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


class ProgressMonitor:
    """识别工具停滞信号；新结果不代表任务已经完成。"""

    _MUTATIONS = {"write_file", "edit_file"}

    def __init__(self, threshold: int = 2, max_entries: int = 128):
        self.threshold = threshold
        self.max_entries = max(1, max_entries)
        self._observations: OrderedDict[tuple[str, str], tuple[bool, str]] = OrderedDict()
        self._denials: OrderedDict[tuple[str, str], None] = OrderedDict()
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
            return ProgressDecision("stop" if repeated_denial else "warn", "permission_denied", 1)

        if failed:
            return self._stalled("tool_error")
        if call.name == "update_task":
            return self._stalled("state_only")
        if previous == observation:
            return self._stalled("repeated_result")

        if call.name in self._MUTATIONS:
            # 保留写入指纹，清除修改前的读取和测试结果。
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
