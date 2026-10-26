import pytest

from core.models import ToolCall
from core.progress import ProgressMonitor


def call(name="read_file", **args):
    return ToolCall(name, args)


def test_many_distinct_reads_are_not_a_call_limit():
    monitor = ProgressMonitor()
    for index in range(100):
        result = monitor.observe(call(path=f"{index}.py"), True, "same contents")
        assert result.action == "continue"
        assert result.streak == 0


def test_same_request_with_changed_output_is_progress():
    monitor = ProgressMonitor(threshold=2)
    request = call(path="a.py")
    monitor.observe(request, True, "before")
    assert monitor.observe(request, True, "before").streak == 1
    result = monitor.observe(request, True, "after")
    assert (result.action, result.streak) == ("continue", 0)


def test_repeated_observations_warn_once_then_stop():
    monitor = ProgressMonitor(threshold=2)
    request = call(path="a.py")
    decisions = [monitor.observe(request, True, "unchanged") for _ in range(5)]
    assert [d.action for d in decisions] == ["continue", "continue", "warn", "continue", "stop"]
    assert decisions[2].streak == decisions[4].streak == 2
    assert decisions[2].reason == "repeated_result"


def test_alternating_unchanged_calls_are_a_cycle():
    monitor = ProgressMonitor(threshold=2)
    a, b = call(path="a.py"), call(path="b.py")
    actions = [monitor.observe(request, True, request.args["path"]).action
               for request in [a, b, a, b, a, b]]
    assert actions == ["continue", "continue", "continue", "warn", "continue", "stop"]


def test_new_evidence_after_warning_resets_the_degradation():
    monitor = ProgressMonitor(threshold=1)
    request = call(path="a.py")
    monitor.observe(request, True, "before")
    assert monitor.observe(request, True, "before").action == "warn"
    assert monitor.observe(request, True, "after").action == "continue"
    assert monitor.observe(request, True, "after").action == "warn"


def test_different_arguments_with_failures_do_not_look_like_progress():
    monitor = ProgressMonitor(threshold=2)
    first = monitor.observe(call(path="missing1.py"), False, "ERROR FileNotFoundError: missing1.py")
    second = monitor.observe(call(path="missing2.py"), False, "ERROR FileNotFoundError: missing2.py")
    assert (first.action, first.streak) == ("continue", 1)
    assert second.action == "warn"


@pytest.mark.parametrize("ok, output", [
    (False, "failed"),
    (True, "ERROR ValueError: bad input"),
    (True, "$ pytest\n(cwd=., timeout=30s)\nexit=1\nfailed"),
    (True, "$ pytest\n(cwd=., timeout=30s)\nERROR timeout after 30s\npartial"),
])
def test_explicit_failure_is_detected_even_when_registry_ok_is_true(ok, output):
    monitor = ProgressMonitor(threshold=1)
    result = monitor.observe(call("bash", command="pytest"), ok, output)
    assert (result.action, result.reason) == ("warn", "tool_error")


def test_error_words_inside_successful_file_content_are_not_errors():
    monitor = ProgressMonitor(threshold=1)
    result = monitor.observe(call(path="errors.py"), True, "1: ERROR timeout after 1s\n2: exit=2")
    assert (result.action, result.streak) == ("continue", 0)


def test_multiline_command_failure_is_detected():
    monitor = ProgressMonitor(threshold=1)
    request = call("bash", command="first command\nsecond command")
    output = "$ first command\nsecond command\n(cwd=., timeout=30s)\nexit=2\nfailed"
    assert monitor.observe(request, True, output).reason == "tool_error"


def test_successful_repair_allows_repeating_a_test_and_read():
    monitor = ProgressMonitor(threshold=1)
    test = call("bash", command="pytest")
    read = call(path="a.py")
    monitor.observe(test, True, "$ pytest\n(cwd=., timeout=30s)\nexit=0\nok")
    monitor.observe(read, True, "content")
    repair = call("edit_file", path="a.py", old="a", new="b")
    assert monitor.observe(repair, True, "edited a.py (1 replacement)").action == "continue"
    assert monitor.observe(test, True, "$ pytest\n(cwd=., timeout=30s)\nexit=0\nok").action == "continue"
    assert monitor.observe(read, True, "content").action == "continue"


def test_identical_write_payload_does_not_reset_progress_forever():
    monitor = ProgressMonitor(threshold=1)
    write = call("write_file", path="a.py", content="same")
    assert monitor.observe(write, True, "wrote a.py (4 bytes)").action == "continue"
    assert monitor.observe(write, True, "wrote a.py (4 bytes)").action == "warn"
    assert monitor.observe(write, True, "wrote a.py (4 bytes)").action == "stop"


def test_self_reported_plans_cannot_clear_a_failure_streak():
    monitor = ProgressMonitor(threshold=2)
    monitor.observe(call(path="missing.py"), False, "ERROR FileNotFoundError: missing.py")
    decision = monitor.observe(call("update_task", done=["everything passed"]), True, "updated")
    assert decision.action == "warn"
    assert decision.reason == "state_only"
    assert monitor.observe(call("update_task", note="try again"), True, "updated").streak == 1
    assert monitor.observe(call("update_task", note="new plan"), True, "updated").action == "stop"


def test_denial_warns_immediately_and_same_denial_stops():
    monitor = ProgressMonitor(threshold=4)
    request = call("bash", command="something")
    first = monitor.observe(request, False, "ERROR denied_by_user")
    assert (first.action, first.reason) == ("warn", "permission_denied")
    assert monitor.observe(request, False, "ERROR denied_by_user").action == "stop"


def test_successful_alternative_after_denial_resets_the_warning_stage():
    monitor = ProgressMonitor(threshold=1)
    monitor.observe(call("bash", command="something"), False, "ERROR denied_by_user")
    read = call(path="a.py")
    assert monitor.observe(read, True, "content").action == "continue"
    assert monitor.observe(read, True, "content").action == "warn"


def test_argument_key_order_and_protocol_id_do_not_hide_a_repeat():
    monitor = ProgressMonitor(threshold=1)
    first = ToolCall("read_file", {"path": "a.py", "start": 1}, id="first")
    second = ToolCall("read_file", {"start": 1, "path": "a.py"}, id="second")
    monitor.observe(first, True, "same")
    assert monitor.observe(second, True, "same").action == "warn"


def test_monitor_keeps_a_bounded_observation_window():
    monitor = ProgressMonitor(threshold=1, max_entries=2)
    for path in ["a.py", "b.py", "c.py"]:
        monitor.observe(call(path=path), True, path)
    assert monitor.observe(call(path="a.py"), True, "a.py").action == "continue"


def test_zero_threshold_disables_progress_guard():
    monitor = ProgressMonitor(threshold=0)
    for _ in range(10):
        assert monitor.observe(call(path="missing"), False, "ERROR denied_by_user").action == "continue"


def test_same_file_reads_with_changing_ranges_are_capped():
    monitor = ProgressMonitor(threshold=2, max_reads_per_file=3, max_warnings=2)
    actions = []
    for index in range(10):
        decision = monitor.observe(call(path="large.py", start=index + 1, end=index + 10), True, f"slice {index}")
        actions.append(decision.action)
        if index >= 3:
            assert decision.reason == "repeated_read"
    assert actions[:4] == ["continue", "continue", "continue", "continue"]
    assert "warn" in actions and "stop" in actions


def test_mutation_resets_repeated_read_budget():
    monitor = ProgressMonitor(threshold=2, max_reads_per_file=2)
    assert monitor.observe(call(path="a.py"), True, "content").action == "continue"
    assert monitor.observe(call(path="a.py", start=2), True, "content").action == "continue"
    assert monitor.observe(call(path="a.py", start=3), True, "content").action == "continue"
    assert monitor.observe(call("edit_file", path="a.py", old="a", new="b"), True, "edited").action == "continue"
    assert monitor.observe(call(path="a.py"), True, "new content").action == "continue"


def test_warning_budget_stops_after_repeated_stall_cycles():
    monitor = ProgressMonitor(threshold=1, max_warnings=2)
    for index, path in enumerate(["a.py", "b.py", "c.py"]):
        monitor.observe(call(path=path), True, f"content {index}")
        decision = monitor.observe(call(path=path), True, f"content {index}")
        if index < 2:
            assert decision.action == "warn"
        else:
            assert decision.action == "stop"


def test_repeated_call_hash_warns_even_when_output_changes():
    monitor = ProgressMonitor(threshold=2, max_repeated_calls=3)
    request = call(path="a.py")
    decisions = [monitor.observe(request, True, f"output {index}") for index in range(6)]
    assert decisions[2].reason == "repeated_call" and decisions[2].action == "continue"
    assert decisions[3].action == "warn"
    assert decisions[5].action == "stop"


def test_artifact_progress_only_resets_on_file_or_verification_change():
    from core.progress import ArtifactProgress
    monitor = ArtifactProgress(threshold=2, max_warnings=2)
    none = monitor.observe()
    assert (none.action, none.count) == ("continue", 1)
    assert monitor.observe().action == "warn"
    assert monitor.observe(verification=("pytest", "exit=0")) .action == "continue"

    monitor = ArtifactProgress(threshold=2, max_warnings=2)
    monitor.observe()
    assert monitor.observe(fingerprint={"a.py": "hash-1"}).action == "continue"
    assert monitor.observe(fingerprint={"a.py": "hash-1"}).count == 1
    assert monitor.observe(fingerprint={"a.py": "hash-2"}).action == "continue"
    assert monitor.observe(fingerprint={"a.py": "hash-2"}).action == "continue"


def test_artifact_progress_stops_after_repeated_no_progress_windows():
    from core.progress import ArtifactProgress
    monitor = ArtifactProgress(threshold=2, max_warnings=2)
    actions = [monitor.observe().action for _ in range(4)]
    assert actions == ["continue", "warn", "continue", "stop"]
