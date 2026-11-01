from app.tools import ToolRegistry
from core.models import ToolCall
from core.state import TaskState


def test_mutation_metadata_records_lifecycle_without_content(tmp_path):
    state = TaskState(goal="修改 a.txt")
    registry = ToolRegistry(tmp_path, state=state)
    call = ToolCall("write_file", {"path": "a.txt", "content": "new"}, id="w1")

    before = registry.mutation_metadata(call)
    registry.write_file("a.txt", "new")
    after = registry.mutation_metadata(call)

    assert before == {"a.txt": {"status": "missing"}}
    assert after["a.txt"]["status"] == "present"
    assert after["a.txt"]["size"] == 3

    changes = state.record_file_changes(
        before=before, after=after, tool=call.name, step=2,
        tool_call_id=call.id, approval_scope="workspace",
    )

    assert len(changes) == 1
    change = changes[0]
    assert change["path"] == "a.txt"
    assert change["kind"] == "create"
    assert change["before_sha256"] is None
    assert change["after_sha256"] == after["a.txt"]["sha256"]
    assert change["tool_call_id"] == "w1"
    assert change["approval_scope"] == "workspace"
    assert "content" not in change and "patch" not in change
    assert state.mutation_seq == 1
    assert state.files_changed == ["a.txt"]


def test_mutation_metadata_marks_modify_only_after_real_content_change(tmp_path):
    (tmp_path / "a.txt").write_text("old")
    state = TaskState(goal="修改 a.txt")
    registry = ToolRegistry(tmp_path, state=state)
    call = ToolCall("write_file", {"path": "a.txt", "content": "new"}, id="w2")

    before = registry.mutation_metadata(call)
    registry.write_file("a.txt", "new")
    after = registry.mutation_metadata(call)
    changes = state.record_file_changes(before=before, after=after, tool="write_file")

    assert changes[0]["kind"] == "modify"
    assert changes[0]["before_sha256"] != changes[0]["after_sha256"]
    assert state.mutation_seq == 1


def test_identical_write_does_not_count_as_mutation(tmp_path):
    (tmp_path / "a.txt").write_text("same")
    state = TaskState(goal="修改 a.txt")
    registry = ToolRegistry(tmp_path, state=state)
    call = ToolCall("write_file", {"path": "a.txt", "content": "same"}, id="w3")

    before = registry.mutation_metadata(call)
    registry.write_file("a.txt", "same")
    after = registry.mutation_metadata(call)
    changes = state.record_file_changes(before=before, after=after, tool="write_file")

    assert changes == []
    assert state.mutation_seq == 0
    assert state.file_changes == []
