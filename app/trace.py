from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Callable


@dataclass
class Pricing:
    input_per_1m: float = 0.0
    output_per_1m: float = 0.0

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens / 1_000_000 * self.input_per_1m
            + completion_tokens / 1_000_000 * self.output_per_1m
        )


class TraceRecorder:
    """一次 run 的 span / usage / cost；emit 为空时只聚合不落盘。

    tool_start / approval / tool_end 是后续 Agent 行为 trace 的预留接口，
    当前 run 循环尚未调用；不要按“无调用方”删除。
    """

    def __init__(
        self,
        emit: Callable[[dict], None] | None = None,
        pricing: Pricing | None = None,
        model: str = "",
    ):
        self.emit = emit
        self.pricing = pricing or Pricing()
        self.model = model
        self.run_id = uuid.uuid4().hex[:8]
        self.started = time.monotonic()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.cost_usd = 0.0
        self.usage_estimated = False
        self.tool_calls = 0
        self._llm_started: float | None = None
        self._request_id: str | None = None
        self._tool_started: dict[int, float] = {}

    def event(self, event_type: str, **data) -> None:
        if self.emit is None:
            return
        self.emit(
            {
                "type": event_type,
                "ts": time.time(),
                "run_id": self.run_id,
                "model": self.model,
                **data,
            }
        )

    def start(self, task: str | None) -> None:
        self.event("run_start", task=(task or "")[:200])

    def llm_start(self, step: int, request_id: str | None = None) -> None:
        self._llm_started = time.monotonic()
        self._request_id = request_id
        self.event("llm_start", step=step, request_id=request_id)

    def llm_end(
        self, step: int, usage: dict | None, finish_reason: str | None,
        estimated: bool = False,
    ) -> None:
        usage = usage or {}
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        total = int(usage.get("total_tokens") or (prompt + completion))
        cost = self.pricing.cost(prompt, completion)
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total
        self.usage_estimated = self.usage_estimated or estimated
        self.cost_usd += cost
        duration = 0.0
        if self._llm_started is not None:
            duration = (time.monotonic() - self._llm_started) * 1000
        self.event(
            "llm_end",
            step=step,
            request_id=self._request_id,
            duration_ms=round(duration, 1),
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            cost_usd=cost,
            finish_reason=finish_reason,
            usage_estimated=estimated,
        )

    def tool_start(self, call, step: int | None = None) -> None:
        self._tool_started[id(call)] = time.monotonic()
        self.event("tool_start", tool=call.name, args=call.args,
                   tool_call_id=call.id, step=step)

    def approval(self, call, allowed: bool, scope: str, step: int | None = None) -> None:
        self.event("approval", tool=call.name, allowed=allowed, scope=scope,
                   tool_call_id=call.id, step=step)

    def tool_end(self, call, ok: bool, risk: str, step: int | None = None,
                 status: str | None = None, error: str | None = None) -> None:
        self.tool_calls += 1
        started = self._tool_started.pop(id(call), None)
        duration = (time.monotonic() - started) * 1000 if started is not None else 0.0
        payload: dict = {}
        if error:
            payload["error"] = str(error)[:300]
        self.event(
            "tool_end",
            tool=call.name,
            ok=ok,
            risk=risk,
            duration_ms=round(duration, 1),
            tool_call_id=call.id,
            step=step,
            status=status or ("succeeded" if ok else "failed"),
            **payload,
        )

    @staticmethod
    def anomaly(events: list[dict]) -> dict | None:
        """从本地 trace 记录定位首个异常工具事件（终态失败、未执行或缺失终态）。"""
        started: dict[str, dict] = {}
        for event in events:
            kind = event.get("type")
            call_id = str(event.get("tool_call_id") or "")
            if kind == "tool_start" and call_id:
                started.setdefault(call_id, event)
                continue
            if kind in {"tool_end", "tool_not_executed"} and call_id:
                started.pop(call_id, None)
            if kind == "tool_not_executed":
                return event
            if kind == "tool_end" and (not event.get("ok") or event.get("status") not in (None, "succeeded")):
                return event
        for call_id, event in started.items():
            return {"type": "missing_tool_end", "tool_call_id": call_id,
                    "tool": event.get("tool"), "step": event.get("step")}
        return None

    def end(
        self, status: str, steps: int, stop_reason: str = "", limit_tokens: int = 0,
    ) -> dict:
        summary = {
            "run_id": self.run_id,
            "status": status,
            "steps": steps,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "tool_calls": self.tool_calls,
            "duration_ms": round((time.monotonic() - self.started) * 1000, 1),
            "stop_reason": stop_reason,
            "limit_tokens": limit_tokens,
            "remaining_tokens": max(0, limit_tokens - self.total_tokens),
            "usage_estimated": self.usage_estimated,
        }
        self.event("run_end", **summary)
        return summary

    def usage(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }
