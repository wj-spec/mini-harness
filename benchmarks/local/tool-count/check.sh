set -euo pipefail
cp "$BENCH_REPO_ROOT/tests/test_stdio.py" tests/test_stdio.py
cp "$BENCH_TASK_DIR/hidden/test_bench_tool_count.py" tests/test_bench_tool_count.py
PYTHONPATH=. .venv/bin/python3 -m pytest -o addopts='' -q \
  tests/test_stdio.py::test_cancelled_run_records_observed_tool_count \
  tests/test_bench_tool_count.py
