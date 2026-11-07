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
    assert summary["tool_calls"] == 2 and summary["tool_failures"] == 1
    assert summary["approvals"] == 1 and summary["denials"] == 1
    assert summary["total_tokens"] == 15
    assert summary["cost_usd"] == 0.001
    assert summary["last_status"] == "stopped"
    assert summary["stop_reasons"] == {"max_run_seconds": 1}
    assert summary["malformed"] == 1
    text = render_trace_summary(summary)
    assert "trace 摘要" in text and "max_run_seconds=1" in text


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
