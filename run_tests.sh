#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-$PWD/.venv/bin/python3}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

PYTHONPATH=.:.pylibs exec "$PY" -m pytest "$@"
