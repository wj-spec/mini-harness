from __future__ import annotations

from copy import deepcopy

from rich.cells import cell_len
from rich.text import Text

from app.context_snapshot_display import snapshot_for_display


SECTIONS = ("system", "memory", "tools", "history")
_COLORS = {"system": "cyan", "memory": "magenta", "tools": "yellow", "history": "green"}
_MARKERS = {"system": "S", "memory": "M", "tools": "T", "history": "H"}
_PHASES = {"request": "请求", "before_compaction": "压缩前", "after_compaction": "压缩后"}


def _display(value: object) -> str:
    return str(value).replace("\x1b", "␛")


def _is_int(value, *, signed: bool = False) -> bool:
    return type(value) is int and (signed or value >= 0)


def _valid_snapshot(event: dict) -> bool:
    if type(event.get("schema_version")) is not int or event["schema_version"] != 1:
        return False
    for key in ("snapshot_id", "request_id", "run_id", "count_method"):
        if not isinstance(event.get(key), str) or not event[key]:
            return False
    if not isinstance(event.get("phase"), str) or event["phase"] not in _PHASES:
        return False
    if type(event.get("estimated")) is not bool:
        return False
    if type(event.get("over_budget")) is not bool:
        return False
    for key in ("step", "attempt", "context_window", "input_capacity", "total_tokens", "remaining_tokens"):
        if not _is_int(event.get(key)):
            return False
    if not _is_int(event.get("delta_tokens"), signed=True):
        return False
    sections = event.get("sections")
    caps = event.get("caps")
    reserves = event.get("reserves")
    deltas = event.get("section_deltas")
    if not isinstance(sections, dict) or set(sections) != set(SECTIONS):
        return False
    if not all(_is_int(value) for value in sections.values()):
        return False
    if not isinstance(caps, dict) or not set(SECTIONS).issubset(caps):
        return False
    if not all(_is_int(value) for value in caps.values()):
        return False
    if not isinstance(reserves, dict) or set(reserves) != {"output", "scratch"}:
        return False
    if not all(_is_int(value) for value in reserves.values()):
        return False
    if not isinstance(deltas, dict) or not set(deltas).issubset(SECTIONS):
        return False
    if not all(_is_int(value, signed=True) for value in deltas.values()):
        return False
    blocks = event.get("blocks")
    if not isinstance(blocks, list):
        return False
    block_totals = dict.fromkeys(SECTIONS, 0)
    for block in blocks:
        if not isinstance(block, dict) or block.get("section") not in SECTIONS:
            return False
        if not all(isinstance(block.get(key), str) for key in ("id", "kind", "label", "source", "content_hash")):
            return False
        if not all(_is_int(block.get(key)) for key in ("tokens", "chars")):
            return False
        block_totals[block["section"]] += block["tokens"]
    return sum(sections.values()) == event["total_tokens"] and block_totals == sections


class ContextHistory:
    """只保留当前运行的有界快照，完整记录由 session 保存。"""

    def __init__(self, limit: int = 100):
        self.limit = limit
        self.run_id: str | None = None
        self.snapshots: list[dict] = []

    def accept(self, event: dict) -> bool:
        if event.get("type") == "run_start":
            self.run_id = event.get("run_id")
            self.snapshots.clear()
            return True
        if event.get("type") != "context_snapshot" or not _valid_snapshot(event):
            return False
        run_id = event.get("run_id")
        if self.run_id is not None and run_id != self.run_id:
            return False
        snapshot_id = event.get("snapshot_id")
        if not snapshot_id or any(item.get("snapshot_id") == snapshot_id for item in self.snapshots):
            return False
        self.run_id = run_id
        self.snapshots.append(deepcopy(snapshot_for_display(event)))
        del self.snapshots[:-self.limit]
        return True


def context_summary(snapshot: dict, width: int | None = None) -> str:
    sections = snapshot["sections"]
    used = snapshot["total_tokens"]
    capacity = snapshot["input_capacity"]
    over = " · 超额" if snapshot.get("over_budget") else ""

    def count(value: int) -> str:
        if width is not None and width < 60:
            if value >= 1_000_000:
                return f"{value / 1_000_000:.1f}M"
            if value >= 10_000:
                return f"{value / 1000:.0f}k"
            if value >= 1000:
                return f"{value / 1000:.1f}k"
        return f"{value:,}"

    separator = " " if width is not None and width < 60 else " · "
    parts = separator.join(f"{_MARKERS[key]} {count(sections[key])}" for key in SECTIONS)
    main = f"ctx 估算 {count(used)}/{count(capacity)}{over}"
    combined = f"{main} · {parts}"
    return f"{main}\n{parts}" if width is not None and cell_len(combined) > width else combined


def _stack(snapshot: dict, width: int) -> Text:
    capacity = snapshot["input_capacity"]
    used = snapshot["total_tokens"]
    scale = max(capacity, used, 1)
    bar = Text()
    filled = 0
    cumulative = 0
    for section in SECTIONS:
        cumulative += snapshot["sections"][section]
        end = min(width, round(cumulative * width / scale))
        bar.append(_MARKERS[section] * max(0, end - filled), style=_COLORS[section])
        filled = end
    bar.append("·" * max(0, width - filled), style="dim")
    return bar


def context_detail(snapshots: list[dict], index: int, width: int = 80) -> Text:
    text = Text()
    if not snapshots:
        text.append("暂无本轮上下文快照。\n", style="bold")
        text.append("运行后按 Ctrl+O 或输入 /context 查看估算占用与回放。")
        return text
    index = max(0, min(index, len(snapshots) - 1))
    snapshot = snapshots[index]
    phase = _PHASES.get(snapshot["phase"], snapshot["phase"])
    used = snapshot["total_tokens"]
    capacity = snapshot["input_capacity"]
    percent = f"{used / capacity:.1%}" if capacity > 0 else "容量为 0"
    text.append(f"本轮快照 {index + 1}/{len(snapshots)} · 步骤 {snapshot['step']} · {phase}\n", style="bold")
    text.append(f"估算输入 {used:,} / {capacity:,} tokens · {percent}\n")
    if snapshot.get("over_budget"):
        text.append("超额：输入或分区超过额度\n", style="bold red")
    text.append(_stack(snapshot, max(8, min(50, width - 6))))
    text.append("\nS system · M memory · T tools · H history\n", style="dim")
    text.append(f"剩余输入 {snapshot['remaining_tokens']:,} · 窗口 {snapshot['context_window']:,}\n")
    reserves = snapshot["reserves"]
    text.append(f"输出预留 {reserves['output']:,} · 安全余量 {reserves['scratch']:,}\n")
    text.append("预留不计入当前输入；累计消耗见 /cost。\n", style="dim")
    text.append(f"计数方法 {_display(snapshot['count_method'])}\n", style="dim")
    text.append(f"请求 {_display(snapshot['request_id'])} · 尝试 {snapshot['attempt']}\n", style="dim")
    text.append(f"原因 {_display(snapshot.get('reason', ''))}\n\n", style="dim")

    text.append("分区占用与额度\n", style="bold")
    for section in SECTIONS:
        tokens = snapshot["sections"][section]
        cap = snapshot["caps"].get(section, 0)
        nominal = snapshot["caps"].get(f"{section}_nominal", cap)
        change = snapshot.get("section_deltas", {}).get(section, 0)
        text.append(f"{section}  {tokens:,} tokens  ({change:+,})\n", style=_COLORS[section])
        text.append(f"名义 {nominal:,} · 有效 {cap:,}")
        if tokens > cap:
            text.append(" · 超额", style="red")
        text.append("\n")
    text.append("\n步骤趋势（各条相对于该快照输入容量）\n", style="bold")
    for position, item in enumerate(snapshots):
        selected = ">" if position == index else " "
        label = _PHASES.get(item["phase"], item["phase"])
        delta = item.get("delta_tokens", 0)
        text.append(f"{selected} {position + 1}. 步骤 {item['step']} {label}\n")
        text.append(_stack(item, max(8, min(30, width - 6))))
        text.append(f" {item['total_tokens']:,} ({delta:+,})\n")
    text.append("\n当前快照的内容块\n", style="bold")
    for block in snapshot.get("blocks", []):
        text.append(f"{block['section']} / {_display(block['kind'])} · {block['tokens']:,} tokens\n",
                    style=_COLORS.get(block["section"], ""))
        text.append(f"{_display(block['label'])}\n{_display(block['source'])}\n")
        text.append(f"{block['chars']:,} 字符 · id {_display(block['id'])}\n", style="dim")
    if not snapshot.get("blocks"):
        text.append("无内容块。\n", style="dim")
    return text
