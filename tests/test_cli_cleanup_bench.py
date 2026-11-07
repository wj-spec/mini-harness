"""--cleanup-bench 显式清理测试。"""

from __future__ import annotations

import json

import pytest

from app import __main__ as entrypoint
from app.config import Config


def _seed(tmp_path, count=3):
    root = tmp_path / ".vibe" / "bench-long"
    for index in range(count):
        directory = root / f"2026110{index + 1}T000000Z"
        directory.mkdir(parents=True)
        (directory / "report.json").write_text('{}', encoding="utf-8")
    return root


def test_cleanup_bench_keeps_latest(tmp_path, monkeypatch, capsys):
    root = _seed(tmp_path, count=3)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    (root / "keep-me").mkdir()
    monkeypatch.setattr("sys.argv", ["vibe", "--cleanup-bench", "--bench-keep", "2",
                                     "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"] == 3 and payload["keep"] == 2
    assert len(payload["removed"]) == 1 and payload["removed"][0]["removed"] is True
    remaining = sorted(path.name for path in root.iterdir())
    assert remaining == ["20261102T000000Z", "20261103T000000Z", "keep-me"]


def test_cleanup_bench_dry_run_does_not_remove(tmp_path, monkeypatch, capsys):
    root = _seed(tmp_path, count=3)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--cleanup-bench", "--bench-keep", "1",
                                     "--log-dry-run", "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert all(item["removed"] is False for item in payload["removed"])
    assert len(list(root.iterdir())) == 3


def test_cleanup_bench_empty_workspace(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--cleanup-bench", "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"] == 0 and payload["removed"] == []


def test_cleanup_bench_validation(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["vibe", "--bench-keep", "2", "--workspace", "/tmp"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--bench-keep requires --cleanup-bench" in capsys.readouterr().err

    monkeypatch.setattr("sys.argv", ["vibe", "--cleanup-bench", "--bench-keep", "-1",
                                     "--workspace", "/tmp"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--bench-keep must be non-negative" in capsys.readouterr().err

    monkeypatch.setattr("sys.argv", ["vibe", "--cleanup-bench", "--workspace", "/tmp", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--cleanup-bench cannot be combined" in capsys.readouterr().err
