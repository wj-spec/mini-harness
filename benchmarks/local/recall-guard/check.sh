set -euo pipefail
# 恢复原始可见测试，防止通过改测试绕过；再跑隐藏变体。
cp "$BENCH_REPO_ROOT/tests/test_tools.py" tests/test_tools.py
cp "$BENCH_TASK_DIR/hidden/test_bench_recall.py" tests/test_bench_recall.py
PYTHONPATH=. .venv/bin/python3 -m pytest -o addopts='' -q \
  tests/test_tools.py::test_recall_rejects_path_traversal_outside_workspace \
  tests/test_tools.py::test_recall_rejects_invalid_reference_format \
  tests/test_tools.py::test_recall_still_resolves_saved_offload_file \
  tests/test_bench_recall.py
