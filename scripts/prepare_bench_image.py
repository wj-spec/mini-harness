"""为本地 Harbor 任务镜像预装 app 依赖与下载缓存。

用法:
    python scripts/prepare_bench_image.py [TASK_DIR] [--pypi-index URL] [--dry-run]

TASK_DIR 形如 local-bench/smoke/astropy__astropy-7606；省略时自动找
local-bench/smoke 下第一个任务。

脚本做两件事（幂等）：
1. 在 environment/Dockerfile 末尾追加缓存层：/opt/vibe-venv（app 依赖）、
   uv/pip cache 预热、testbed 测试依赖预热；
2. 在 task.toml 的 [verifier] 段加 UV_HTTP_TIMEOUT 与 PyPI 镜像 env。

第一次 harbor run 会用 --force-build 重建镜像；之后 trial 只做 symlink，
不再重复下载依赖。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MARKER = "# vibe-bench-cache-layer"
DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
APP_REQUIREMENTS = ("fastmcp==4.1.1", "ddgs==9.16.0", "httpx==0.28.1")
PARSER_REQUIREMENTS = ("swebench==4.0.3", "datasets==2.16.1", "fastcore<1.11")


def dockerfile_layer(pypi_index: str = DEFAULT_INDEX) -> str:
    """返回追加到任务 Dockerfile 末尾的缓存层。"""
    parser_with = " ".join(f'--with "{item}"' for item in PARSER_REQUIREMENTS)
    app_requirements = " ".join(APP_REQUIREMENTS)
    return f"""
{MARKER}
ENV UV_HTTP_TIMEOUT=600 \\
    UV_INDEX_URL={pypi_index} \\
    UV_DEFAULT_INDEX={pypi_index} \\
    PIP_INDEX_URL={pypi_index} \\
    PIP_DEFAULT_TIMEOUT=600 \\
    UV_CACHE_DIR=/opt/uv-cache \\
    PIP_CACHE_DIR=/opt/pip-cache \\
    UV_PYTHON=3.11 \\
    PATH="/root/.local/bin:$PATH"

# 预装 app venv；超时/失败不阻塞镜像构建，adapter 会回退到运行时安装
RUN mkdir -p /opt/uv-cache /opt/pip-cache \\
 && python -m venv /opt/vibe-venv \\
 && timeout 600 /opt/vibe-venv/bin/pip install --disable-pip-version-check \\
      {app_requirements} || true

# 预热 verifier parser 的 uv 依赖（与 tests/test.sh 内联元数据一致）
RUN timeout 600 uv run --no-project --python 3.11 \\
      {parser_with} \\
      python -c 'import swebench, datasets, fastcore' || true

# 预热 testbed 的测试依赖；失败不阻塞镜像构建，verifier 会再试
RUN timeout 900 bash -lc 'source /opt/miniconda3/bin/activate \\
      && conda activate testbed \\
      && cd /testbed && python -m pip install --disable-pip-version-check \\
      -e ".[test]"' || true
"""


def patch_task_toml(text: str, pypi_index: str = DEFAULT_INDEX) -> tuple[str, bool]:
    """给 [verifier] 段追加 env；返回 (新文本, 是否修改)。"""
    if "UV_HTTP_TIMEOUT" in text:
        return text, False
    env_line = (
        f'env = {{ UV_HTTP_TIMEOUT = "600", UV_INDEX_URL = "{pypi_index}", '
        f'PIP_INDEX_URL = "{pypi_index}", PIP_DEFAULT_TIMEOUT = "600" }}'
    )
    match = re.search(r"(?ms)^\[verifier\]\n(.*?)(?=^\[|\Z)", text)
    if match is None:
        return text.rstrip() + "\n\n[verifier]\n" + env_line + "\n", True
    block = match.group(0)
    if not block.endswith("\n"):
        block += "\n"
    updated = text[: match.start()] + block + env_line + "\n" + text[match.end():]
    return updated, True


def prepare_task_image(
    task_dir: str | Path,
    *,
    pypi_index: str = DEFAULT_INDEX,
    dry_run: bool = False,
) -> dict[str, object]:
    """给一个本地任务目录打补丁；返回修改摘要。"""
    task_dir = Path(task_dir)
    dockerfile = task_dir / "environment" / "Dockerfile"
    task_toml = task_dir / "task.toml"
    if not dockerfile.is_file() or not task_toml.is_file():
        raise FileNotFoundError(
            f"not a Harbor task dir (missing environment/Dockerfile or task.toml): {task_dir}"
        )

    dockerfile_text = dockerfile.read_text(encoding="utf-8")
    layer = dockerfile_layer(pypi_index)
    if MARKER in dockerfile_text:
        # 升级旧缓存层：从 marker 到文件末尾整体替换，保证脚本更新能生效
        head = dockerfile_text.split(MARKER, 1)[0].rstrip()
        updated = head + "\n" + layer
    else:
        updated = dockerfile_text.rstrip() + "\n" + layer
    dockerfile_changed = updated != dockerfile_text
    dockerfile_text = updated

    toml_text = task_toml.read_text(encoding="utf-8")
    toml_text, toml_changed = patch_task_toml(toml_text, pypi_index)

    result = {
        "task_dir": str(task_dir),
        "dockerfile_changed": dockerfile_changed,
        "task_toml_changed": toml_changed,
        "pypi_index": pypi_index,
    }
    if dry_run:
        result["dockerfile"] = dockerfile_text
        result["task_toml"] = toml_text
        return result

    if dockerfile_changed:
        dockerfile.write_text(dockerfile_text, encoding="utf-8")
    if toml_changed:
        task_toml.write_text(toml_text, encoding="utf-8")
    return result


def _default_task_dir() -> Path | None:
    base = Path("local-bench/smoke")
    if not base.is_dir():
        return None
    for candidate in sorted(base.iterdir()):
        if (candidate / "environment" / "Dockerfile").is_file() and (candidate / "task.toml").is_file():
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_dir", nargs="?", default=None, help="本地任务目录")
    parser.add_argument("--pypi-index", default=DEFAULT_INDEX, help="PyPI 镜像（simple URL）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将写入的内容")
    args = parser.parse_args(argv)

    task_dir = Path(args.task_dir) if args.task_dir else _default_task_dir()
    if task_dir is None:
        print("找不到本地任务目录；请显式传入 TASK_DIR", file=sys.stderr)
        return 2
    try:
        result = prepare_task_image(
            task_dir, pypi_index=args.pypi_index, dry_run=args.dry_run,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"task: {result['task_dir']}")
    print(f"dockerfile changed: {result['dockerfile_changed']}")
    print(f"task.toml changed: {result['task_toml_changed']}")
    print(f"pypi index: {result['pypi_index']}")
    if args.dry_run:
        print("\n===== Dockerfile =====\n" + str(result["dockerfile"]))
        print("\n===== task.toml =====\n" + str(result["task_toml"]))
        return 0
    if result["dockerfile_changed"] or result["task_toml_changed"]:
        print("\n下一步：用 --force-build 首次重建镜像：")
        print("  harbor run -p local-bench/smoke ... --force-build")
    else:
        print("已经是预装镜像配置，无需修改。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
