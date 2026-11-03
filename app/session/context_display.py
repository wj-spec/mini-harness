from __future__ import annotations

import hashlib


def snapshot_for_display(snapshot: dict, max_blocks: int = 256) -> dict:
    """保留主要块，其余按分区合并；完整快照仍由 trace 保存。"""
    blocks = snapshot["blocks"]
    if len(blocks) <= max_blocks:
        return snapshot
    if max_blocks < 4:
        raise ValueError("display needs room for four partitions")
    ranked = sorted(range(len(blocks)), key=lambda index: (-blocks[index]["tokens"], index))
    kept = set(ranked[:max_blocks - 4])
    visible = [block for index, block in enumerate(blocks) if index in kept]
    for section in ("system", "memory", "tools", "history"):
        omitted = [block for index, block in enumerate(blocks)
                   if index not in kept and block["section"] == section]
        if not omitted:
            continue
        fingerprint = hashlib.sha256("\n".join(block["id"] for block in omitted).encode()).hexdigest()
        visible.append({
            "id": f"aggregate:{section}:{fingerprint[:32]}",
            "section": section, "kind": "aggregate",
            "label": f"其余 {len(omitted):,} 块（完整明细见 trace.jsonl）",
            "source": "trace.jsonl",
            "tokens": sum(block["tokens"] for block in omitted),
            "chars": sum(block["chars"] for block in omitted),
            "content_hash": fingerprint,
        })
    return {**snapshot, "blocks": visible}
