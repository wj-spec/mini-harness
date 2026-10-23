set -euo pipefail
PYTHONPATH=. .venv/bin/python3 -m pytest -o addopts='' -q \
  tests/test_tools.py::test_recall_rejects_path_traversal_outside_workspace \
  tests/test_tools.py::test_recall_rejects_invalid_reference_format
