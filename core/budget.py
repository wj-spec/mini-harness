from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetPolicy:
    """输出和安全余量固定保留，history 可借用其他分区的空闲额度。"""

    context_window: int = 1_000_000
    output_cap: int = 32_000
    system_cap: int = 8_000
    memory_cap: int = 64_000
    tools_cap: int = 16_000
    history_cap: int | None = None
    scratch_cap: int = 8_000

    @classmethod
    def for_context_window(
        cls,
        context_window: int | None = None,
        output_cap: int | None = None,
        system_cap: int | None = None,
        memory_cap: int | None = None,
        tools_cap: int | None = None,
        history_cap: int | None = None,
        scratch_cap: int | None = None,
    ) -> "BudgetPolicy":
        defaults = cls()
        window = defaults.context_window if context_window is None else context_window
        overrides = {
            "output_cap": output_cap,
            "system_cap": system_cap,
            "memory_cap": memory_cap,
            "tools_cap": tools_cap,
            "scratch_cap": scratch_cap,
        }
        if window < 0 or any(
            value is not None and value < 0
            for value in (*overrides.values(), history_cap)
        ):
            raise ValueError("context budgets must be non-negative")
        scaled_window = min(window, defaults.context_window)
        values = {
            name: (
                override if override is not None
                else getattr(defaults, name) * scaled_window // defaults.context_window
            )
            for name, override in overrides.items()
        }
        reserved = sum(values.values())
        if reserved > window:
            values = {name: value * window // reserved for name, value in values.items()}
        history_limit = (
            None if history_cap is None
            else min(history_cap, max(0, window - sum(values.values())))
        )
        return cls(
            context_window=window,
            history_cap=history_limit,
            **values,
        )

    @classmethod
    def from_book_ratios(cls, *args, **kwargs) -> "BudgetPolicy":
        """兼容旧入口；新代码使用 for_context_window。"""
        return cls.for_context_window(*args, **kwargs)

    @property
    def prompt_cap(self) -> int:
        return max(0, self.context_window - self.output_cap)

    def caps(self) -> dict[str, int]:
        reserved = (
            self.system_cap + self.memory_cap + self.tools_cap
            + self.output_cap + self.scratch_cap
        )
        history = (
            max(0, self.context_window - reserved)
            if self.history_cap is None else self.history_cap
        )
        return {
            "system": self.system_cap,
            "memory": self.memory_cap,
            "tools": self.tools_cap,
            "history": history,
            "output": self.output_cap,
            "scratch": self.scratch_cap,
            "unallocated": max(0, self.context_window - reserved - history),
            "prompt": self.prompt_cap,
        }

    def history_budget(
        self, system_tokens: int = 0, memory_tokens: int = 0, tool_tokens: int = 0
    ) -> int:
        remaining = (
            self.prompt_cap
            - system_tokens
            - memory_tokens
            - tool_tokens
            - self.scratch_cap
        )
        if self.history_cap is not None:
            remaining = min(self.history_cap, remaining)
        return max(0, remaining)
