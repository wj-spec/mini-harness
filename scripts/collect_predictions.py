"""从 Harbor trial 收集 model.patch，生成官方 SWE-bench 评测用的 predictions.jsonl。

前置条件：adapter 已配置在 Agent 跑完后写 `agent/model.patch`。

用法:
    python3 scripts/collect_predictions.py jobs/agent-... -o predictions.jsonl
    python3 scripts/collect_predictions.py jobs/agent-... \
      --model-name-or-path vibe-mini-harness --include-empty
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


_TEST_DIR_NAMES = {"tests", "test", "testing", "__tests__"}


def _is_test_path(path: str) -> bool:
    parts = Path(path.replace("\\", "/")).parts
    if not parts:
        return False
    name = parts[-1]
    if name.startswith("test_") or name.endswith("_test.py") or name in {
        "conftest.py", "tests.py", "test.py",
    }:
        return True
    return any(part in _TEST_DIR_NAMES for part in parts[:-1])


def strip_test_file_hunks(patch: str) -> tuple[str, int]:
    """移除 diff 中的测试文件块；返回 (清理后 patch, 移除块数)。"""
    lines = patch.splitlines(keepends=True)
    kept: list[str] = []
    block: list[str] = []
    block_is_test = False
    removed = 0

    def flush() -> None:
        nonlocal block, block_is_test, removed
        if not block:
            return
        if block_is_test:
            removed += 1
        else:
            kept.extend(block)
        block = []
        block_is_test = False

    for line in lines:
        if line.startswith("diff --git "):
            flush()
        block.append(line)
        if line.startswith("diff --git "):
            parts = line.split()
            paths = [part[2:] for part in parts[2:4] if part[:2] in {"a/", "b/"}]
            block_is_test = any(_is_test_path(path) for path in paths)
    flush()
    return "".join(kept), removed


def collect(job_dir: str | Path, *, include_empty: bool = False) -> list[dict]:
    job_dir = Path(job_dir)
    rows: list[dict] = []
    for trial_dir in sorted(path for path in job_dir.iterdir() if path.is_dir()):
        result_path = trial_dir / "result.json"
        patch_path = trial_dir / "agent" / "model.patch"
        if not result_path.is_file() or not patch_path.is_file():
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        instance = result.get("task_name")
        if not instance:
            continue
        patch = patch_path.read_text(encoding="utf-8", errors="replace")
        if not patch.strip() and not include_empty:
            print(f"[skip empty] {instance}", file=sys.stderr)
            continue
        rows.append({"instance_id": instance, "model_patch": patch})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_dir", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("predictions.jsonl"))
    parser.add_argument("--model-name-or-path", default="vibe-mini-harness")
    parser.add_argument("--include-empty", action="store_true")
    parser.add_argument("--keep-test-files", action="store_true",
                        help="保留模型对测试文件的修改（默认过滤，官方评测只接受源码 patch）")
    args = parser.parse_args(argv)

    if not args.job_dir.is_dir():
        print(f"not a directory: {args.job_dir}", file=sys.stderr)
        return 2
    rows = collect(args.job_dir, include_empty=args.include_empty)
    stripped = 0
    if not args.keep_test_files:
        for row in rows:
            row["model_patch"], count = strip_test_file_hunks(row["model_patch"])
            stripped += count
        print(f"stripped {stripped} test-file diff block(s)", file=sys.stderr)
    if not rows:
        print("no model.patch found (did you rerun with the updated adapter?)", file=sys.stderr)
        return 2

    with args.output.open("w", encoding="utf-8") as handle:
        for row in rows:
            row["model_name_or_path"] = args.model_name_or_path
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} predictions to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
