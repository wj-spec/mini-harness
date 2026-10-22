from app.eval import EvalTask, load_tasks, run_checks, run_eval
from app.providers.fake import FakeProvider
from core.models import Response, ToolCall


def test_load_default_tasks():
    tasks = load_tasks()
    assert len(tasks) == 10
    assert len({task.id for task in tasks}) == 10


def test_run_checks_file_and_command(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n")
    task = EvalTask(
        id="t",
        prompt="p",
        checks=[
            {"type": "file_equals", "path": "a.txt", "text": "hello"},
            {"type": "file_contains", "path": "a.txt", "text": "ell"},
            {"type": "command", "command": "echo ok", "exit": 0, "contains": "ok"},
        ],
    )
    checks = run_checks(task, tmp_path)
    assert all(check["ok"] for check in checks)


def test_run_eval_with_scripted_provider(tmp_path):
    task = EvalTask(
        id="create_file",
        prompt="create hello.txt",
        checks=[{"type": "file_equals", "path": "hello.txt", "text": "hello"}],
    )

    def factory(_task):
        return FakeProvider(
            [
                Response(
                    tool_calls=[
                        ToolCall(
                            "write_file",
                            {"path": "hello.txt", "content": "hello"},
                            id="c1",
                        )
                    ]
                ),
                Response(content="done"),
            ]
        )

    report = run_eval([task], factory, tmp_path, model="fake")

    assert report.passed == 1
    assert report.total == 1
    assert report.previous_passed is None
    assert report.delta is None
    assert (tmp_path / ".vibe" / "evals").exists()
    assert (report.run_dir and __import__("pathlib").Path(report.run_dir, "report.json").exists())


def test_run_eval_reports_regression_delta(tmp_path):
    task = EvalTask(
        id="create_file",
        prompt="create hello.txt",
        checks=[{"type": "file_equals", "path": "hello.txt", "text": "hello"}],
    )

    def good_factory(_task):
        return FakeProvider(
            [
                Response(
                    tool_calls=[
                        ToolCall(
                            "write_file",
                            {"path": "hello.txt", "content": "hello"},
                            id="c1",
                        )
                    ]
                ),
                Response(content="done"),
            ]
        )

    first = run_eval([task], good_factory, tmp_path, model="fake")
    second = run_eval([task], good_factory, tmp_path, model="fake")

    assert first.passed == 1
    assert second.passed == 1
    assert second.previous_passed == 1
    assert second.delta == 0


def test_run_eval_failure(tmp_path):
    task = EvalTask(
        id="create_file",
        prompt="create hello.txt",
        checks=[{"type": "file_equals", "path": "hello.txt", "text": "hello"}],
    )

    def bad_factory(_task):
        return FakeProvider(["I cannot do that"])

    report = run_eval([task], bad_factory, tmp_path, model="fake")
    assert report.passed == 0
    assert report.results[0].passed is False
    assert report.results[0].checks[0]["ok"] is False


def test_run_eval_rejects_empty_checks(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="check"):
        run_eval([EvalTask(id="empty", prompt="p")], lambda task: FakeProvider(["done"]), tmp_path)


def test_run_eval_rejects_non_done_even_when_checks_pass(tmp_path, monkeypatch):
    from app.agent import Agent, AgentResult

    async def stopped(self, task, **kwargs):
        return AgentResult("stopped", 1, "stopped", stop_reason="max_steps")

    monkeypatch.setattr(Agent, "run", stopped)
    task = EvalTask(id="stopped", prompt="p", files={"a.txt": "ok"},
                    checks=[{"type": "file_equals", "path": "a.txt", "text": "ok"}])
    report = run_eval([task], lambda task: FakeProvider([]), tmp_path, model="fake")
    assert report.passed == 0
    assert report.results[0].stop_reason == "max_steps"


def test_run_eval_rejects_changed_grading_files(tmp_path):
    task = EvalTask(
        id="cheat", prompt="fix calc without modifying tests",
        files={"calc.py": "VALUE = 0", "test_calc.py": "from calc import VALUE\nassert VALUE == 1\n"},
        checks=[{"type": "command", "command": "python3 test_calc.py"}],
    )
    provider = FakeProvider([Response(tool_calls=[ToolCall(
        "write_file", {"path": "test_calc.py", "content": "print('ok')"}, id="c1")]), "done"])
    report = run_eval([task], lambda task: provider, tmp_path, model="fake")
    assert report.passed == 0
    assert any(check["type"] == "protected_files" and not check["ok"]
               for check in report.results[0].checks)


def test_run_eval_supports_explicit_protected_files(tmp_path):
    task = EvalTask(id="protected", prompt="p", files={"oracle.txt": "old"},
                    protected_files=["oracle.txt"],
                    checks=[{"type": "file_exists", "path": "oracle.txt"}])
    provider = FakeProvider([Response(tool_calls=[ToolCall(
        "write_file", {"path": "oracle.txt", "content": "new"}, id="c1")]), "done"])
    report = run_eval([task], lambda task: provider, tmp_path, model="fake")
    assert report.passed == 0


def test_eval_task_paths_are_validated_before_writes(tmp_path):
    import pytest
    for task in [
        EvalTask(id="../escape", prompt="p", checks=[{"type": "file_exists", "path": "x"}]),
        EvalTask(id="safe", prompt="p", files={"../escape": "bad"},
                 checks=[{"type": "file_exists", "path": "x"}]),
        EvalTask(id="safe", prompt="p", checks=[{"type": "file_exists", "path": "/tmp/escape"}]),
    ]:
        with pytest.raises(ValueError):
            run_eval([task], lambda task: FakeProvider([]), tmp_path)
    assert not (tmp_path / ".vibe").exists()


def test_run_checks_rejects_symlink_outside_workspace(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "link.txt").symlink_to(outside)
    task = EvalTask(id="t", prompt="p", checks=[
        {"type": "file_equals", "path": "link.txt", "text": "secret"}])
    assert not run_checks(task, workspace)[0]["ok"]


def test_eval_delta_requires_same_tasks_and_protocol(tmp_path):
    from app.config import Config
    task = EvalTask(id="same", prompt="p", files={"x": "ok"},
                    checks=[{"type": "file_equals", "path": "x", "text": "ok"}])
    factory = lambda task: FakeProvider(["done"])
    first = run_eval([task], factory, tmp_path, model="fake", config=Config(max_run_tokens=10000))
    second = run_eval([task], factory, tmp_path, model="fake", config=Config(max_run_tokens=20000))
    third = run_eval([task], factory, tmp_path, model="other", config=Config(max_run_tokens=10000))
    assert first.previous_passed is None
    assert second.previous_passed is None
    assert third.previous_passed is None
    assert first.fingerprint != second.fingerprint
    assert first.metadata["agent"]["max_run_tokens"] == 10000


def test_eval_config_budget_trace_and_baseline_tools(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from app.config import Config
    from app.agent import Agent

    task = EvalTask(id="configured", prompt="p", files={"x": "ok"},
                    checks=[{"type": "file_equals", "path": "x", "text": "ok"}])
    seen = {}
    original = Agent.__init__

    def spy(self, *args, **kwargs):
        seen.update(kwargs)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", spy)
    provider = FakeProvider(["done"])
    report = run_eval([task], lambda task: provider, tmp_path, model="fake",
                      config=Config(max_run_seconds=17, max_run_tokens=12345, max_steps=3,
                                    llm_retries=0, keep_recent_groups=2))
    assert seen["max_run_seconds"] == 17
    assert seen["max_run_tokens"] == 12345
    assert seen["max_steps"] == 3
    assert seen["llm_retries"] == 0
    names = {spec["function"]["name"] for spec in provider.seen_tools[0]}
    assert not names.intersection({"web_search", "list_skills", "load_skill", "unload_skill"})
    artifacts = Path(report.results[0].workspace) / ".vibe"
    assert json.loads((artifacts / "messages.jsonl").read_text().splitlines()[0])["role"] == "user"
    assert (artifacts / "trace.jsonl").read_text()
    audit = [json.loads(line) for line in (artifacts / "audit.jsonl").read_text().splitlines()]
    assert any(event["tool"] == "write_file" for event in audit)
    assert report.metadata["isolation"] == "local_process_no_os_sandbox"


def test_command_checks_use_approval_gate(tmp_path):
    from app.policy import ApprovalDecision, ApprovalGate

    async def deny(call, preview):
        return ApprovalDecision(False, "deny")

    task = EvalTask(id="deny", prompt="p", checks=[
        {"type": "command", "command": "touch should-not-exist"}])
    checks = run_checks(task, tmp_path, approval=ApprovalGate(handler=deny))
    assert not checks[0]["ok"]
    assert not (tmp_path / "should-not-exist").exists()


def test_eval_fake_and_real_histories_are_separate(tmp_path):
    class OtherProvider:
        def __init__(self):
            self.fake = FakeProvider(["done"])

        def stream(self, messages, tools):
            return self.fake.stream(messages, tools)

    task = EvalTask(id="t", prompt="p", files={"x": "ok"},
                    checks=[{"type": "file_equals", "path": "x", "text": "ok"}])
    fake = run_eval([task], lambda task: FakeProvider(["done"]), tmp_path, model="same")
    real = run_eval([task], lambda task: OtherProvider(), tmp_path, model="same")
    assert real.previous_passed is None
    assert fake.metadata["mode"] == "fake"
    assert real.metadata["mode"] == "model"
    assert "/fake/" in fake.run_dir
    assert "/model/" in real.run_dir


def test_run_checks_fails_on_malformed_output_condition(tmp_path):
    task = EvalTask(id="bad", prompt="p", checks=[
        {"type": "command", "command": "echo ok", "contains": 1}])
    assert not run_checks(task, tmp_path)[0]["ok"]


def test_eval_refuses_dangerous_grading_command(tmp_path):
    task = EvalTask(id="danger", prompt="p", checks=[
        {"type": "command", "command": "sudo true"}])
    check = run_checks(task, tmp_path)[0]
    assert not check["ok"]
    assert "blocked dangerous command" in check["detail"]
