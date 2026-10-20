from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .budget import BudgetPolicy
from .models import Message, ToolCall
from .summarize import summarize_messages

Estimator = Callable[[str], int]
Summarizer = Callable[[list[Message]], str]
_HISTORY_SUMMARIES = {"history_summary_l1", "history_summary_l2"}
_OFFLOAD_REF = re.compile(r"\[offloaded: ([^\]]+)\]")


def estimate_tokens(text: str) -> int:
    """便宜的估算：英文 4 字符 ≈ 1 token；中文约 1 字 ≈ 1 token。"""
    if not text:
        return 1
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return max(1, cjk + (len(text) - cjk) // 4)


def message_tokens(message: Message, estimator: Estimator = estimate_tokens) -> int:
    n = estimator(message.content)
    for call in message.tool_calls:
        n += estimator(call.name + json.dumps(call.args, ensure_ascii=False, sort_keys=True))
    return n


def total_tokens(messages: Iterable[Message], estimator: Estimator = estimate_tokens) -> int:
    return sum(message_tokens(m, estimator) for m in messages)


def stable_prefix_hash(messages: Iterable[Message]) -> str:
    """system 前缀的指纹；history 变化不应改变它。"""
    h = hashlib.sha256()
    for m in messages:
        if m.role == "system" and m.name != "task_state":
            h.update(m.content.encode("utf-8"))
    return h.hexdigest()[:12]


@dataclass
class ContextResult:
    messages: list[Message]
    tokens: int
    offloaded: list[str] = field(default_factory=list)
    summarized: bool = False
    over_budget: bool = False
    notes: list[str] = field(default_factory=list)
    sections: dict[str, int] = field(default_factory=dict)
    caps: dict[str, int] = field(default_factory=dict)


def _group(messages: list[Message]) -> list[list[Message]]:
    """把 assistant(tool_calls) 和紧随的 tool 结果合成一组，不能拆。"""
    groups: list[list[Message]] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.role == "assistant" and m.tool_calls:
            j = i + 1
            while j < len(messages) and messages[j].role == "tool":
                j += 1
            groups.append(messages[i:j])
            i = j
        else:
            groups.append([m])
            i += 1
    return groups


def _flatten(groups: list[list[Message]]) -> list[Message]:
    return [m for group in groups for m in group]


class ContextBuilder:
    def __init__(
        self,
        max_tokens: int = 800,
        reserve_output: int = 200,
        keep_recent_groups: int = 2,
        pin_first_user: bool = True,
        offload_dir: str | Path | None = None,
        summarizer: Summarizer | None = None,
        estimator: Estimator = estimate_tokens,
        policy: BudgetPolicy | None = None,
    ):
        self.max_tokens = max_tokens
        self.reserve_output = reserve_output
        self.keep_recent_groups = keep_recent_groups
        self.pin_first_user = pin_first_user
        self.offload_dir = Path(offload_dir) if offload_dir else None
        self.summarizer = summarizer
        self.estimator = estimator
        self.policy = policy
        self._refs: dict[str, list[Message]] = {}

    @property
    def budget(self) -> int:
        return max(0, self.max_tokens - self.reserve_output)

    @property
    def display_budget(self) -> int:
        return self.policy.prompt_cap if self.policy is not None else self.budget

    def build(
        self,
        messages: list[Message],
        tool_tokens: int = 0,
        memory_tokens: int = 0,
    ) -> ContextResult:
        if self.policy is not None:
            return self._build_sectioned(messages, tool_tokens, memory_tokens)
        return self._build_flat(messages, tool_tokens, memory_tokens)

    def compact_history(
        self,
        messages: list[Message],
        history_budget: int,
        keep_recent_groups: int | None = None,
        summarizer: Summarizer | None = None,
    ) -> ContextResult:
        """主动压缩 history；保留 system、原始目标与最新用户指令。"""
        history_budget = max(0, history_budget)
        system = [m for m in messages if m.role == "system"]
        history = [m for m in messages if m.role != "system"]
        first_user = next((m for m in history if m.role == "user"), None)
        latest_user = next((m for m in reversed(history) if m.role == "user"), None)
        pinned = [first_user] if first_user is not None else []
        rest = list(history)
        if first_user is not None:
            rest.remove(first_user)
        summaries: list[Message] = []
        groups: list[list[Message]] = []
        for group in _group(rest):
            if len(group) == 1 and group[0].role == "assistant" and group[0].name in _HISTORY_SUMMARIES:
                summaries.extend(group)
            else:
                groups.append(group)

        keep = max(0, self.keep_recent_groups if keep_recent_groups is None else keep_recent_groups)
        split = max(0, len(groups) - keep)
        older, recent = [], []
        for index, group in enumerate(groups):
            target = recent if index >= split or any(m is latest_user for m in group) else older
            target.append(group)
        initial_recent = len(recent)
        notes: list[str] = []

        def result(out: list[Message], refs: list[str], summarized: bool) -> ContextResult:
            before = total_tokens(history, self.estimator)
            after = total_tokens([m for m in out if m.role != "system"], self.estimator)
            layers = 2 if any(m.name == "history_summary_l2" for m in out) else 0
            sections = {
                "system": total_tokens(system, self.estimator), "history": after,
                "history_before": before, "history_after": after,
                "recent_groups": len(recent), "summary_layers": layers,
            }
            notes.append(f"history_compacted:{before}->{after}:recent={len(recent)}:layers={layers}")
            return ContextResult(
                out, total_tokens(out, self.estimator), refs, summarized,
                after > history_budget, notes, sections, {"history": history_budget},
            )

        pinned_tokens = total_tokens(pinned, self.estimator)
        required_tokens = pinned_tokens
        if latest_user is not None and latest_user is not first_user:
            required_tokens += message_tokens(latest_user, self.estimator)
        if required_tokens > history_budget:
            notes.append("pinned_over_budget")
            return result(messages, [], False)

        minimum_summary = message_tokens(
            Message("assistant", "[history summary L2]\n[offloaded: off-00000000]"), self.estimator,
        )
        while recent:
            reserve = minimum_summary if older or summaries else 0
            if pinned_tokens + total_tokens(_flatten(recent), self.estimator) + reserve <= history_budget:
                break
            removable = next(
                (index for index, group in enumerate(recent) if not any(m is latest_user for m in group)), None,
            )
            if removable is None:
                break
            older.append(recent.pop(removable))
        if len(recent) < initial_recent:
            notes.append(f"recent_window_reduced:{initial_recent}->{len(recent)}")

        available = history_budget - pinned_tokens - total_tokens(_flatten(recent), self.estimator)
        if not older and not summaries:
            return result(system + pinned + _flatten(recent), [], False)

        # 没有新的旧历史时直接复用，不再次层层摘要。
        if not older and len(summaries) == 1:
            prior = summaries[0]
            refs = _OFFLOAD_REF.findall(prior.content)
            if message_tokens(prior, self.estimator) <= available:
                notes.append("history_summary_reused")
                return result(system + pinned + summaries + _flatten(recent), refs, False)
            if refs:
                summary = self._fit_history_summary(self._summary_body(prior.content), refs, available, notes)
                notes.append("history_summary_reused")
                return result(system + pinned + [summary] + _flatten(recent), refs, False)

        # 先持久化原文，失败时保持当前消息，不把内存缓存当成持久化成功。
        if self.offload_dir is None:
            notes.append("offload_unavailable")
            return result(messages, [], False)
        prior_refs = list(dict.fromkeys(ref for m in summaries for ref in _OFFLOAD_REF.findall(m.content)))
        archive = [Message("assistant", f"[offloaded: {ref}]", name="history_archive_ref") for ref in prior_refs]
        archive.extend(m for m in summaries if not _OFFLOAD_REF.findall(m.content))
        archive.extend(_flatten(older))
        ref, ok = self._offload(archive)
        if not ok:
            notes.append(f"offload_failed:{ref}")
            return result(messages, [], False)
        notes.append(f"offloaded:history:{ref}")

        active_summarizer = summarizer or self.summarizer or summarize_messages

        def summarize(batch: list[Message]) -> str:
            nonlocal active_summarizer
            try:
                text = active_summarizer(batch)
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("empty summary")
            except Exception as exc:
                notes.extend([f"summary_failed:{type(exc).__name__}", "summary_fallback:deterministic"])
                active_summarizer = summarize_messages
                text = active_summarizer(batch)
            return self._shorten(self._summary_body(text), 1024)

        level_one: list[Message] = []
        chunk: list[list[Message]] = []
        for group in older:
            if chunk and (len(chunk) >= 8 or total_tokens(_flatten(chunk + [group]), self.estimator) > 8192):
                level_one.append(Message("assistant", summarize(_flatten(chunk)), name="history_summary_l1"))
                chunk = []
            chunk.append(group)
        if chunk:
            level_one.append(Message("assistant", summarize(_flatten(chunk)), name="history_summary_l1"))
        prior_summaries = [
            Message("assistant", self._summary_body(m.content), name=m.name) for m in summaries
        ]
        overall = summarize(prior_summaries + level_one)
        summary = self._fit_history_summary(overall, [ref], min(4096, available), notes)
        notes.append(f"history_summary_layers:2:chunks={len(level_one)}")
        return result(system + pinned + [summary] + _flatten(recent), [ref], True)

    @staticmethod
    def _summary_body(text: str) -> str:
        return _OFFLOAD_REF.sub("", text).replace("[history summary L2]", "").strip()

    def _shorten(self, text: str, token_cap: int) -> str:
        if self.estimator(text) <= token_cap:
            return text
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if self.estimator(self._summary_excerpt(text, middle)) <= token_cap:
                low = middle
            else:
                high = middle - 1
        return self._summary_excerpt(text, low) if low else ""

    @staticmethod
    def _summary_excerpt(text: str, length: int) -> str:
        if length >= len(text):
            return text
        if length <= 0:
            return ""
        head = length // 2
        tail = length - head
        return text[:head] + "\n...[summary truncated]...\n" + text[-tail:]

    def _fit_history_summary(
        self, text: str, refs: list[str], token_cap: int, notes: list[str],
    ) -> Message:
        header = "[history summary L2]\n"
        footer = "\n" + "\n".join(f"[offloaded: {ref}]" for ref in refs)
        content = header + text + footer
        if self.estimator(content) > token_cap:
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if self.estimator(header + self._summary_excerpt(text, middle) + footer) <= token_cap:
                    low = middle
                else:
                    high = middle - 1
            content = header + self._summary_excerpt(text, low) + footer
            notes.append("summary_truncated")
            if self.estimator(content) > token_cap:
                notes.append("summary_reference_over_budget")
        return Message("assistant", content, name="history_summary_l2")

    def _prepare(
        self, messages: list[Message]
    ) -> tuple[list[Message], Message | None, list[list[Message]]]:
        system_messages: list[Message] = []
        first_user: Message | None = None
        rest: list[Message] = []
        for m in messages:
            if m.role == "system":
                system_messages.append(m)
            elif self.pin_first_user and m.role == "user" and first_user is None:
                first_user = m
            else:
                rest.append(m)
        return system_messages, first_user, _group(rest)

    def _compact(
        self,
        pinned: list[Message],
        groups: list[list[Message]],
        budget: int,
    ) -> tuple[list[list[Message]], list[list[Message]], list[str], bool, list[str], bool]:
        if self.keep_recent_groups > 0:
            recent = groups[-self.keep_recent_groups :]
            candidates = groups[: -self.keep_recent_groups]
        else:
            recent, candidates = [], groups

        notes: list[str] = []
        offloaded: list[str] = []

        if self._flat_tokens(pinned, candidates, recent) <= budget:
            return candidates, recent, offloaded, False, notes, False

        if total_tokens(pinned, self.estimator) > budget:
            return candidates, recent, offloaded, False, ["pinned_over_budget"], True

        # 1) 先 offload 含 tool 的旧组
        for i, group in enumerate(candidates):
            if self._flat_tokens(pinned, candidates, recent) <= budget:
                break
            if any(m.role == "tool" for m in group):
                ref, ok = self._offload(group)
                candidates[i] = [Message(group[0].role, f"[offloaded: {ref}]")]
                offloaded.append(ref)
                notes.append(f"offloaded:tool:{ref}" + ("" if ok else ":offload_failed"))

        # 2) 再 offload 其他旧组
        for i, group in enumerate(candidates):
            if self._flat_tokens(pinned, candidates, recent) <= budget:
                break
            if len(group) == 1 and group[0].content.startswith("[offloaded:"):
                continue
            ref, ok = self._offload(group)
            candidates[i] = [Message(group[0].role, f"[offloaded: {ref}]")]
            offloaded.append(ref)
            notes.append(f"offloaded:group:{ref}" + ("" if ok else ":offload_failed"))

        # 3) 还不够就摘要；摘要失败/没有 summarizer 就 drop
        summarized = False
        summary_failed = False
        guard = 0
        while self._flat_tokens(pinned, candidates, recent) > budget and candidates:
            guard += 1
            if guard > 100:
                notes.append("compaction_guard")
                break
            if self.summarizer is not None and not summarized and not summary_failed:
                try:
                    text = self.summarizer([m for group in candidates for m in group])
                    candidates = [[Message("assistant", f"[summary] {text}")]]
                    summarized = True
                    notes.append("summarized")
                    continue
                except Exception as exc:  # 摘要失败不能把循环打崩
                    summary_failed = True
                    notes.append(f"summary_failed:{type(exc).__name__}")
            dropped = candidates.pop(0)
            notes.append(f"dropped:{dropped[0].role}:{len(dropped)}")

        over = self._flat_tokens(pinned, candidates, recent) > budget
        return candidates, recent, offloaded, summarized, notes, over

    def _build_flat(
        self, messages: list[Message], tool_tokens: int, memory_tokens: int
    ) -> ContextResult:
        system_messages, first_user, groups = self._prepare(messages)
        pinned = system_messages + ([first_user] if first_user else [])
        budget = max(0, self.max_tokens - self.reserve_output - tool_tokens - memory_tokens)

        candidates, recent, offloaded, summarized, notes, over = self._compact(
            pinned, groups, budget
        )
        if over and any(note == "pinned_over_budget" for note in notes):
            return ContextResult(messages, total_tokens(messages, self.estimator), over_budget=True, notes=notes)

        out = pinned + _flatten(candidates) + _flatten(recent)
        tokens = total_tokens(out, self.estimator)
        return ContextResult(out, tokens, offloaded, summarized, over, notes)

    def _build_sectioned(
        self, messages: list[Message], tool_tokens: int, memory_tokens: int
    ) -> ContextResult:
        assert self.policy is not None
        policy = self.policy
        system_messages, first_user, groups = self._prepare(messages)
        system_tokens = total_tokens(system_messages, self.estimator)
        caps = policy.caps()

        notes: list[str] = []
        over = False
        if system_tokens > caps["system"]:
            notes.append("system_over_cap")
            over = True
        if tool_tokens > caps["tools"]:
            notes.append("tools_over_cap")
            over = True
        if memory_tokens > caps["memory"]:
            notes.append("memory_over_cap")
            over = True

        history_budget = policy.history_budget(system_tokens, memory_tokens, tool_tokens)
        caps["history_nominal"] = caps["history"]
        caps["history"] = history_budget
        pinned_history = [first_user] if first_user else []
        candidates, recent, offloaded, summarized, compact_notes, compact_over = self._compact(
            pinned_history, groups, history_budget
        )
        notes.extend(compact_notes)
        over = over or compact_over

        out = system_messages + pinned_history + _flatten(candidates) + _flatten(recent)
        history_tokens = total_tokens(out, self.estimator) - system_tokens
        sections = {
            "system": system_tokens,
            "memory": memory_tokens,
            "tools": tool_tokens,
            "history": history_tokens,
        }
        tokens = sum(sections.values())
        if tokens > policy.prompt_cap:
            over = True
            notes.append("prompt_over_cap")
        return ContextResult(
            out,
            tokens,
            offloaded,
            summarized,
            over,
            notes,
            sections,
            caps,
        )

    def resolve(self, ref: str) -> list[Message]:
        return self._resolve(ref, set())

    def _resolve(self, ref: str, seen: set[str]) -> list[Message]:
        if ref in seen:
            raise ValueError(f"cyclic offload reference: {ref}")
        seen = seen | {ref}
        if ref in self._refs:
            messages = self._refs[ref]
        elif self.offload_dir is not None:
            path = self.offload_dir / f"{ref}.json"
            if not path.exists():
                raise KeyError(ref)
            raw = json.loads(path.read_text(encoding="utf-8"))
            messages = [self._message_from_dict(d) for d in raw]
        else:
            raise KeyError(ref)
        out: list[Message] = []
        for message in messages:
            if message.name == "history_archive_ref":
                for nested in _OFFLOAD_REF.findall(message.content):
                    out.extend(self._resolve(nested, seen))
            else:
                out.append(message)
        return out

    def _flat_tokens(
        self,
        pinned: list[Message],
        candidates: list[list[Message]],
        recent: list[list[Message]],
    ) -> int:
        flat = pinned + _flatten(candidates) + _flatten(recent)
        return total_tokens(flat, self.estimator)

    def _offload(self, group: list[Message]) -> tuple[str, bool]:
        payload = [asdict(m) for m in group]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        ref = "off-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        self._refs[ref] = group
        if self.offload_dir is None:
            return ref, True
        try:
            self.offload_dir.mkdir(parents=True, exist_ok=True)
            (self.offload_dir / f"{ref}.json").write_text(raw, encoding="utf-8")
            return ref, True
        except OSError:
            return ref, False

    @staticmethod
    def _message_from_dict(d: dict) -> Message:
        calls = [ToolCall(name=c["name"], args=c.get("args", {}), id=c.get("id")) for c in d.get("tool_calls", [])]
        return Message(
            role=d["role"],
            content=d.get("content", ""),
            tool_calls=calls,
            usage=d.get("usage"),
            finish_reason=d.get("finish_reason"),
            name=d.get("name"),
            tool_call_id=d.get("tool_call_id"),
        )
