set -euo pipefail
PYTHONPATH=. .venv/bin/python3 -m pytest -o addopts='' -q \
  tests/test_stdio.py::test_cancelled_run_records_observed_tool_count
