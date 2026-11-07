"""--trace-summary / --trace-json 只读 CLI 测试。"""

from __future__ import annotations

import json

import pytest

from app import __main__ as entrypoint
from app.config import Config
from app.session.observability import render_trace_summary, summarize_trace
from app.session.store import SessionStore


def _seed(tmp_path):
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    session.append_trace({"type": "run_start", "ts": 1.0, "task": "观测任务"})
    session.append_trace({"type": "llm_start", "ts": 2.0, "step": 1})
    session.append_trace({"type": "llm_end", "ts": 3.0, "prompt_tokens": 10,
                          "completion_tokens": 5, "cost_usd": 0.001, "usage_estimated": False})
    session.append_trace({"type": "llm_retry", "ts": 3.5, "attempt": 1})
    session.append_trace({"type": "tool_end", "ts": 4.0, "tool": "read_file", "ok": True})
    session.append_trace({"type": "tool_end", "ts": 4.5, "tool": "bash", "ok": False})
    session.append_trace({"type": "tool_end", "ts": 4.6, "tool": "bash", "ok": False})
    session.append_trace({"type": "llm_error", "ts": 4.7, "error": "ConnectTimeout"})
    session.append_trace({"type": "approval", "ts": 5.0, "tool": "bash", "allowed": False})
    session.append_trace({"type": "run_end", "ts": 6.0, "status": "stopped",
                          "stop_reason": "max_run_seconds"})
    with session.trace_path.open("a", encoding="utf-8") as handle:
        handle.write("not-json\n")
    return session


def test_summarize_trace_aggregates(tmp_path):
    session = _seed(tmp_path)
    summary = summarize_trace(session.root)
    assert summary["runs"] == 1
    assert summary["llm_calls"] == 1 and summary["llm_retries"] == 1
    assert summary["tool_calls"] == 3 and summary["tool_failures"] == 2
    assert summary["failure_tools"] == {"bash": 2}
    assert summary["llm_error_messages"] == {"ConnectTimeout": 1}
    assert summary["approvals"] == 1 and summary["denials"] == 1
    assert summary["total_tokens"] == 15
    assert summary["cost_usd"] == 0.001
    assert summary["last_status"] == "stopped"
    assert summary["stop_reasons"] == {"max_run_seconds": 1}
    assert summary["malformed"] == 1
    text = render_trace_summary(summary)
    assert "trace 摘要" in text and "max_run_seconds=1" in text
    assert "工具失败分布：bash=2" in text
    assert "LLM 错误：ConnectTimeout×1" in text


def test_trace_summary_cli_text(tmp_path, monkeypatch, capsys):
    session = _seed(tmp_path)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--trace-summary", "--session", session.id,
                                     "--workspace", str(tmp_path)])
    entrypoint.main()
    out = capsys.readouterr().out
    assert "trace 摘要" in out and "tokens 15" in out


def test_trace_summary_cli_json(tmp_path, monkeypatch, capsys):
    session = _seed(tmp_path)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--trace-summary", "--trace-json",
                                     "--session", session.id, "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["llm_calls"] == 1 and payload["denials"] == 1
    assert payload["failure_tools"] == {"bash": 2}


def test_trace_summary_empty_session(tmp_path, monkeypatch, capsys):
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--trace-summary", "--session", session.id,
                                     "--workspace", str(tmp_path)])
    entrypoint.main()
    assert "事件：0" in capsys.readouterr().out


def test_trace_summary_rejects_other_modes(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["vibe", "--trace-summary", "--session", "s-1",
                                     "--workspace", "/tmp", "--stdio"])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--trace-summary cannot be combined" in capsys.readouterr().err


def _seed_second(tmp_path):
    session = SessionStore.new(tmp_path, "fake", purpose="user")
    session.append_trace({"type": "run_start", "ts": 1.0})
    session.append_trace({"type": "llm_end", "ts": 2.0, "prompt_tokens": 4,
                          "completion_tokens": 2, "cost_usd": 0.0005})
    session.append_trace({"type": "run_end", "ts": 3.0, "status": "done", "stop_reason": ""})
    return session


def test_trace_all_sessions_cli_text(tmp_path, monkeypatch, capsys):
    import os

    _seed(tmp_path)
    sessions_dir = tmp_path / ".vibe" / "sessions"
    _seed_second(tmp_path)
    sessions = [path for path in sessions_dir.iterdir() if path.is_dir()]
    old = min(sessions, key=lambda path: path.stat().st_mtime)
    newest = max(sessions, key=lambda path: path.stat().st_mtime)
    os.utime(old, (old.stat().st_atime, old.stat().st_mtime - 100))
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--trace-summary", "--trace-all-sessions",
                                     "--workspace", str(tmp_path)])
    entrypoint.main()
    out = capsys.readouterr().out
    assert "工作区 trace 汇总（2 个会话）" in out
    assert "LLM：calls 2" in out
    assert "最近会话" in out
    lines = [line for line in out.splitlines() if line.startswith("  - ")]
    assert lines and lines[-1].startswith(f"  - {newest.name}")


def test_trace_all_sessions_cli_json(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    _seed_second(tmp_path)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--trace-summary", "--trace-all-sessions",
                                     "--trace-json", "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "all_sessions"
    assert len(payload["sessions"]) == 2
    assert payload["totals"]["llm_calls"] == 2
    assert payload["totals"]["total_tokens"] == 21
    assert payload["totals"]["failure_tools"] == {"bash": 2}
    assert payload["totals"]["llm_error_messages"] == {"ConnectTimeout": 1}


def test_trace_all_sessions_rejects_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["vibe", "--trace-summary", "--trace-all-sessions",
                                     "--session", "s-1", "--workspace", str(tmp_path)])
    with pytest.raises(SystemExit) as error:
        entrypoint.main()
    assert error.value.code == 2
    assert "--trace-all-sessions cannot combine with --session" in capsys.readouterr().err


def _seed_window(tmp_path):
    import time

    session = SessionStore.new(tmp_path, "fake", purpose="user")
    now = time.time()
    session.append_trace({"type": "llm_end", "ts": now - 7200, "prompt_tokens": 100,
                          "completion_tokens": 50, "cost_usd": 0.01})
    session.append_trace({"type": "llm_end", "ts": now - 60, "prompt_tokens": 10,
                          "completion_tokens": 5, "cost_usd": 0.001})
    session.append_trace({"type": "run_end", "ts": now - 30, "status": "done", "stop_reason": ""})
    return session


def test_trace_last_hours_filters_events(tmp_path, monkeypatch, capsys):
    session = _seed_window(tmp_path)
    monkeypatch.setattr(entrypoint.Config, "from_env", lambda: Config(workspace=tmp_path))
    monkeypatch.setattr("sys.argv", ["vibe", "--trace-summary", "--trace-json",
                                     "--trace-last-hours", "1",
                                     "--session", session.id, "--workspace", str(tmp_path)])
    entrypoint.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["llm_calls"] == 1
    assert payload["total_tokens"] == 15
    assert payload["since_ts"] is not None


def test_trace_last_hours_validation(monkeypatch, capsys):
    for value in ("0", "-1", "9000"):
        monkeypatch.setattr("sys.argv", ["vibe", "--trace-summary", "--trace-last-hours", value,
                                         "--session", "s-1", "--workspace", "/tmp"])
        with pytest.raises(SystemExit) as error:
            entrypoint.main()
        assert error.value.code == 2
        assert "--trace-last-hours must be between 0 and 8760" in capsys.readouterr().err
