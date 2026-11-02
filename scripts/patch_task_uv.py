"""把任务 Dockerfile 里的 uv 在线安装替换成本地镜像 COPY。

SWE-bench 任务 Dockerfile 常见：

    RUN curl -LsSf https://astral.sh/uv/0.7.13/install.sh | sh

Harbor 的 Docker build 不会继承宿主 HTTP_PROXY，网络差时这一层会失败。
先在 WSL 构建本地 uv 镜像：

    curl -L -x http://127.0.0.1:17891 -o /tmp/uv.tar.gz \\
      https://github.com/astral-sh/uv/releases/download/0.7.13/uv-x86_64-unknown-linux-gnu.tar.gz
    tar -xzf /tmp/uv.tar.gz -C /tmp
    mkdir -p /tmp/uv-image
    cp /tmp/uv-x86_64-unknown-linux-gnu/uv /tmp/uv-image/uv
    printf 'FROM scratch\\nCOPY uv /uv\\n' > /tmp/uv-image/Dockerfile
    docker build -t local/uv:0.7.13 /tmp/uv-image

然后：

    python3 scripts/patch_task_uv.py local-bench/batch20

用法:
    python3 scripts/patch_task_uv.py TASK_DIR_OR_ROOT [更多路径...]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

UV_IMAGE = "local/uv:0.7.13"
REPLACEMENT = f"COPY --from={UV_IMAGE} /uv /usr/local/bin/uv"
ASTTAL_RUN = re.compile(r"^\s*RUN\s+.*astral\.sh/uv.*install\.sh.*\|\s*sh\s*$")


def patch_dockerfile(path: Path) -> str:
    """返回 changed / skipped / warning。"""
    text = path.read_text(encoding="utf-8")
    if UV_IMAGE in text:
        return "skipped"
    lines = text.splitlines()
    changed = False
    for index, line in enumerate(lines):
        if ASTTAL_RUN.match(line):
            lines[index] = REPLACEMENT
            changed = True
    if not changed:
        if "astral.sh/uv" in text:
            return "warning"
        return "skipped"
    path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")
    return "changed"


def iter_dockerfiles(root: Path) -> list[Path]:
    if root.name == "Dockerfile":
        return [root]
    direct = root / "environment" / "Dockerfile"
    if direct.is_file():
        return [direct]
    return sorted(root.glob("*/environment/Dockerfile"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="task 目录或包含 task 的目录")
    args = parser.parse_args(argv)

    totals = {"changed": 0, "skipped": 0, "warning": 0}
    for raw in args.paths:
        root = Path(raw)
        if not root.exists():
            print(f"[missing] {root}", file=sys.stderr)
            totals["warning"] += 1
            continue
        for dockerfile in iter_dockerfiles(root):
            status = patch_dockerfile(dockerfile)
            totals[status] += 1
            print(f"[{status}] {dockerfile}")

    print(
        f"\nchanged={totals['changed']} skipped={totals['skipped']} "
        f"warning={totals['warning']}"
    )
    if totals["warning"]:
        print("有 Dockerfile 含 astral.sh/uv 但不是预期单行 RUN，请手动检查。", file=sys.stderr)
    return 0 if totals["warning"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
