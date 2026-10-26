import json

from app.user_profile import load_user_profile, select_user_preferences
from scripts.w6_preferences import EMPTY_PROFILE, TASKS, W6_TEST_PREFERENCES


def _preferences(tmp_path):
    path = tmp_path / "user-profile.json"
    path.write_text(json.dumps(W6_TEST_PREFERENCES, ensure_ascii=False), encoding="utf-8")
    return path


def test_w6_v2_fixture_selects_expected_preferences_per_task(tmp_path):
    path = _preferences(tmp_path)
    profile = load_user_profile(path)
    assert len(profile.entries) == 3

    explain = select_user_preferences(profile.entries, TASKS["task-a"]["prompt"])
    assert [entry.id for entry in explain] == ["w6-explain"]

    change = select_user_preferences(profile.entries, TASKS["task-b"]["prompt"])
    assert {entry.id for entry in change} == {"w6-change", "w6-test"}

    diagnose = select_user_preferences(profile.entries, TASKS["task-c"]["prompt"])
    assert [entry.id for entry in diagnose] == ["w6-test"]


def test_w6_v2_off_fixture_has_no_entries(tmp_path):
    path = tmp_path / "user-profile.json"
    path.write_text(json.dumps(EMPTY_PROFILE, ensure_ascii=False), encoding="utf-8")
    assert load_user_profile(path).entries == []
