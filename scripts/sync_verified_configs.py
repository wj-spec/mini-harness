"""用官方 SWE-bench Verified 数据集校验/覆盖 Harbor task config。

严格官方口径要求 task 的 FAIL_TO_PASS / PASS_TO_PASS / test_patch /
base_commit 等字段与 princeton-nlp/SWE-bench_Verified 完全一致。

用法:
    # 只校验（不写文件）
    python3 scripts/sync_verified_configs.py \
      --dataset /tmp/swebench_verified.parquet \
      --tasks local-bench/batch20

    # 用官方字段覆盖本地 task config
    python3 scripts/sync_verified_configs.py \
      --dataset /tmp/swebench_verified.parquet \
      --tasks local-bench/batch20 --write

官方 parquet 下载（HF 镜像）:
    curl -L -o /tmp/swebench_verified.parquet \\
      https://hf-mirror.com/datasets/princeton-nlp/SWE-bench_Verified/resolve/main/data/test-00000-of-00001.parquet
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

OFFICIAL_FIELDS = (
    "repo",
    "instance_id",
    "base_commit",
    "patch",
    "test_patch",
    "problem_statement",
    "hints_text",
    "created_at",
    "version",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "environment_setup_commit",
    "difficulty",
)
LIST_FIELDS = ("FAIL_TO_PASS", "PASS_TO_PASS")


def load_official(path: Path) -> dict[str, dict]:
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    rows = table.to_pylist()
    return {row["instance_id"]: row for row in rows}


def _as_list(value):
    return json.loads(value) if isinstance(value, str) else list(value or [])


def _store_like(value: list, old_value):
    return json.dumps(value) if isinstance(old_value, str) else value


def sync_task(task_dir: Path, official: dict, *, write: bool) -> dict:
    cfg_path = task_dir / "tests" / "config.json"
    if not cfg_path.is_file():
        return {"instance": task_dir.name, "status": "missing-config"}

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    instance = cfg.get("instance_id") or task_dir.name
    off = official.get(instance)
    if off is None:
        return {"instance": instance, "status": "not-in-official"}

    diffs: list[str] = []
    for key in OFFICIAL_FIELDS:
        old = cfg.get(key)
        new = off.get(key)
        if key in LIST_FIELDS:
            old_list = _as_list(old)
            new_list = _as_list(new)
            if old_list != new_list:
                diffs.append(f"{key}:{len(old_list)}->{len(new_list)}")
                cfg[key] = _store_like(new_list, old)
        elif old != new:
            diffs.append(key)
            cfg[key] = new

    if diffs and write:
        cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "instance": instance,
        "status": "updated" if diffs and write else ("diff" if diffs else "ok"),
        "diffs": diffs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path, help="官方 Verified parquet")
    parser.add_argument("--tasks", required=True, type=Path, help="本地 task 根目录")
    parser.add_argument("--write", action="store_true", help="用官方字段覆盖 config")
    args = parser.parse_args(argv)

    official = load_official(args.dataset)
    if args.tasks.is_dir() and (args.tasks / "tests" / "config.json").is_file():
        task_dirs = [args.tasks]
    else:
        task_dirs = sorted(
            path for path in args.tasks.iterdir() if (path / "tests" / "config.json").is_file()
        )

    counts = {"ok": 0, "diff": 0, "updated": 0, "missing-config": 0, "not-in-official": 0}
    for task_dir in task_dirs:
        result = sync_task(task_dir, official, write=args.write)
        counts[result["status"]] += 1
        if result["status"] in {"diff", "updated", "missing-config", "not-in-official"}:
            print(f"[{result['status']}] {result['instance']} {result.get('diffs', '')}")
    print("\nsummary:", json.dumps(counts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
