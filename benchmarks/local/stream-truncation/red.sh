set -euo pipefail
PYTHONPATH=. .venv/bin/python3 -m pytest -o addopts='' -q \
  tests/test_provider.py::test_incomplete_tool_calls_are_not_executable \
  tests/test_provider.py::test_stream_without_done_or_finish_reason_is_truncated
