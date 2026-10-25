set -euo pipefail
PYTHONPATH=. .venv/bin/python3 -m pytest -o addopts='' -q \
  tests/test_execution_persistence.py::test_agent_message_persists_execution_and_plain_output \
  tests/test_agent.py::test_receipt_records_verified_command_exit_codes
