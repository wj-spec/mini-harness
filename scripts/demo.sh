#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)/demo-repo"
cp -R "$ROOT/examples/demo-repo" "$TMP"

echo "demo repo: $TMP"
echo "试试：修一下 calc.py，让 python3 test_calc.py 通过"
echo
cd "$ROOT"
exec ./run_vibe.sh --workspace "$TMP"
