#!/usr/bin/env bash
set -euo pipefail
# 公共入口统一加载用户配置；start.sh 回调时不再重复准备。
if [ "${VIBE_CONFIG_LOADED:-0}" != "1" ]; then
  exec "$(dirname "$0")/start.sh" "$@"
fi
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
  echo "首次使用请运行 ./start.sh；或手动安装：npm --prefix tui ci" >&2
  exit 2
fi

need_build=0
if [ ! -f tui/dist/src/cli.js ]; then
  need_build=1
elif find tui/src -type f -newer tui/dist/src/cli.js -print -quit | grep -q .; then
  need_build=1
fi
if [ "$need_build" = "1" ] && [ "${VIBE_TUI_SKIP_BUILD:-0}" != "1" ]; then
  # 构建自己的 stdin 与交互终端隔离，避免 npm/tsc 改变终端输入状态。
  npm --prefix tui run build --silent < /dev/null
  stty sane < /dev/tty 2>/dev/null || true
fi
# 不用 exec：让 node 作为子进程拿到自己的前后台进程组，避免脚本进程组
# 在某些终端上无法设置 raw mode。
node tui/dist/src/cli.js "$@"
