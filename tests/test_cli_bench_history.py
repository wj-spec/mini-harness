"""--bench-history 只读 CLI 测试。"""

from __future__ import annotations

import json

import pytest

from app import __main__ as entrypoint
from app.config import Config
from app.eval.history import bench_reports, render_bench_history


def _report(path, *, generated_at, agent_version, passed=2, total=2, mode="fake-worker-p0"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "generated_at": generated_at,
        "mode": mode,
        "agent_version": agent_version,
        "scenarios": ["a", "b"],
        "summary": {
            "total": total, "passed": passed, "failed": total - passed,
            "scenario_stats": {
                "a": {"runs": 1, "passed": 1, "failed": 0, "avg_duration_seconds": 1.2},
            },
        },
    }), encoding="utf-8")


def _seed(tmp_path):
    root = tmp_path / ".vibe" / "bench-long"
    _report(root / "20261101T000000Z" / "report.json",
            generated_at="2026-11-01T00:00:00+00:00", agent_version="aaa1111")
    _report(root / "20261102T000000Z" / "report.json",
            generated_at="2026-11-02T00:00:00+00:00", agent_version="bbb2222", passed=1)


def test_bench_history_reads_reports(tmp_path):
    _seed(tmp_path)
    rows = bench_reports(tmp_path, limit=10)
    assert len(rows) == 2
    assert rows[-1]["agent_version"] == "bbb2222"
    assert rows[-1]["passed"] == 1 and rows[-1]["total"] == 2
    text = render_bench_history(rows)
    assert "长任务 benchmark 历史（2 次）" in text
    assert "agent bbb2222" in text
    assert "最近一次场景通过率" in text


def test_bench_history_cli_text(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--bench-history", "--workspace", str(tmp_path)])
    entrypoint.main()
    out = capsys.readouterr().out
    assert "（2 次）" in out and "bbb2222" in out


def test_bench_history_cli_json(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--bench-history", "--bench-history-json",
                                     "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 2
    assert payload["reports"][-1]["agent_version"] == "bbb2222"


def test_bench_history_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--bench-history", "--workspace", str(tmp_path)])
    entrypoint.main()
    assert "还没有长任务 benchmark 报告" in capsys.readouterr().out


def test_bench_history_limit_bounds(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["vibe", "--bench-history", "--bench-history-limit", "0",
                                     "--workspace", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--bench-history-limit must be between 1 and 200" in capsys.readouterr().err


def test_bench_history_rejects_other_modes(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["vibe", "--bench-history", "--workspace", "/tmp", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--bench-history cannot be combined" in capsys.readouterr().err
