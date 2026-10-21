from core.context import stable_prefix_hash
from core.models import Message
from core.state import TaskState


def test_render_and_update():
    state = TaskState(goal="fix bug")
    rendered = state.update(
        plan=["read", "fix"], done=["read"], open=["fix"], note="started"
    )
    assert "Goal: fix bug" in rendered
    assert "Plan: read | fix" in rendered
    assert "Done: read" in rendered
    assert "Open: fix" in rendered
    assert "started" in rendered


def test_observe_tool_tracks_files_without_assuming_commands_are_tests():
    state = TaskState(goal="g")
    state.observe_tool("write_file", {"path": "a.py"}, True, "ok")
    state.observe_tool("bash", {"command": "pytest -q"}, False, "1 failed")

    assert state.files_changed == ["a.py"]
    assert state.tests_run == []
    assert any("bash failed" in note for note in state.notes)


def test_task_state_is_not_in_stable_prefix_hash():
    a = [Message("system", "sys"), Message("system", "Goal: a", name="task_state")]
    b = [Message("system", "sys"), Message("system", "Goal: b", name="task_state")]
    assert stable_prefix_hash(a) == stable_prefix_hash(b)
