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
    args = parser.parse_args(argv)

    if not args.job_dir.is_dir():
        print(f"not a directory: {args.job_dir}", file=sys.stderr)
        return 2
    rows = collect(args.job_dir, include_empty=args.include_empty)
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
