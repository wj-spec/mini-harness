"""从 harbor-datasets 下载 SWE-bench Verified task 到本地目录。

用法:
    python3 scripts/fetch_swebench_task.py astropy__astropy-12907 django__django-11099
    python3 scripts/fetch_swebench_task.py --dest local-bench/batch1 <instance> ...

每个 task 需要 6 个文件:
    task.toml
    instruction.md
    environment/Dockerfile
    tests/test.sh
    tests/config.json
    solution/solve.sh

只下载文件，不做任何评分口径修改；需要本地加速时再跑
scripts/prepare_bench_image.py。
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAW = (
    "https://raw.githubusercontent.com/harbor-framework/harbor-datasets/"
    "main/datasets/swebench-verified/{instance}/{path}"
)
JSDELIVR = (
    "https://cdn.jsdelivr.net/gh/harbor-framework/harbor-datasets@main/"
    "datasets/swebench-verified/{instance}/{path}"
)
FILES = (
    "task.toml",
    "instruction.md",
    "environment/Dockerfile",
    "tests/test.sh",
    "tests/config.json",
    "solution/solve.sh",
)


def _download(url: str, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "mini-harness-bench-fetch/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _fetch_file(instance: str, rel_path: str, *, retries: int = 3) -> bytes:
    urls = (
        RAW.format(instance=instance, path=rel_path),
        JSDELIVR.format(instance=instance, path=rel_path),
    )
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        for url in urls:
            try:
                return _download(url)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
        if attempt < retries:
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"failed to download {instance}/{rel_path}: {last_error}")


def fetch_task(
    instance: str,
    dest_root: str | Path,
    *,
    force: bool = False,
) -> Path:
    """下载一个 task；返回 task 目录。"""
    task_dir = Path(dest_root) / instance
    missing = [rel for rel in FILES if not (task_dir / rel).is_file()]
    if not force and not missing:
        return task_dir

    for rel_path in FILES:
        target = task_dir / rel_path
        if not force and target.is_file() and target.stat().st_size > 0:
            continue
        content = _fetch_file(instance, rel_path)
        if not content:
            raise RuntimeError(f"empty file: {instance}/{rel_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return task_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instances", nargs="+", help="SWE-bench instance id，可多个")
    parser.add_argument(
        "--dest", default="local-bench/batch1", help="本地数据集目录（默认 local-bench/batch1）",
    )
    parser.add_argument("--force", action="store_true", help="覆盖已有文件")
    args = parser.parse_args(argv)

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    failures = 0
    for instance in args.instances:
        try:
            task_dir = fetch_task(instance, dest, force=args.force)
        except Exception as exc:  # noqa: BLE001 - CLI 汇总错误
            failures += 1
            print(f"[FAIL] {instance}: {exc}", file=sys.stderr)
            continue
        size = sum(p.stat().st_size for p in task_dir.rglob("*") if p.is_file())
        print(f"[ok] {instance} -> {task_dir} ({size / 1024:.1f} KiB)")

    if failures:
        print(f"{failures} task(s) failed", file=sys.stderr)
        return 1
    print(f"\n下载完成：{len(args.instances)} 个 task 在 {dest}")
    print("下一步：")
    print(f"  for task in {dest}/*/; do python3 scripts/prepare_bench_image.py \"$task\"; done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
