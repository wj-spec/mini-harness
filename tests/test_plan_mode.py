from __future__ import annotations

from app.runtime.plan_mode import PLAN_MODE_FILE, PlanModeStore


def test_idle_toggle_commits_and_is_idempotent(tmp_path):
    store = PlanModeStore(tmp_path)
    assert store.status() == {"active": False, "pending": False, "wanted": None}
    assert store.set(True) == "committed"
    assert store.status() == {"active": True, "pending": False, "wanted": None}
    assert store.path == tmp_path / PLAN_MODE_FILE
    assert store.set(True) == "noop"
    assert store.set(False) == "committed"
    assert store.effective() is False
    assert not (tmp_path / f"{PLAN_MODE_FILE}.tmp").exists()


def test_running_toggle_queues_pending_and_commits(tmp_path):
    store = PlanModeStore(tmp_path)
    assert store.set(True, running=True) == "queued"
    assert store.status() == {"active": False, "pending": True, "wanted": True}
    assert store.effective() is False
    state = store.commit_pending()
    assert state.active is True and state.wanted is None
    assert store.status() == {"active": True, "pending": False, "wanted": None}


def test_running_toggle_can_be_cancelled(tmp_path):
    store = PlanModeStore(tmp_path)
    store.set(True)
    assert store.set(False, running=True) == "queued"
    assert store.status()["pending"] is True
    assert store.set(True, running=True) == "cancelled"
    assert store.status() == {"active": True, "pending": False, "wanted": None}


def test_running_noop_when_no_change(tmp_path):
    store = PlanModeStore(tmp_path)
    store.set(True)
    assert store.set(True, running=True) == "noop"


def test_corrupt_state_falls_back_to_inactive(tmp_path):
    store = PlanModeStore(tmp_path)
    store.path.write_text("{not-json", encoding="utf-8")
    assert store.status() == {"active": False, "pending": False, "wanted": None}
    assert store.set(True) == "committed"
