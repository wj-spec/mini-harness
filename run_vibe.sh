#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-$PWD/.venv/bin/python3}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

VIBE_IMPORT_PATH=.
if [ "$PY" != "$PWD/.venv/bin/python3" ]; then
  VIBE_IMPORT_PATH=.:.pylibs
fi
PYTHONPATH="$VIBE_IMPORT_PATH" exec "$PY" -m app "$@"
