set -euo pipefail
cp "$BENCH_REPO_ROOT/tests/test_execution_persistence.py" tests/test_execution_persistence.py
cp "$BENCH_REPO_ROOT/tests/test_agent.py" tests/test_agent.py
cp "$BENCH_TASK_DIR/hidden/test_bench_execution.py" tests/test_bench_execution.py
PYTHONPATH=. .venv/bin/python3 -m pytest -o addopts='' -q \
  tests/test_execution_persistence.py \
  tests/test_agent.py::test_receipt_records_verified_command_exit_codes \
  tests/test_bench_execution.py
cp "$BENCH_TASK_DIR/hidden/bench-execution.test.tsx" tui/tests/bench-execution.test.tsx
npm --prefix tui run build --silent
node --test tui/dist/tests/bench-execution.test.js
