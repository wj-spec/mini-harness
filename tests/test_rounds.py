import json
from dataclasses import asdict

from app.session import SessionStore
from core.rounds import build_round_record
from core.state import TaskState, completion_decision


def _change():
    return {
        "path": "a.txt", "kind": "modify",
        "before_sha256": "old", "after_sha256": "new",
        "before_status": "present", "after_status": "present",
        "size": 3, "step": 2, "tool": "write_file",
        "tool_call_id": "w1", "approval_scope": "once",
        "at": "2026-01-01T00:00:00+00:00",
    }


def test_build_round_record_keeps_evidence_not_outputs():
    state = TaskState(
        goal="修改 a.txt", file_changes=[_change()], files_changed=["a.txt"],
        mutation_seq=1, last_verification_seq=1, last_verification_ok=True,
        command_results=[{"command": "pytest", "status": "succeeded",
                          "exit_code": 0, "verification": True, "output": "x" * 500}],
    )
    record = build_round_record(
        run_id="run-1", goal=state.goal, task_kind="action", status="done",
        stop_reason="", steps=3, usage={"total_tokens": 10}, cost_usd=0.01,
        started_at="2026-01-01T00:00:00+00:00", state=state,
        completion=completion_decision(state), audit={"status": "complete"},
        verified=True, receipt="receipt",
    )

    assert record["schema_version"] == 1
    assert record["run_id"] == "run-1"
    assert record["verified"] is True
    assert record["completion"]["allowed"] is True
    assert record["task_state"]["file_changes"][0]["after_sha256"] == "new"
    assert record["task_state"]["last_verification_ok"] is True
    assert record["task_state"]["command_results_count"] == 1
    assert "output" not in json.dumps(record["task_state"])
    assert len(record["task_state"]["notes"]) <= 20


def test_session_saves_and_loads_rounds_in_order(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    first = {"schema_version": 1, "run_id": "r1", "status": "done"}
    second = {"schema_version": 1, "run_id": "r2", "status": "stopped"}

    path1 = session.save_round(first)
    path2 = session.save_round(second)

    assert path1.name == "round_001.json"
    assert path2.name == "round_002.json"
    rounds = session.load_rounds()
    assert [item["run_id"] for item in rounds] == ["r1", "r2"]
    assert [item["round_index"] for item in rounds] == [1, 2]
    assert session.latest_round()["run_id"] == "r2"
    assert not list(session.rounds_dir.glob("*.tmp"))


def test_session_skips_corrupt_round_and_rejects_invalid_record(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    session.save_round({"schema_version": 1, "run_id": "r1"})
    (session.rounds_dir / "round_002.json").write_text("{broken")

    rounds = session.load_rounds()
    assert [item["run_id"] for item in rounds] == ["r1"]

    try:
        session.save_round({"schema_version": 1})
    except ValueError as exc:
        assert "run_id" in str(exc)
    else:
        raise AssertionError("invalid round must be rejected")


def test_state_json_has_version_and_loads_legacy_without_it(tmp_path):
    session = SessionStore.new(tmp_path, "fake")
    state = TaskState(goal="g", done=["a"])
    session.save_state(state)

    raw = json.loads(session.state_path.read_text(encoding="utf-8"))
    assert raw["state_version"] == 2
    assert session.load_state() == state

    session.state_path.write_text(
        json.dumps(asdict(TaskState(goal="legacy", done=["x"])), ensure_ascii=False),
        encoding="utf-8",
    )
    loaded = session.load_state()
    assert loaded is not None and loaded.goal == "legacy" and loaded.done == ["x"]
