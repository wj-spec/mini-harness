#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python3}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3 || true)"
fi
if [ -z "$PY" ]; then
  echo "需要 Python 3.11+：https://www.python.org/downloads/ 。安装后重新运行 ./start.sh。" >&2
  exit 2
fi
exec "$PY" "$ROOT/scripts/start.py" "$@"
