"""所有只读 CLI 视图对损坏运行时状态的统一容错测试。"""

from __future__ import annotations

import json

from app import __main__ as entrypoint
from app.config import Config
from app.runtime.tasks import TaskStore
from app.session.store import SessionStore


def test_read_only_views_tolerate_corrupt_state(tmp_path, monkeypatch, capsys):
    session = SessionStore.new(tmp_path, "fake", purpose="test")
    store = TaskStore(session.root)
    task = store.create(kind="run", objective="损坏容错", workspace=str(tmp_path), mode="act")
    task_dir = store.task_dir(task.task_id)

    corrupt_task = session.root / "tasks" / "t-20261107-000000-badbad"
    corrupt_task.mkdir(parents=True)
    (corrupt_task / "meta.json").write_text("{bad json", encoding="utf-8")

    session.rounds_dir.mkdir(parents=True, exist_ok=True)
    (session.rounds_dir / "round_9999.json").write_text("{bad json", encoding="utf-8")
    plan_dir = task_dir / "plan"
    plan_dir.mkdir()
    (plan_dir / "plan.v99.json").write_text("{bad json", encoding="utf-8")
    (task_dir / "acceptance.v99.json").write_text("{bad json", encoding="utf-8")
    (task_dir / "approvals.jsonl").write_text("{bad json\n", encoding="utf-8")
    (tmp_path / ".vibe" / "notifications.jsonl").write_text("{bad json\n", encoding="utf-8")
    bad_bench = tmp_path / ".vibe" / "bench-long" / "20261107T000000Z"
    bad_bench.mkdir(parents=True)
    (bad_bench / "report.json").write_text("{bad json", encoding="utf-8")

    monkeypatch.setattr(
        entrypoint.Config, "from_env",
        lambda: Config(workspace=tmp_path, provider="fake"),
    )
    commands = [
        ["--tasks", "--tasks-json", "--fake"],
        ["--plans", "--fake"],
        ["--acceptance", "--fake"],
        ["--approvals", "--fake"],
        ["--notifications", "--fake"],
        ["--evidence", "--evidence-json", "--session", session.id, "--fake"],
        ["--bench-history", "--bench-history-json", "--fake"],
        ["--trace-summary", "--trace-all-sessions", "--trace-json", "--fake"],
    ]
    for argv in commands:
        monkeypatch.setattr("sys.argv", ["vibe", *argv, "--workspace", str(tmp_path)])
        entrypoint.main()
        captured = capsys.readouterr()
        json.loads(captured.out)  # 视图仍输出合法 JSON，不崩溃
