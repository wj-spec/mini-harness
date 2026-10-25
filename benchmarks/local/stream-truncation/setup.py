"""把工具调用完整性判定退回宽松版本，作为 benchmark 的初始 bug。"""
from pathlib import Path

path = Path("app/providers/openai_compat.py")
source = path.read_text()
old = '''        stream_complete = done_seen or finish_reason is not None
        complete: list[ToolCall] = []
        incomplete: list[ToolCall] = []
        for index in sorted(calls):
            slot = calls[index]
            raw = slot["arguments"]
            name = slot["name"].strip()
            args: dict | None = None
            if raw:
                try:
                    parsed_args = json.loads(raw)
                except json.JSONDecodeError:
                    parsed_args = None
                if isinstance(parsed_args, dict):
                    args = parsed_args
            elif finish_reason == "tool_calls":
                args = {}
            call_id = slot["id"] or f"call_{self._call_seq}_{index}"
            # 不完整接收的工具调用绝不进入可执行列表。
            if name and args is not None and stream_complete:
                complete.append(ToolCall(name=name, args=args, id=call_id))
            else:
                incomplete.append(ToolCall(name=name or "unknown", args={"_raw": raw}, id=call_id))
        if complete or incomplete:
            yield ToolCallsReady(complete, incomplete=incomplete)
'''
new = '''        complete: list[ToolCall] = []
        incomplete: list[ToolCall] = []
        for index in sorted(calls):
            slot = calls[index]
            raw = slot["arguments"]
            try:
                args = json.loads(raw or "{}")
            except json.JSONDecodeError:
                args = {"_raw": raw}
            complete.append(ToolCall(
                name=slot["name"], args=args,
                id=slot["id"] or f"call_{self._call_seq}_{index}",
            ))
        if complete or incomplete:
            yield ToolCallsReady(complete, incomplete=incomplete)
'''
assert source.count(old) == 1, "provider completeness block changed; update benchmarks/local/stream-truncation/setup.py"
source = source.replace(old, new, 1)
old2 = '''            finish_reason=finish_reason if stream_complete else "truncated", usage=usage,'''
new2 = '''            finish_reason=finish_reason, usage=usage,'''
assert source.count(old2) == 1, "StreamDone line changed; update benchmarks/local/stream-truncation/setup.py"
path.write_text(source.replace(old2, new2, 1))
print("seeded: truncated tool calls become executable again")
