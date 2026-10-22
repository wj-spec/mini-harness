from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict

from .budget import BudgetPolicy
from .context import estimate_tokens
from .models import Message, ToolCall

_OFFLOAD_REF = re.compile(r"\[offloaded: ([^\]]+)\]")
_SUMMARIES = {"history_summary_l1", "history_summary_l2"}


def _bounded(value: str) -> str:
    return value[:256]


def _token_deltas(parts: list[str], envelope: str = "") -> list[int]:
    """按前缀增量分配取整和列表外壳开销。"""
    chars = len(envelope)
    cjk = sum("\u4e00" <= char <= "\u9fff" for char in envelope)
    previous = 0
    result = []
    for part in parts:
        chars += len(part)
        cjk += sum("\u4e00" <= char <= "\u9fff" for char in part)
        current = max(1, cjk + (chars - cjk) // 4)
        result.append(current - previous)
        previous = current
    return result


def _call_source(call: ToolCall) -> str:
    for key in ("path", "ref"):
        value = call.args.get(key)
        if isinstance(value, str) and value:
            return value
    return call.id or call.name


def build_context_snapshot(
    messages: list[Message],
    tool_specs: list[dict],
    *,
    policy: BudgetPolicy | None = None,
    max_tokens: int = 800,
    reserve_output: int = 200,
    system_parts: list[dict] | None = None,
) -> dict:
    """观察最终请求的估算占用；只保存计数、身份和来源。"""
    sections = {"system": 0, "memory": 0, "tools": 0, "history": 0}
    blocks: list[dict] = []
    occurrences: dict[str, int] = defaultdict(int)

    def add(
        section: str, kind: str, label: str, source: str,
        content: str, tokens: int | None = None,
    ) -> None:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        identity = "\0".join((section, kind, source, content_hash))
        base = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        occurrences[base] += 1
        count = estimate_tokens(content) if tokens is None else tokens
        blocks.append({
            "id": f"{base}:{occurrences[base]}",
            "section": section,
            "kind": _bounded(kind),
            "label": _bounded(label),
            "source": _bounded(source),
            "tokens": count,
            "chars": len(content),
            "content_hash": content_hash,
        })
        sections[section] += count

    first_system = next((message for message in messages if message.role == "system"), None)
    matching_parts = bool(system_parts) and all(
        isinstance(part, dict)
        and all(isinstance(part.get(key), str) for key in ("kind", "label", "source", "content"))
        for part in system_parts
    )
    if matching_parts:
        matching_parts = first_system is not None and "".join(
            part["content"] for part in system_parts
        ) == first_system.content
    call_sources = {
        call.id: _call_source(call)
        for message in messages for call in message.tool_calls if call.id
    }
    used_parts = False
    for message in messages:
        section = "system" if message.role == "system" else "history"
        if message is first_system and matching_parts and not used_parts:
            contents = [part["content"] for part in system_parts]
            for part, tokens in zip(system_parts, _token_deltas(contents)):
                add(section, part["kind"], part["label"], part["source"], part["content"], tokens)
            used_parts = True
        else:
            kind = message.role
            source = message.name or message.role
            if message.role == "system" and message.name == "task_state":
                kind = "task_state"
            elif message.role != "system":
                refs = _OFFLOAD_REF.findall(message.content)
                if message.name in _SUMMARIES or message.content.startswith(("[summary]", "[history summary")):
                    kind = "history_summary"
                    source = refs[0] if refs else message.name or kind
                elif message.content.lstrip().startswith("[offloaded:"):
                    kind = "offload_reference"
                    source = refs[0] if refs else message.name or kind
                elif message.role == "tool":
                    kind = "tool_result"
                    source = call_sources.get(message.tool_call_id) or message.tool_call_id or message.name or "tool"
            add(section, kind, kind, source, message.content)
        if message.reasoning_content:
            add(section, "reasoning", "reasoning", message.name or message.role,
                message.reasoning_content)
        for call in message.tool_calls:
            serialized = call.name + json.dumps(call.args, ensure_ascii=False, sort_keys=True)
            add(section, "tool_call", call.name, _call_source(call), serialized)

    if tool_specs:
        serialized = [json.dumps(spec, ensure_ascii=False) for spec in tool_specs]
        pieces = [text if index == 0 else ", " + text for index, text in enumerate(serialized)]
        for index, (spec, content, tokens) in enumerate(zip(tool_specs, serialized, _token_deltas(pieces, "[]"))):
            function = spec.get("function")
            name = spec.get("name") or (function.get("name") if isinstance(function, dict) else None)
            source = name if isinstance(name, str) else f"tool_{index + 1}"
            add("tools", "tool_definition", source, source, content, tokens)
    else:
        add("tools", "tools_envelope", "tools", "tools", "[]")

    if policy is not None:
        context_window = policy.context_window
        reserves = {"output": policy.output_cap, "scratch": policy.scratch_cap}
        caps = policy.caps()
        caps["history_nominal"] = caps["history"]
        caps["history"] = policy.history_budget(sections["system"], 0, sections["tools"])
    else:
        context_window = max_tokens
        reserves = {"output": reserve_output, "scratch": 0}
        prompt_cap = max(0, context_window - reserve_output)
        caps = {
            "system": prompt_cap, "memory": prompt_cap, "tools": prompt_cap,
            "history_nominal": prompt_cap,
            "history": max(0, prompt_cap - sections["system"] - sections["tools"]),
            "output": reserve_output, "scratch": 0, "unallocated": 0, "prompt": prompt_cap,
        }
    capacity = max(0, context_window - reserves["output"] - reserves["scratch"])
    total = sum(sections.values())
    return {
        "schema_version": 1,
        "estimated": True,
        "count_method": "heuristic_cjk_v1",
        "context_window": context_window,
        "input_capacity": capacity,
        "total_tokens": total,
        "sections": sections,
        "caps": caps,
        "reserves": reserves,
        "remaining_tokens": max(0, capacity - total),
        "over_budget": total > capacity,
        "blocks": blocks,
    }
