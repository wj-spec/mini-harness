#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# --doctor 必须在 Node / 前端依赖检查之前拦截：自检本身不需要 node_modules。
for arg in "$@"; do
  if [ "$arg" = "--doctor" ]; then
    PY="${PYTHON:-$PWD/.venv/bin/python3}"
    if [ ! -x "$PY" ]; then
      PY="$(command -v python3 || true)"
    fi
    if [ -z "$PY" ]; then
      echo "环境自检需要 Python 3.11+。" >&2
      exit 2
    fi
    exec "$PY" scripts/doctor.py "$@"
  fi
done

if ! command -v node >/dev/null || ! command -v npm >/dev/null; then
  echo "Ink 入口需要 Node.js 22+ 和 npm。" >&2
  exit 2
fi
if [ ! -d tui/node_modules/ink ]; then
  echo "请先安装依赖：npm --prefix tui ci" >&2
  exit 2
fi

if [ "${VIBE_TUI_SKIP_BUILD:-0}" != "1" ]; then
  # 构建自己的 stdin 与交互终端隔离，避免 npm/tsc 改变终端输入状态。
  npm --prefix tui run build --silent < /dev/null
fi
exec node tui/dist/src/cli.js "$@"
