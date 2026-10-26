import json

from scripts.w6_report_v2 import group_runs, summary


def _evidence(path, label, version, selected):
    payload = {
        "label": label,
        "user_profile": {"version": version, "enabled": True, "entries": []},
        "profile_selections": [{"run_id": "r", "selected": selected}],
        "user_message_count": 1,
        "run_ends": [{"steps": 2, "total_tokens": 100, "tool_calls": 1}],
        "commands": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_report_excludes_v1_evidence_and_counts_only_real_selections(tmp_path):
    _evidence(tmp_path / "old.json", "task-a-on", 1, [])
    _evidence(tmp_path / "on-1.json", "task-a-on-1", 2, [{"text": "解释偏好"}])
    _evidence(tmp_path / "on-2.json", "task-a-on-2", 2, [])
    _evidence(tmp_path / "off-1.json", "task-a-off-1", 2, [])
    groups, ignored = group_runs(tmp_path)
    assert ignored == 1
    assert len(groups[("task-a", "on")]) == 2
    assert len(groups[("task-a", "off")]) == 1
    stats = summary(groups[("task-a", "on")])
    assert stats["selected_runs"] == 1
    assert [item["text"] for item in stats["selected"]] == ["解释偏好"]
