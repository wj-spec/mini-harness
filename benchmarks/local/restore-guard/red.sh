set -euo pipefail
PYTHONPATH=. .venv/bin/python3 -m pytest -o addopts='' -q \
  tests/test_tools.py::test_restored_session_requires_reread_before_editing \
  tests/test_tools.py::test_restored_session_allows_new_and_own_written_files
