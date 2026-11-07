"""`--version` 构建信息输出测试。"""

from __future__ import annotations

import json
import sys

import pytest

from app import __main__ as entrypoint


def test_version_outputs_json(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vibe", "--version"])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] and payload["python"]
    assert set(payload) == {"version", "python", "agent_version", "platform"}


def test_version_rejects_other_modes(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["vibe", "--version", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--version cannot be combined" in capsys.readouterr().err
