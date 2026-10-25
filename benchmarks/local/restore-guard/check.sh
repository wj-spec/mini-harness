set -euo pipefail
cp "$BENCH_REPO_ROOT/tests/test_tools.py" tests/test_tools.py
cp "$BENCH_TASK_DIR/hidden/test_bench_restore.py" tests/test_bench_restore.py
PYTHONPATH=. .venv/bin/python3 -m pytest -o addopts='' -q \
  tests/test_tools.py::test_restored_session_requires_reread_before_editing \
  tests/test_tools.py::test_restored_session_allows_new_and_own_written_files \
  tests/test_tools.py::test_external_change_after_restore_read_is_rejected \
  tests/test_bench_restore.py
