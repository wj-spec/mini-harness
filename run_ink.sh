#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v node >/dev/null || ! command -v npm >/dev/null; then
  echo "Ink 入口需要 Node.js 22+ 和 npm。" >&2
  exit 2
fi
if [ ! -d tui/node_modules/ink ]; then
  echo "请先安装依赖：npm --prefix tui ci" >&2
  exit 2
fi

npm --prefix tui run build --silent
exec node tui/dist/src/cli.js "$@"
