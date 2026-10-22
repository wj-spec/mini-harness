from __future__ import annotations

from copy import deepcopy

from rich.cells import cell_len
from rich.text import Text

from app.context_snapshot_display import snapshot_for_display


SECTIONS = ("system", "memory", "tools", "history")
_COLORS = {"system": "#6DD9F3", "memory": "#D5A0FF", "tools": "#F2C56B", "history": "#83D19E"}
_LIGHT_COLORS = {"system": "#00718C", "memory": "#85389C", "tools": "#805500", "history": "#1D7041"}
_REMAINING = {True: "#627286", False: "#94A3B8"}
_MARKERS = {"system": "S", "memory": "M", "tools": "T", "history": "H"}
_PHASES = {"loaded": "基础装载", "request": "请求", "before_compaction": "压缩前", "after_compaction": "压缩后"}


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
        self.run_active = False

    def accept(self, event: dict) -> bool:
        if event.get("type") == "run_start":
            self.run_id = event.get("run_id")
            self.run_active = True
            self.snapshots.clear()
            return True
        if event.get("type") == "run_end" and event.get("run_id") == self.run_id:
            self.run_active = False
        if event.get("type") != "context_snapshot" or not _valid_snapshot(event):
            return False
        run_id = event.get("run_id")
        loaded = event["phase"] == "loaded"
        if loaded and self.run_active:
            return False
        if not loaded and self.run_id is not None and run_id != self.run_id:
            return False
        snapshot_id = event.get("snapshot_id")
        if not snapshot_id or any(item.get("snapshot_id") == snapshot_id for item in self.snapshots):
            return False
        if loaded:
            self.snapshots.clear()
        self.run_id = run_id
        self.snapshots.append(deepcopy(snapshot_for_display(event)))
        del self.snapshots[:-self.limit]
        return True


def _count(value: int, width: int | None = None) -> str:
    if width is not None and width < 60:
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if value >= 10_000:
            return f"{value / 1000:.0f}k"
        if value >= 1000:
            return f"{value / 1000:.1f}k"
    return f"{value:,}"


def context_summary(snapshot: dict, width: int | None = None, *, include_sections: bool = True) -> str:
    sections = snapshot["sections"]
    used = snapshot["total_tokens"]
    capacity = snapshot["input_capacity"]
    over = " · 超额" if snapshot.get("over_budget") else ""

    separator = " " if width is not None and width < 60 else " · "
    parts = separator.join(f"{_MARKERS[key]} {_count(sections[key], width)}" for key in SECTIONS)
    label = "ctx 基础装载估算" if snapshot.get("phase") == "loaded" else "ctx 估算"
    main = f"{label} {_count(used, width)}/{_count(capacity, width)}{over}"
    if not include_sections:
        return main
    combined = f"{main} · {parts}"
    return f"{main}\n{parts}" if width is not None and cell_len(combined) > width else combined


def _section_widths(snapshot: dict, width: int, composition: bool = False) -> list[int]:
    width = max(0, width)
    scale = max(1, snapshot["total_tokens"], 0 if composition else snapshot["input_capacity"])
    lengths = []
    filled = 0
    cumulative = 0
    for section in SECTIONS:
        cumulative += snapshot["sections"][section]
        end = min(width, (2 * cumulative * width + scale) // (2 * scale))
        lengths.append(max(0, end - filled))
        filled = end
    return lengths


def context_stack(snapshot: dict, width: int, composition: bool = False, *, dark: bool = True) -> Text:
    """窗口条按输入容量缩放，构成条只放大已用部分。"""
    colors = _COLORS if dark else _LIGHT_COLORS
    bar = Text()
    for section, length in zip(SECTIONS, _section_widths(snapshot, width, composition)):
        if length:
            bar.append("━" * length, style=colors[section])
    if len(bar) < width:
        bar.append("━" * (width - len(bar)), style=_REMAINING[dark])
    return bar


def context_legend(snapshot: dict, *, dark: bool = True, width: int | None = None) -> Text:
    colors = _COLORS if dark else _LIGHT_COLORS
    legend = Text()
    for section in SECTIONS:
        if legend:
            legend.append(" ")
        legend.append("━", style=colors[section])
        legend.append(f"{_MARKERS[section]} {_count(snapshot['sections'][section], width)}")
    return legend


def context_detail(snapshots: list[dict], index: int, width: int = 80, *, dark: bool = True) -> Text:
    text = Text()
    colors = _COLORS if dark else _LIGHT_COLORS
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
    loaded = snapshot["phase"] == "loaded"
    position = "尚未请求模型" if loaded else f"步骤 {snapshot['step']}"
    text.append(f"本轮快照 {index + 1}/{len(snapshots)} · {position} · {phase}\n", style="bold")
    text.append(f"估算输入 {used:,} / {capacity:,} tokens · {percent}\n")
    if snapshot.get("over_budget"):
        text.append("超额：输入或分区超过额度\n", style="bold red")
    bar_width = max(0, min(50, width - 6))
    text.append(context_stack(snapshot, bar_width, dark=dark))
    text.append("\n")
    if used > capacity:
        text.append("超出输入容量，彩线按已用总量缩放。\n", style="dim")
    if any(snapshot["sections"][section] > 0 and length == 0
           for section, length in zip(SECTIONS, _section_widths(snapshot, bar_width))):
        text.append("已用构成（放大已用部分）\n")
        text.append(context_stack(snapshot, bar_width, composition=True, dark=dark))
        text.append("\n")
    text.append(context_legend(snapshot, dark=dark))
    text.append("\nS system · M memory · T tools · H history\n", style="dim")
    text.append(f"剩余输入 {snapshot['remaining_tokens']:,} · 窗口 {snapshot['context_window']:,}\n")
    reserves = snapshot["reserves"]
    text.append(f"输出预留 {reserves['output']:,} · 安全余量 {reserves['scratch']:,}\n")
    text.append("预留不计入当前输入；累计消耗见 /cost。\n", style="dim")
    text.append(f"计数方法 {_display(snapshot['count_method'])}\n", style="dim")
    if not loaded:
        text.append(f"请求 {_display(snapshot['request_id'])} · 尝试 {snapshot['attempt']}\n", style="dim")
    text.append(f"原因 {_display(snapshot.get('reason', ''))}\n\n", style="dim")

    text.append("分区占用与额度\n", style="bold")
    for section in SECTIONS:
        tokens = snapshot["sections"][section]
        cap = snapshot["caps"].get(section, 0)
        nominal = snapshot["caps"].get(f"{section}_nominal", cap)
        change = snapshot.get("section_deltas", {}).get(section, 0)
        text.append("━ ", style=colors[section])
        text.append(f"{section}  {tokens:,} tokens  ({change:+,})\n")
        text.append(f"名义 {nominal:,} · 有效 {cap:,}")
        if tokens > cap:
            text.append(" · 超额", style="red")
        text.append("\n")
    text.append("\n占用趋势（相对于输入容量，超额时按总量缩放）\n", style="bold")
    for position, item in enumerate(snapshots):
        selected = ">" if position == index else " "
        label = _PHASES.get(item["phase"], item["phase"])
        delta = item.get("delta_tokens", 0)
        step = "尚未请求模型" if item["phase"] == "loaded" else f"步骤 {item['step']}"
        text.append(f"{selected} {position + 1}. {step} {label}\n")
        text.append(context_stack(item, max(0, min(30, width - 6)), dark=dark))
        text.append(f" {item['total_tokens']:,} ({delta:+,})\n")
    text.append("\n当前快照的内容块\n", style="bold")
    for block in snapshot.get("blocks", []):
        text.append("━ ", style=colors.get(block["section"], ""))
        text.append(f"{block['section']} / {_display(block['kind'])} · {block['tokens']:,} tokens\n")
        text.append(f"{_display(block['label'])}\n{_display(block['source'])}\n")
        text.append(f"{block['chars']:,} 字符 · id {_display(block['id'])}\n", style="dim")
    if not snapshot.get("blocks"):
        text.append("无内容块。\n", style="dim")
    return text
